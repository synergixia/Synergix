/**
 * POST /api/judge
 *
 * Evaluate a candidate contribution with the bot's Judge model (preview
 * before sealing on Irys).  Returns the same JSON shape the bot produces:
 *   { quality_score, reason, category, impact_index, approved,
 *     constructive_feedback, content_summary }
 *
 * No writes happen here — this is a read-only quality check.
 *
 * Env (server-side):  SYNERGIX_UPSTREAM, SYNERGIX_UPSTREAM_KEY
 */

export const config = { runtime: 'edge' };

const json = (obj, status = 200) =>
  new Response(JSON.stringify(obj), {
    status,
    headers: {
      'Content-Type': 'application/json',
      'Cache-Control': 'no-store',
      'Access-Control-Allow-Origin': '*',
    },
  });

export default async function handler(req) {
  if (req.method === 'OPTIONS') {
    return new Response(null, {
      status: 204,
      headers: {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'POST, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type',
      },
    });
  }
  if (req.method !== 'POST') return json({ error: 'method not allowed' }, 405);

  const upstream = process.env.SYNERGIX_UPSTREAM || '';
  const key = process.env.SYNERGIX_UPSTREAM_KEY || '';
  if (!upstream || !key) {
    return json({ error: 'cloud upstream not configured', code: 'NO_UPSTREAM' }, 501);
  }

  let payload;
  try {
    payload = await req.json();
  } catch {
    return json({ error: 'invalid JSON' }, 400);
  }
  const text = String(payload?.text ?? '').slice(0, 5000).trim();
  if (text.length < 20) {
    return json({ error: 'too_short', minLength: 20 }, 400);
  }

  try {
    const ctrl = new AbortController();
    const to = setTimeout(() => ctrl.abort(), 120_000);
    const res = await fetch(`${upstream.replace(/\/$/, '')}/v1/judge`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-API-Key': key },
      body: JSON.stringify({ text }),
      signal: ctrl.signal,
    });
    clearTimeout(to);
    const data = await res.json();
    return json(data, res.ok ? 200 : 502);
  } catch (err) {
    return json({ error: `upstream unreachable: ${err?.message || 'error'}` }, 502);
  }
}
