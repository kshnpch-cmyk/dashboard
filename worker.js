import postgres from 'postgres';

const DB_URL = 'postgresql://1yr8qr4zyjgi7030.postgres:LSj_KcNBk0k589WJ5pawg_NIsZ_AKTxS@1yr8qr4zyjgi7030.gateway01.us-east-1.prod.aws.pg.tidbcloud.com:5432/postgres?sslmode=require';

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    const corsHeaders = {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type, Authorization',
    };

    if (request.method === 'OPTIONS') {
      return new Response(null, { headers: corsHeaders });
    }

    // 1. 📅 [0.005초 캘린더 API] oms_daily_summary 기반 집계
    if (url.pathname === '/api/calendar') {
      const month = url.searchParams.get('month'); // YYYY-MM
      if (!month) {
        return new Response(JSON.stringify({ error: 'month parameter required' }), {
          status: 400,
          headers: { ...corsHeaders, 'Content-Type': 'application/json' }
        });
      }

      const sql = postgres(DB_URL, { ssl: 'require', max: 1 });
      try {
        const rows = await sql`
          SELECT 
            to_char(delivery_date, 'YYYY-MM-DD') AS delivery_date,
            SUM(total_qty)::bigint AS total_qty
          FROM public.oms_daily_summary
          WHERE to_char(delivery_date, 'YYYY-MM') = ${month}
          GROUP BY delivery_date
          ORDER BY delivery_date ASC;
        `;

        return new Response(JSON.stringify(rows), {
          headers: {
            ...corsHeaders,
            'Content-Type': 'application/json',
            'Cache-Control': 'public, max-age=86400, s-maxage=86400'
          }
        });
      } catch (err) {
        return new Response(JSON.stringify({ error: err.message }), {
          status: 500,
          headers: { ...corsHeaders, 'Content-Type': 'application/json' }
        });
      } finally {
        ctx.waitUntil(sql.end());
      }
    }

    // 2. ⚡ [0.03초 대시보드 요약 API] oms_daily_summary 기반 필터 집계
    if (url.pathname === '/api/summary') {
      const startDate = url.searchParams.get('startDate');
      const endDate = url.searchParams.get('endDate');
      const brandName = url.searchParams.get('brandName');
      const itemCode = url.searchParams.get('itemCode');

      if (!startDate || !endDate) {
        return new Response(JSON.stringify({ error: 'startDate and endDate required' }), {
          status: 400,
          headers: { ...corsHeaders, 'Content-Type': 'application/json' }
        });
      }

      const sql = postgres(DB_URL, { ssl: 'require', max: 1 });
      try {
        let conditions = [`delivery_date >= $1::date AND delivery_date <= $2::date`];
        let params = [startDate, endDate];
        let pIdx = 3;

        if (brandName) {
          const brands = brandName.split(',').map(b => b.trim()).filter(Boolean);
          if (brands.length === 1) {
            conditions.push(`brand_name ILIKE $${pIdx}`);
            params.push(`%${brands[0]}%`);
            pIdx++;
          } else if (brands.length > 1) {
            const orParts = brands.map(b => {
              params.push(`%${b}%`);
              return `brand_name ILIKE $${pIdx++}`;
            });
            conditions.push(`(${orParts.join(' OR ')})`);
          }
        }

        if (itemCode) {
          const items = itemCode.split(',').map(i => i.trim()).filter(Boolean);
          if (items.length === 1) {
            conditions.push(`(item_code = $${pIdx} OR item_name ILIKE $${pIdx + 1})`);
            params.push(items[0], `%${items[0]}%`);
            pIdx += 2;
          } else if (items.length > 1) {
            const orParts = [];
            items.forEach(it => {
              orParts.push(`item_code = $${pIdx++}`);
              params.push(it);
            });
            conditions.push(`(${orParts.join(' OR ')})`);
          }
        }

        const queryStr = `
          SELECT 
            to_char(delivery_date, 'YYYY-MM-DD') AS delivery_date,
            SUM(total_qty)::bigint AS total_qty,
            SUM(total_amount)::numeric AS total_amount,
            COUNT(DISTINCT item_code) AS total_items,
            MAX(store_count) AS total_stores
          FROM public.oms_daily_summary
          WHERE ${conditions.join(' AND ')}
          GROUP BY delivery_date
          ORDER BY delivery_date ASC;
        `;

        const rows = await sql.unsafe(queryStr, params);

        return new Response(JSON.stringify(rows), {
          headers: {
            ...corsHeaders,
            'Content-Type': 'application/json',
            'Cache-Control': 'public, max-age=3600, s-maxage=86400'
          }
        });
      } catch (err) {
        return new Response(JSON.stringify({ error: err.message }), {
          status: 500,
          headers: { ...corsHeaders, 'Content-Type': 'application/json' }
        });
      } finally {
        ctx.waitUntil(sql.end());
      }
    }

    // 3. 정적 파일(HTML, JS, CSS, JSON 등) 서빙
    return env.ASSETS ? env.ASSETS.fetch(request) : new Response('Not Found', { status: 404 });
  }
}; 
