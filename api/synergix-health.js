/**
 * GET /api/synergix-health
 *
 * Tells the browser whether CLOUD chat mode is available, i.e. whether
 * the bot's web API upstream is configured for this deployment.  The
 * browser uses this to pick its default chat mode (cloud vs local).
 *
 * Never leaks the upstream URL or key — only a boolean + best-effort
 * upstream liveness.
 *
 * Env (server-side only, set in Vercel project settings):
 *   SYNERGIX_UPSTREAM       base URL of the bot web API (Cloudflare tunnel)
 *   SYNERGIX_UPSTREAM_KEY   shared secret sent as X-API-Key
 */

export const config = { runtime: 'edge' };

export default async function handler() {
  const upstream = process.env.SYNERGIX_UPSTREAM || '';
  const key = process.env.SYNERGIX_UPSTREAM_KEY || '';
  const configured = Boolean(upstream && key);

  let upstreamHealthy = false;
  if (configured) {
    try {
      const ctrl = new AbortController();
      const to = setTimeout(() => ctrl.abort(), 4000);
      const res = await fetch(`${upstream.replace(/\/$/, '')}/health`, {
        headers: { 'X-API-Key': key },
        signal: ctrl.signal,
      });
      clearTimeout(to);
      upstreamHealthy = res.ok;
    } catch {
      upstreamHealthy = false;
    }
  }

  return new Response(
    JSON.stringify({
      upstreamConfigured: configured,
      upstreamHealthy,
      ts: Date.now(),
    }),
    {
      status: 200,
      headers: {
        'Content-Type': 'application/json',
        'Cache-Control': 'no-store',
        'Access-Control-Allow-Origin': '*',
      },
    },
  );
}
