export async function onRequestPost(context) {
    const { request, env } = context;
    
    try {
        const body = await request.json();

        const githubRes = await fetch(
            `https://api.github.com/repos/${body.githubId}/${body.repoName}/actions/workflows/capture.yml/dispatches`,
            {
                method: "POST",
                headers: {
                    "Accept": "application/vnd.github.v3+json",
                    "Authorization": `Bearer ${env.GITHUB_PAT}`,
                    "User-Agent": "Cloudflare-Pages"
                },
                body: JSON.stringify({
                    ref: "main",
                    inputs: {
                        start_date: body.start_date,
                        end_date: body.end_date
                    }
                })
            }
        );

        return new Response(JSON.stringify({ ok: githubRes.ok }), {
            status: githubRes.status,
            headers: { "Content-Type": "application/json" }
        });
    } catch (err) {
        return new Response(JSON.stringify({ ok: false, error: err.message }), { status: 500 });
    }
}
