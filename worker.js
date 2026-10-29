import postgres from 'postgres';

const corsHeaders = {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type, Authorization, apikey, Prefer, Range',
    'Content-Type': 'application/json'
};

function getDb(env) {
    const dbUrl = env.TIDB_DATABASE_URL || "postgresql://1yr8qr4zyjgi7030.postgres:LSj_KcNBk0k589WJ5pawg_NIsZ_AKTxS@1yr8qr4zyjgi7030.gateway01.us-east-1.prod.aws.pg.tidbcloud.com:5432/postgres?sslmode=require";
    return postgres(dbUrl, {
        ssl: 'require',
        max: 5,
        idle_timeout: 20,
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

async function handleRestApi(request, env, ctx) {
    const url = new URL(request.url);

    if (request.method === 'OPTIONS') {
        return new Response(null, { headers: corsHeaders });
    }

    // ⚡ Cloudflare Edge 캐시 확인 (미국 왕복 없이 즉시 응답)
    const cache = caches.default;
    const cacheKey = new Request(url.toString(), request);
    let cachedResponse = await cache.match(cacheKey);
    if (cachedResponse) {
        return cachedResponse;
    }

    const endpoint = url.pathname.replace(/^\/rest\/v1\/?/, '').split('?')[0];

    if (endpoint.startsWith('rpc/')) {
        return new Response(JSON.stringify({ ok: true, message: "TiDB view is live." }), { headers: corsHeaders });
    }

    const validTables = ['daily_order_summary', 'store_order_summary', 'oms_orders'];
    if (!validTables.includes(endpoint)) {
        return new Response(JSON.stringify({ error: `Not Found or Invalid Table: ${endpoint}` }), { status: 404, headers: corsHeaders });
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
            if (subConds.length > 0) {
                whereClauses.push(`(${subConds.join(' OR ')})`);
            }
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
            } else if (val.startsWith('in.(') && val.endsWith(')')) {
                let inVals = val.slice(4, -1).split(',').map(x => decodeURIComponent(x.trim())).filter(Boolean);
                if (inVals.length > 0) {
                    let marks = inVals.map(() => `$${pIdx++}`).join(', ');
                    whereClauses.push(`${col} IN (${marks})`);
                    queryParams.push(...inVals);
                }
            }
        }
    }

    let sqlQuery = `SELECT ${cols} FROM public."${endpoint}"`;
    if (whereClauses.length > 0) {
        sqlQuery += ` WHERE ${whereClauses.join(' AND ')}`;
    }

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
            'Cache-Control': 'public, max-age=60, s-maxage=180'
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

        // ⚡ [인덱스 생성 엔드포인트] 브라우저에서 /init-index 접속 시 작동
        if (url.pathname === '/init-index') {
            const sql = getDb(env);
            try {
                await sql.unsafe(`
                    CREATE INDEX IF NOT EXISTS idx_oms_orders_delivery_date ON public.oms_orders (delivery_date);
                    CREATE INDEX IF NOT EXISTS idx_oms_orders_brand_delivery ON public.oms_orders (brand_name, delivery_date);
                    CREATE INDEX IF NOT EXISTS idx_oms_orders_store_delivery ON public.oms_orders (store_name, delivery_date);
                `);
                return new Response(JSON.stringify({ ok: true, message: "TiDB 인덱스 생성이 성공적으로 완료되었습니다!" }), { headers: corsHeaders });
            } catch (err) {
                return new Response(JSON.stringify({ ok: false, error: err.message }), { status: 500, headers: corsHeaders });
            }
        }

        // 1. /rest/v1/ 요청 라우팅
        if (url.pathname.startsWith('/rest/v1/')) {
            return handleRestApi(request, env, ctx);
        }

        // 2. 정적 자산(HTML, JS 등) 반환
        if (env.ASSETS) {
            return env.ASSETS.fetch(request);
        }

        return new Response('Not Found', { status: 404 });
    }
};
