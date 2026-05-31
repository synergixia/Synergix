/**
 * synergix-rag-browser.js
 *
 * Phase C — client-side Retrieval-Augmented Generation.  Mirrors the
 * semantics of the bot's aisynergix/services/rag_engine.py, but runs
 * entirely in the browser:
 *
 *   1. Pull recent aporte `content-summary` tags from Irys GraphQL
 *      (no extra gateway fetches — the summary already lives in the tag).
 *   2. Embed them with the SAME model the bot uses,
 *      `paraphrase-multilingual-MiniLM-L12-v2`, via transformers.js.
 *   3. Embed the user query, cosine-rank, apply the same relevance
 *      window (MIN 0.45 … NEAR-CLONE 0.92), build a context block with
 *      per-fragment truncation and cross-lingual [lang] markers.
 *
 * The embedding model is ~110 MB and is cached by the browser after the
 * first load.  Corpus embeddings are cached in memory for the session.
 *
 * Public surface:
 *   warmup(onProgress)             → preload embedder + corpus
 *   buildContext(query, lang)      → { context, results }
 *   refreshCorpus()                → re-pull aportes from Irys
 */

import { listAportes } from './synergix-irys.js';

/* Mirror of rag_engine.py constants. */
const EMBED_MODEL = 'Xenova/paraphrase-multilingual-MiniLM-L12-v2';
const MIN_RELEVANCE_SCORE = 0.45;
const NEAR_CLONE_THRESHOLD = 0.92;
const SNIPPET_MAX_CHARS = 240;
const MAX_CONTEXT_CHARS = 2000;
const TOP_K = 7;
const CORPUS_LIMIT = 200; // recent aportes to consider

const TRANSFORMERS_CDN =
  'https://cdn.jsdelivr.net/npm/@huggingface/transformers@3.0.2';

let _extractorPromise = null;
let _corpus = null; // [{ text, language, score:0, embedding:Float32Array }]
let _corpusPromise = null;

/* ── Embedder (transformers.js feature-extraction) ──────────────── */

async function _getExtractor(onProgress) {
  if (_extractorPromise) return _extractorPromise;
  _extractorPromise = (async () => {
    const { pipeline, env } = await import(/* @vite-ignore */ TRANSFORMERS_CDN);
    // Use the hosted WASM/ONNX weights; allow remote model download.
    env.allowRemoteModels = true;
    const extractor = await pipeline('feature-extraction', EMBED_MODEL, {
      progress_callback: (p) => {
        if (typeof onProgress === 'function' && p?.status === 'progress') {
          onProgress({
            progress: (p.progress || 0) / 100,
            text: `embedding model · ${p.file || ''}`,
          });
        }
      },
    });
    return extractor;
  })();
  return _extractorPromise;
}

/**
 * Embed a list of strings → array of normalized Float32Array vectors.
 * Mean-pooled + L2-normalized to match SentenceTransformer defaults.
 */
async function _embed(texts, onProgress) {
  const extractor = await _getExtractor(onProgress);
  const output = await extractor(texts, { pooling: 'mean', normalize: true });
  // transformers.js Tensor → nested arrays
  const dims = output.dims; // [n, d]
  const data = output.data; // flat Float32Array
  const [n, d] = dims;
  const vectors = [];
  for (let i = 0; i < n; i++) {
    vectors.push(data.slice(i * d, (i + 1) * d));
  }
  return vectors;
}

function _cosine(a, b) {
  // Both are already L2-normalized → cosine == dot product.
  let dot = 0;
  for (let i = 0; i < a.length; i++) dot += a[i] * b[i];
  return dot;
}

/* ── Corpus management ──────────────────────────────────────────── */

/**
 * Pull recent aportes from Irys and embed their content-summaries.
 * Cached for the session; call refreshCorpus() to rebuild.
 */
async function _buildCorpus(onProgress) {
  const aportes = await listAportes({ limit: CORPUS_LIMIT });
  // Keep only those with a usable summary.
  const items = aportes
    .map((a) => ({
      text: (a.contentSummary || '').trim(),
      language: a.language || 'es',
      category: a.category || 'filosofia',
      qualityScore: a.qualityScore || 0,
      txId: a.txId,
    }))
    .filter((x) => x.text.length >= 10);

  if (!items.length) {
    _corpus = [];
    return _corpus;
  }

  const embeddings = await _embed(
    items.map((x) => x.text),
    onProgress,
  );
  _corpus = items.map((x, i) => ({ ...x, embedding: embeddings[i] }));
  return _corpus;
}

