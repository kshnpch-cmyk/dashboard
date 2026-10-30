import postgres from 'postgres';

// ⚡ DB 커넥션 풀 싱글톤 유지
let sqlInstance = null;

function getDb(env) {
    if (!sqlInstance) {
        const dbUrl = env.TIDB_DATABASE_URL || "postgresql://1yr8qr4zyjgi7030.postgres:LSj_KcNBk0k589WJ5pawg_NIsZ_AKTxS@1yr8qr4zyjgi7030.gateway01.us-east-1.prod.aws.pg.tidbcloud.com:5432/postgres?sslmode=require";
        sqlInstance = postgres(dbUrl, {
            ssl: 'require',
            max: 10,
            idle_timeout: 30,
            connect_timeout: 10,
            types: {
                1082: {
                    to: 1082,
                    from: [1082],
                    serialize: (x) => x,
                    parse: (x) => x
                }
            }
        });
    }
    return sqlInstance;
}

const corsHeaders = {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type, Authorization, apikey, Prefer, Range',
    'Content-Type': 'application/json'
};

// ⚡ 1. [서버 사이드 Push-down 집계] TiDB 엔진 내부에서 합산 계산 후 전송
async function handleSummaryApi(request, env) {
    const url = new URL(request.url);
    const startDate = url.searchParams.get('startDate');
    const endDate = url.searchParams.get('endDate');
    const brandName = url.searchParams.get('brandName');
    const itemCode = url.searchParams.get('itemCode');

    if (!startDate || !endDate) {
        return new Response(JSON.stringify({ error: "startDate and endDate are required" }), { status: 400, headers: corsHeaders });
    }

    let whereClauses = ["delivery_date >= $1::date", "delivery_date <= $2::date"];
    let params = [startDate.replace(/\//g, '-'), endDate.replace(/\//g, '-')];
    let pIdx = 3;

    if (brandName) {
        whereClauses.push(`brand_name ILIKE $${pIdx++}`);
        params.push(`%${brandName.trim()}%`);
    }

    if (itemCode) {
        whereClauses.push(`item_code = $${pIdx++}`);
        params.push(itemCode.trim());
    }

    // TiDB TiFlash 컬럼 엔진을 직접 타는 초고속 집계 SQL (수만 건 행 -> 30건으로 축소)
    const sqlQuery = `
        SELECT 
            to_char(delivery_date, 'YYYY/MM/DD') AS delivery_date,
            SUM(qty)::bigint AS total_qty,
            SUM(total_amount)::numeric(15, 2) AS total_amount,
            COUNT(DISTINCT store_name)::int AS total_stores,
            COUNT(DISTINCT item_code)::int AS total_items
        FROM public.oms_orders
        WHERE ${whereClauses.join(' AND ')}
        GROUP BY delivery_date
        ORDER BY delivery_date ASC;
    `;

    try {
        const sql = getDb(env);
        const rows = await sql.unsafe(sqlQuery, params);
        return new Response(JSON.stringify(rows), {
            headers: {
                ...corsHeaders,
                'Cache-Control': 'public, max-age=60, s-maxage=300'
            }
        });
    } catch (e) {
        return new Response(JSON.stringify({ error: e.message, sql: sqlQuery }), { status: 500, headers: corsHeaders });
    }
}

// ⚡ 2. [캘린더 전용 초고속 집계] 월 전체 날짜별 주문 건수만 즉시 계산
async function handleCalendarApi(request, env) {
    const url = new URL(request.url);
    const month = url.searchParams.get('month'); // 예: 2026-09
    if (!month) {
        return new Response(JSON.stringify({ error: "month parameter required (YYYY-MM)" }), { status: 400, headers: corsHeaders });
    }

    const startDate = `${month}-01`;
    const sqlQuery = `
        SELECT 
            to_char(delivery_date, 'YYYY-MM-DD') AS delivery_date,
            SUM(qty)::bigint AS total_qty
        FROM public.oms_orders
        WHERE delivery_date >= $1::date AND delivery_date < ($1::date + INTERVAL '1 month')
        GROUP BY delivery_date
        ORDER BY delivery_date ASC;
    `;

    try {
        const sql = getDb(env);
        const rows = await sql.unsafe(sqlQuery, [startDate]);
        return new Response(JSON.stringify(rows), {
            headers: {
                ...corsHeaders,
                'Cache-Control': 'public, max-age=120, s-maxage=600'
            }
        });
    } catch (e) {
        return new Response(JSON.stringify({ error: e.message }), { status: 500, headers: corsHeaders });
    }
}

// 기존 PostgREST 에뮬레이션 엔드포인트
async function handleRestApi(request, env, ctx) {
    const url = new URL(request.url);
    if (request.method === 'OPTIONS') return new Response(null, { headers: corsHeaders });

    const cache = caches.default;
    const cacheKey = new Request(url.toString(), request);
    let cachedResponse = await cache.match(cacheKey);
    if (cachedResponse) return cachedResponse;

    const endpoint = url.pathname.replace(/^\/rest\/v1\/?/, '').split('?')[0];
    if (endpoint.startsWith('rpc/')) {
        return new Response(JSON.stringify({ ok: true, message: "TiDB view is live." }), { headers: corsHeaders });
    }

    let whereClauses = [];
    let queryParams = [];
    let pIdx = 1;

    const selectCols = url.searchParams.get('select') || '*';
    const cols = selectCols !== '*' 
        ? selectCols.split(',').map(c => c.trim()).filter(Boolean).map(c => `"${c}"`).join(', ')
        : '*';

    for (const [key, val] of url.searchParams.entries()) {
        if (['select', 'limit', 'offset', 'order'].includes(key)) continue;

        if (key === 'or') {
            let inner = val.trim();
            if (inner.startsWith('(') && inner.endsWith(')')) inner = inner.slice(1, -1);
            let subConds = [];
            for (let item of inner.split(',')) {
                item = item.trim();
                if (item.includes('.eq.')) {
                    let [col, v] = item.split('.eq.');
                    subConds.push(`"${col}" = $${pIdx++}`);
                    queryParams.push(decodeURIComponent(v));
                } else if (item.includes('.ilike.')) {
                    let [col, v] = item.split('.ilike.');
                    let clean = decodeURIComponent(v).replace(/^\*|\*$/g, '');
                    subConds.push(`"${col}" ILIKE $${pIdx++}`);
                    queryParams.push(`%${clean}%`);
                }
            }
            if (subConds.length > 0) whereClauses.push(`(${subConds.join(' OR ')})`);
        } else {
            const col = `"${key}"`;
            if (val.startsWith('gte.')) {
                whereClauses.push(`${col} >= $${pIdx++}`);
                queryParams.push(val.slice(4));
            } else if (val.startsWith('lte.')) {
                whereClauses.push(`${col} <= $${pIdx++}`);
                queryParams.push(val.slice(4));
            } else if (val.startsWith('eq.')) {
                whereClauses.push(`${col} = $${pIdx++}`);
                queryParams.push(decodeURIComponent(val.slice(3)));
            } else if (val.startsWith('ilike.')) {
                let clean = decodeURIComponent(val.slice(6)).replace(/^\*|\*$/g, '');
                whereClauses.push(`${col} ILIKE $${pIdx++}`);
                queryParams.push(`%${clean}%`);
            }
        }
    }

    let sqlQuery = `SELECT ${cols} FROM public."${endpoint}"`;
    if (whereClauses.length > 0) sqlQuery += ` WHERE ${whereClauses.join(' AND ')}`;

    const order = url.searchParams.get('order');
    if (order) {
        let orderParts = order.split(',').map(o => {
            let [c, dir] = o.trim().split('.');
            return `"${c}" ${(dir || 'ASC').toUpperCase()}`;
        });
        sqlQuery += ` ORDER BY ${orderParts.join(', ')}`;
    }

    const limit = url.searchParams.get('limit');
    if (limit) sqlQuery += ` LIMIT ${parseInt(limit, 10)}`;

    const offset = url.searchParams.get('offset');
    if (offset) sqlQuery += ` OFFSET ${parseInt(offset, 10)}`;

    const sql = getDb(env);
    try {
        const rows = await sql.unsafe(sqlQuery, queryParams);
        const responseHeaders = {
            ...corsHeaders,
            'Cache-Control': 'public, max-age=60, s-maxage=300'
        };
        const response = new Response(JSON.stringify(rows), { headers: responseHeaders });
        ctx.waitUntil(cache.put(cacheKey, response.clone()));
        return response;
    } catch (err) {
        return new Response(JSON.stringify({ error: err.message, sql: sqlQuery }), { status: 500, headers: corsHeaders });
    }
}

export default {
    async fetch(request, env, ctx) {
        const url = new URL(request.url);

        // ⚡ [TiFlash & 커버링 인덱스 원클릭 구축]
        if (url.pathname === '/init-index') {
            const sql = getDb(env);
            const logs = [];

            const ddlTasks = [
                { name: "TiFlash 복제본 활성화", sql: `ALTER TABLE public.oms_orders SET TIFLASH REPLICA 1;` },
                { name: "커버링 집계 인덱스", sql: `CREATE INDEX IF NOT EXISTS idx_oms_orders_covering ON public.oms_orders (delivery_date, brand_name, store_name, qty, total_amount);` },
                { name: "기본 배송일 인덱스", sql: `CREATE INDEX IF NOT EXISTS idx_oms_orders_delivery_date ON public.oms_orders (delivery_date);` }
            ];

            for (const task of ddlTasks) {
                try {
                    await sql.unsafe(task.sql);
                    logs.push(`✅ ${task.name}: 성공`);
                } catch (e) {
                    logs.push(`ℹ️ ${task.name}: ${e.message}`);
                }
            }

            return new Response(JSON.stringify({ ok: true, details: logs }, null, 2), { headers: corsHeaders });
        }

        // ⚡ 서버 사이드 푸시다운 API 라우팅
        if (url.pathname === '/api/summary') {
            return handleSummaryApi(request, env);
        }
        if (url.pathname === '/api/calendar') {
            return handleCalendarApi(request, env);
        }

        // 기존 REST API
        if (url.pathname.startsWith('/rest/v1/')) {
            return handleRestApi(request, env, ctx);
        }

        // 정적 자산 배포
        if (env.ASSETS) {
            return env.ASSETS.fetch(request);
        }

        return new Response('Not Found', { status: 404 });
    }
};
