/**
 * POST /api/contribute
 *
 * Seal an approved contribution on Irys.  The browser provides the text
 * plus a wallet signature over a SIWE challenge; the bot's web API
 * re-verifies the signature server-side, runs the Judge, and (if
 * approved) uploads the aporte to Irys tagged with the signer wallet.
 *
 * Request body:
 *   { text, address, challenge, signature, lang }
 * Response:
 *   { status:'success', txId, gatewayUrl, evaluation } | { status:'rejected', evaluation } | { error }
 *
 * The Irys PRIVATE_KEY lives only on the bot side — never here, never in
 * the browser.  This endpoint is a thin authenticated proxy.
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

  let p;
  try {
    p = await req.json();
  } catch {
    return json({ error: 'invalid JSON' }, 400);
  }

  const text = String(p?.text ?? '').slice(0, 5000).trim();
  const address = String(p?.address ?? '').trim();
  const challenge = String(p?.challenge ?? '');
  const signature = String(p?.signature ?? '');
  const lang = String(p?.lang ?? 'en').slice(0, 5);

  if (text.length < 20) return json({ error: 'too_short', minLength: 20 }, 400);
  if (!/^0x[0-9a-fA-F]{40}$/.test(address)) return json({ error: 'bad_address' }, 400);
  if (!challenge || !/^0x[0-9a-fA-F]{130}$/.test(signature)) {
    return json({ error: 'missing_signature' }, 400);
  }

  try {
    const ctrl = new AbortController();
    const to = setTimeout(() => ctrl.abort(), 150_000);
    const res = await fetch(`${upstream.replace(/\/$/, '')}/v1/contribute`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-API-Key': key },
      body: JSON.stringify({ text, address, challenge, signature, lang }),
      signal: ctrl.signal,
    });
    clearTimeout(to);
    const data = await res.json();
    return json(data, res.ok ? 200 : 502);
  } catch (err) {
    return json({ error: `upstream unreachable: ${err?.message || 'error'}` }, 502);
  }
}
