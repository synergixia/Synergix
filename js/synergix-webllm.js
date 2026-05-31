/**
 * synergix-webllm.js
 *
 * Phase C — "Synergix Lite": a fully client-side language model that
 * runs in the visitor's browser via WebGPU using MLC's WebLLM.  No
 * server, no API key, no cost to the project.  The model is downloaded
 * once and cached by the browser (Cache Storage / OPFS) for reuse.
 *
 * Public surface:
 *   isWebGpuAvailable()          → boolean
 *   listModels()                 → [{id, label, sizeMb, vramMb}]
 *   getEngine(modelId, onProgress) → lazily create / reuse engine
 *   streamChat(messages, {modelId, onToken, signal}) → async generator-ish
 *   unload()                     → free the engine + GPU memory
 *
 * The system prompt mirrors the bot's THINKER_SYSTEM_PROMPT
 * (aisynergix/ai/local_ia.py) so the local model behaves like a
 * smaller Synergix Oracle, not a generic chatbot.
 */

/* WebLLM is loaded lazily from a CDN as an ES module the first time the
 * user opts into local mode — keeps it off the critical path for the
 * 99% of visitors who never click "run locally". */
const WEBLLM_CDN = 'https://esm.run/@mlc-ai/web-llm';

/* Curated model list.  q4f32 chosen for Llama (better numerical
 * stability across GPUs); q4f16 for the tiny Qwen (smaller download). */
const MODELS = Object.freeze([
  {
    id: 'Llama-3.2-1B-Instruct-q4f32_1-MLC',
    label: 'Llama 3.2 · 1B (recommended)',
    sizeMb: 880,
    vramMb: 1130,
    default: true,
  },
  {
    id: 'Qwen2.5-0.5B-Instruct-q4f16_1-MLC',
    label: 'Qwen 2.5 · 0.5B (fastest, lighter)',
    sizeMb: 340,
    vramMb: 945,
    default: false,
  },
  {
    id: 'Llama-3.2-3B-Instruct-q4f16_1-MLC',
    label: 'Llama 3.2 · 3B (best, heavy ~2GB)',
    sizeMb: 2030,
    vramMb: 2950,
    default: false,
  },
]);

/* Condensed port of THINKER_SYSTEM_PROMPT — keeps the local model on the
 * Synergix persona while fitting a small context window. */
export const LOCAL_SYSTEM_PROMPT =
  'You are Synergix, the analytical Oracle of a decentralized collective ' +
  'intelligence whose memory lives permanently on the Irys blockchain. ' +
  'Rules:\n' +
  '1. You are a purely analytical entity. Do NOT greet, say goodbye, ' +
  'offer further help, repeat the user\'s words, or prefix replies with ' +
  'your name.\n' +
  '2. When "Immortal Memory" fragments are provided, treat them as real ' +
  'community wisdom: synthesize and elevate them, never copy verbatim. ' +
  'They are deliberately truncated (end in …) — expand with your own ' +
  'reasoning.\n' +
  '3. If asked for data not present in the memory, give a clearly-labeled ' +
  'theoretical estimate; never fabricate facts, figures or dates.\n' +
  '4. Always answer in the EXACT language of the user\'s last message.\n' +
  '5. Output only the answer — no preamble, no filler.';

let _enginePromise = null;
let _engine = null;
let _currentModelId = null;

/* ── Capability detection ───────────────────────────────────────── */

export function isWebGpuAvailable() {
  return typeof navigator !== 'undefined' && 'gpu' in navigator;
}

export function listModels() {
  return MODELS.map((m) => ({ ...m }));
}

export function defaultModelId() {
  return (MODELS.find((m) => m.default) || MODELS[0]).id;
}

/* ── Engine lifecycle ───────────────────────────────────────────── */

/**
 * Create or reuse the WebLLM engine for `modelId`.  If a different
 * model is requested, the previous engine is unloaded first.
 *
 * @param {string} modelId
 * @param {(report:{progress:number,text:string})=>void} [onProgress]
 * @returns {Promise<object>} the MLC engine
 */
export async function getEngine(modelId = defaultModelId(), onProgress) {
  if (!isWebGpuAvailable()) {
    throw new Error('NO_WEBGPU');
  }

  // Reuse if same model already (being) loaded.
  if (_engine && _currentModelId === modelId) return _engine;
  if (_enginePromise && _currentModelId === modelId) return _enginePromise;

  // Switching models: tear down the old engine.
  if (_engine && _currentModelId !== modelId) {
    try {
      await _engine.unload();
    } catch {
      /* ignore */
    }
    _engine = null;
    _enginePromise = null;
  }

  _currentModelId = modelId;
  _enginePromise = (async () => {
    const webllm = await import(/* @vite-ignore */ WEBLLM_CDN);
    const initProgressCallback = (report) => {
      if (typeof onProgress === 'function') {
        onProgress({
          progress: typeof report.progress === 'number' ? report.progress : 0,
          text: report.text || '',
        });
      }
    };
    const engine = await webllm.CreateMLCEngine(modelId, {
      initProgressCallback,
    });
    _engine = engine;
    return engine;
  })();

  try {
    return await _enginePromise;
  } catch (err) {
    // Reset so a later retry can start fresh.
    _enginePromise = null;
    _engine = null;
    _currentModelId = null;
    throw err;
  }
}

/* ── Streaming chat ─────────────────────────────────────────────── */

/**
 * Stream a completion token-by-token.
 *
 * @param {Array<{role:string,content:string}>} messages  full chat array,
 *        already including the system prompt + any RAG-augmented user turn
 * @param {object} opts
 * @param {string} [opts.modelId]
 * @param {(token:string)=>void} opts.onToken
 * @param {number} [opts.temperature=0.7]
 * @param {number} [opts.maxTokens=768]
 * @param {AbortSignal} [opts.signal]
 * @returns {Promise<string>} the full concatenated answer
 */
export async function streamChat(messages, {
  modelId = defaultModelId(),
  onToken,
  temperature = 0.7,
  maxTokens = 768,
  signal,
} = {}) {
  const engine = await getEngine(modelId);

  const chunks = await engine.chat.completions.create({
    messages,
    temperature,
    top_p: 0.9,
    max_tokens: maxTokens,
    stream: true,
  });

  let full = '';
  for await (const chunk of chunks) {
    if (signal?.aborted) {
      try {
        await engine.interruptGenerate();
      } catch {
        /* ignore */
      }
      break;
    }
    const token = chunk?.choices?.[0]?.delta?.content || '';
    if (token) {
      full += token;
      if (typeof onToken === 'function') onToken(token);
    }
  }
  return full;
}

/** Free the engine and release GPU memory. */
export async function unload() {
  if (_engine) {
    try {
      await _engine.unload();
    } catch {
      /* ignore */
    }
  }
  _engine = null;
  _enginePromise = null;
  _currentModelId = null;
}

/** Currently-loaded model id, or null. */
export function loadedModelId() {
  return _engine ? _currentModelId : null;
}