export async function refreshCorpus(onProgress) {
  _corpusPromise = _buildCorpus(onProgress);
  return _corpusPromise;
}

async function _ensureCorpus(onProgress) {
  if (_corpus) return _corpus;
  if (!_corpusPromise) _corpusPromise = _buildCorpus(onProgress);
  return _corpusPromise;
}

/* ── Public: warmup + buildContext ──────────────────────────────── */

/**
 * Preload the embedding model and corpus so the first query is fast.
 * @param {(report:{progress:number,text:string})=>void} [onProgress]
 */
export async function warmup(onProgress) {
  await _getExtractor(onProgress);
  await _ensureCorpus(onProgress);
}

/**
 * Build a RAG context block for `query`, mirroring CrossLingualRAG.query.
 *
 * @param {string} query
 * @param {string} targetLanguage   user's language code
 * @returns {Promise<{context:string, results:Array}>}
 */
export async function buildContext(query, targetLanguage = 'es') {
  const corpus = await _ensureCorpus();
  if (!corpus.length) return { context: '', results: [] };

  const [queryVec] = await _embed([query]);

  // Score every fragment.
  const scored = corpus.map((c) => ({
    text: c.text,
    language: c.language,
    txId: c.txId,
    score: _cosine(queryVec, c.embedding),
    crossLingual: c.language !== targetLanguage,
  }));

  // Sort: same-language first, then score desc (mirror of the bot).
  scored.sort((a, b) => {
    if (a.crossLingual !== b.crossLingual) return a.crossLingual ? 1 : -1;
    return b.score - a.score;
  });

  // Dedup by first-100-char hash, keep TOP_K.
  const seen = new Set();
  const merged = [];
  for (const r of scored) {
    const key = r.text.slice(0, 100);
    if (!seen.has(key)) {
      seen.add(key);
      merged.push(r);
    }
    if (merged.length === TOP_K) break;
  }

  // Relevance window.
  const relevant = merged.filter(
    (r) => r.score >= MIN_RELEVANCE_SCORE && r.score <= NEAR_CLONE_THRESHOLD,
  );
  if (!relevant.length) return { context: '', results: [] };

  // Build context with per-fragment truncation + cross-lingual markers.
  const parts = [];
  let total = 0;
  for (const r of relevant) {
    let text = r.text.trim();
    if (text.length > SNIPPET_MAX_CHARS) {
      text = text.slice(0, SNIPPET_MAX_CHARS).replace(/\s+\S*$/, '') + '…';
    }
    const entry = r.crossLingual ? `— [${r.language}] ${text}\n` : `— ${text}\n`;
    if (total + entry.length > MAX_CONTEXT_CHARS) {
      const remaining = MAX_CONTEXT_CHARS - total;
      if (remaining > 80) parts.push(entry.slice(0, remaining) + '…');
      break;
    }
    parts.push(entry);
    total += entry.length;
  }

  return { context: parts.join('\n'), results: relevant };
}

/**
 * Wrap a user message with the RAG context the way the bot's Thinker
 * expects it (the "do not copy verbatim" instruction sits next to the
 * fragments — last-instruction bias).
 */
export function augmentUserMessage(userMessage, context, targetLanguageName) {
  const parts = [];
  if (context) {
    parts.push(
      '📜 IMMORTAL MEMORY FRAGMENTS (hints, NOT answers):\n' +
        context +
        '\nCRITICAL: these fragments are deliberately truncated (end in …). ' +
        'Use them as inspiration. Do NOT complete or copy them. Synthesize ' +
        'your own answer in your own voice.',
    );
  }
  parts.push(userMessage);
  if (targetLanguageName) {
    parts.push(`[Respond ONLY in ${targetLanguageName}.]`);
  }
  return parts.join('\n\n');
}

export function corpusSize() {
  return _corpus ? _corpus.length : 0;
}
