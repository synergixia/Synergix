/**
 * POST /api/chat
 *
 * Proxies a chat turn to the bot's web API (real Thinker + server-side
 * RAG) and streams the SSE response straight back to the browser.  The
 * upstream API key is injected server-side and never reaches the client.
 *
 * Request body:  { message: string, history: [{role,content}], lang: string }
 * Response:      text/event-stream of `data: {"kind":"answer"|"memory_count"|"error","text":...}`
 *                terminated by `data: [DONE]`
 *
 * Env (server-side):  SYNERGIX_UPSTREAM, SYNERGIX_UPSTREAM_KEY
 */

export const config = { runtime: 'edge' };

const sse = (obj) => `data: ${JSON.stringify(obj)}\n\n`;

function errorStream(message, status = 200) {
  const body = sse({ kind: 'error', text: message }) + 'data: [DONE]\n\n';
  return new Response(body, {
    status,
    headers: {
      'Content-Type': 'text/event-stream; charset=utf-8',
      'Cache-Control': 'no-store',
      'Access-Control-Allow-Origin': '*',
    },
  });
}

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
  if (req.method !== 'POST') {
    return errorStream('method not allowed', 405);
  }

  const upstream = process.env.SYNERGIX_UPSTREAM || '';
  const key = process.env.SYNERGIX_UPSTREAM_KEY || '';
  if (!upstream || !key) {
    // Tell the browser to fall back to LOCAL (WebLLM) mode.
    return errorStream('cloud upstream not configured — use local mode');
  }

  let payload;
  try {
    payload = await req.json();
  } catch {
    return errorStream('invalid JSON body', 400);
  }

  const message = String(payload?.message ?? '').slice(0, 4000).trim();
  if (!message) return errorStream('empty message', 400);

  const body = JSON.stringify({
    message,
    history: Array.isArray(payload?.history) ? payload.history.slice(-10) : [],
    lang: String(payload?.lang ?? 'en').slice(0, 5),
  });

  let upstreamRes;
  try {
    const ctrl = new AbortController();
    const to = setTimeout(() => ctrl.abort(), 180_000);
    upstreamRes = await fetch(`${upstream.replace(/\/$/, '')}/v1/chat`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-API-Key': key,
        Accept: 'text/event-stream',
      },
      body,
      signal: ctrl.signal,
    });
    // Note: don't clear the timeout here — it guards the whole stream.
  } catch (err) {
    return errorStream(`upstream unreachable: ${err?.message || 'error'}`);
  }

  if (!upstreamRes.ok || !upstreamRes.body) {
    return errorStream(`upstream HTTP ${upstreamRes.status}`);
  }

  // Pipe the upstream SSE straight through.
  return new Response(upstreamRes.body, {
    status: 200,
    headers: {
      'Content-Type': 'text/event-stream; charset=utf-8',
      'Cache-Control': 'no-store',
      Connection: 'keep-alive',
      'Access-Control-Allow-Origin': '*',
    },
  });
}
