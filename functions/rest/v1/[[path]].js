import pg from 'pg';
const { Client } = pg;

// TiDB 날짜/숫자 타입을 Supabase REST 규격과 100% 동일하게 직렬화
pg.types.setTypeParser(1082, (str) => str); // DATE -> "YYYY-MM-DD"
pg.types.setTypeParser(1700, (str) => parseFloat(str)); // NUMERIC -> number
pg.types.setTypeParser(20, (str) => parseInt(str, 10)); // BIGINT -> number

export async function onRequest(context) {
    const { request, env, params } = context;
    const url = new URL(request.url);

    // CORS 기본 헤더
    const corsHeaders = {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type, Authorization, apikey, Prefer, Range',
        'Content-Type': 'application/json'
    };

    if (request.method === 'OPTIONS') {
        return new Response(null, { headers: corsHeaders });
    }

    const pathArr = Array.isArray(params.path) ? params.path : [params.path || ''];
    const endpoint = pathArr.join('/');

    // 1. Supabase 구체화 뷰 갱신 RPC 호출 처리 (TiDB는 실시간 일반 뷰이므로 즉시 성공 응답)
    if (endpoint.startsWith('rpc/')) {
        return new Response(JSON.stringify({ ok: true, message: "TiDB view is live." }), { headers: corsHeaders });
    }

    // 허용 테이블 및 뷰 화이트리스트
    const validTables = ['daily_order_summary', 'store_order_summary', 'oms_orders'];
    if (!validTables.includes(endpoint)) {
        return new Response(JSON.stringify({ error: `Not Found or Invalid Table: ${endpoint}` }), { status: 404, headers: corsHeaders });
    }

    // 2. PostgREST 쿼리 스트링 -> PostgreSQL SQL 변환
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

    let sql = `SELECT ${cols} FROM public."${endpoint}"`;
    if (whereClauses.length > 0) {
        sql += ` WHERE ${whereClauses.join(' AND ')}`;
    }

    const order = url.searchParams.get('order');
    if (order) {
        let orderParts = order.split(',').map(o => {
            let [c, dir] = o.trim().split('.');
            return `"${c}" ${(dir || 'ASC').toUpperCase()}`;
        });
        sql += ` ORDER BY ${orderParts.join(', ')}`;
    }

    const limit = url.searchParams.get('limit');
    if (limit) sql += ` LIMIT ${parseInt(limit, 10)}`;

    const offset = url.searchParams.get('offset');
    if (offset) sql += ` OFFSET ${parseInt(offset, 10)}`;

    // 3. TiDB Cloud DB 접속 및 쿼리 실행
    const dbUrl = env.TIDB_DATABASE_URL || "postgresql://1yr8qr4zyjgi7030.postgres:LSj_KcNBk0k589WJ5pawg_NIsZ_AKTxS@1yr8qr4zyjgi7030.gateway01.us-east-1.prod.aws.pg.tidbcloud.com:5432/postgres?sslmode=require";
    
    const client = new Client({
        connectionString: dbUrl,
        ssl: { rejectUnauthorized: false }
    });

    try {
        await client.connect();
        const result = await client.query(sql, queryParams);
        return new Response(JSON.stringify(result.rows), { headers: corsHeaders });
    } catch (err) {
        return new Response(JSON.stringify({ error: err.message, sql }), { status: 500, headers: corsHeaders });
    } finally {
        try { await client.end(); } catch (e) {}
    }
}
