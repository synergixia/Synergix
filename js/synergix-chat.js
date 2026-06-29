/**
 * synergix-chat.js
 *
 * The interactive Synergix Oracle for the website.  Two execution modes:
 *
 *   ☁️  CLOUD  (phase B) — talks to the real Thinker + server-side RAG
 *       through same-origin Vercel functions (/api/chat, /api/judge,
 *       /api/contribute).  The functions proxy to the bot's web API over
 *       a Cloudflare tunnel; the API key never reaches the browser.
 *
 *   🖥️  LOCAL  (phase C) — runs Llama-3.2-1B in the visitor's browser via
 *       WebLLM, with client-side RAG over Irys content-summaries.  Zero
 *       server, zero cost, works offline after the first model download.
 *
 * Mode is auto-detected: if /api/synergix-health reports a configured
 * upstream, CLOUD is the default; otherwise LOCAL (when WebGPU exists).
 * The user can switch at any time.
 *
 * Mounts into #st-synergix-chat (created by the HTML edits).
 */

import { SYNERGIX_CONFIG, shortAddr } from './synergix-config.js';
import { t, applyI18n, getI18nLang } from './synergix-i18n.js';
import * as webllm from './synergix-webllm.js';
import * as browserRag from './synergix-rag-browser.js';
import {
  detectWallet,
  connect as walletConnect,
  buildChallenge,
  signChallenge,
  recoverSigner,
} from './synergix-wallet.js';

const $ = (sel, root = document) => root.querySelector(sel);

const API = {
  health: '/api/synergix-health',
  chat: '/api/chat',
  judge: '/api/judge',
  contribute: '/api/contribute',
};

/* Human-readable language names for the "respond in X" instruction. */
const LANG_NAMES = {
  es: 'español', en: 'English', pt: 'Português', fr: 'Français',
  de: 'Deutsch', zh: '中文', ja: '日本語', ko: '한국어',
  ar: 'العربية', hi: 'हिन्दी', bn: 'বাংলা', id: 'Bahasa Indonesia',
  ur: 'اردو',
};

const escapeHtml = (s = '') =>
  s.replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));

/* ── Module state ───────────────────────────────────────────────── */

const state = {
  mode: null,            // 'cloud' | 'local'
  cloudAvailable: false,
  history: [],           // [{role, content}]
  busy: false,
  modelId: webllm.defaultModelId(),
  modelReady: false,
  abort: null,
};

/* ── Cloud availability probe ───────────────────────────────────── */

async function probeCloud() {
  try {
    const res = await fetch(API.health, { method: 'GET' });
    if (!res.ok) return false;
    const j = await res.json();
    return Boolean(j?.upstreamConfigured);
  } catch {
    return false;
  }
}

/* ── Cloud chat (SSE through Vercel proxy) ──────────────────────── */

async function cloudChat(message, onChunk, signal) {
  const res = await fetch(API.chat, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      message,
      history: state.history.slice(-10),
      lang: getI18nLang(),
    }),
    signal,
  });
  if (!res.ok || !res.body) {
    throw new Error(`chat HTTP ${res.status}`);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  let full = '';
  let memoryCount = 0;

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const lines = buf.split('\n');
    buf = lines.pop() ?? '';
    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed || !trimmed.startsWith('data:')) continue;
      const payload = trimmed.slice(5).trim();
      if (payload === '[DONE]') return { full, memoryCount };
      try {
        const obj = JSON.parse(payload);
        if (obj.kind === 'memory_count') {
          memoryCount = Number(obj.text) || 0;
        } else if (obj.kind === 'answer' && obj.text) {
          full += obj.text;
          onChunk(obj.text);
        } else if (obj.kind === 'error') {
          throw new Error(obj.text || 'cloud error');
        }
      } catch {
        /* tolerate non-JSON keepalives */
      }
    }
  }
  return { full, memoryCount };
}

/* ── Local chat (WebLLM + browser RAG) ──────────────────────────── */

async function localChat(message, onChunk, onProgress, signal) {
  const lang = getI18nLang();
  // 1. Build RAG context client-side.
  let context = '';
  let memoryCount = 0;
  try {
    const rag = await browserRag.buildContext(message, lang);
    context = rag.context;
    memoryCount = rag.results.length;
  } catch (err) {
    console.warn('browser RAG failed (continuing without context):', err);
  }

  // 2. Assemble the chat messages with the Synergix persona.
  const augmented = browserRag.augmentUserMessage(
    message,
    context,
    LANG_NAMES[lang] || 'English',
  );
  const messages = [
    { role: 'system', content: webllm.LOCAL_SYSTEM_PROMPT },
    ...state.history.slice(-8),
    { role: 'user', content: augmented },
  ];

  // 3. Stream from WebLLM.
  const full = await webllm.streamChat(messages, {
    modelId: state.modelId,
    onToken: onChunk,
    signal,
  });
  return { full, memoryCount };
}

/* ── DOM rendering ──────────────────────────────────────────────── */

let els = {};

function renderShell(root) {
  root.innerHTML = `
    <div class="sh">
      <div class="sh-eye"><span class="eye-inner" data-st-i18n="ch_eye"></span></div>
      <h2 class="sh-title"><span class="wh" data-st-i18n="ch_title"></span></h2>
      <p class="sh-sub" data-st-i18n="ch_sub"></p>
    </div>

    <div class="st-chat-modebar">
      <div class="st-chat-modes" role="tablist">
        <button type="button" class="st-mode-btn" data-mode="cloud" data-st-i18n="ch_mode_cloud"></button>
        <button type="button" class="st-mode-btn" data-mode="local" data-st-i18n="ch_mode_local"></button>
      </div>
      <div class="st-chat-modehint" id="st-chat-modehint"></div>
    </div>

    <div class="st-chat-local-setup" id="st-chat-localsetup" hidden>
      <label class="st-chat-modellbl" data-st-i18n="ch_model"></label>
      <select class="st-chat-modelsel" id="st-chat-model"></select>
      <button type="button" class="st-btn st-btn-primary" id="st-chat-load" data-st-i18n="ch_load_model"></button>
      <div class="st-chat-progress" id="st-chat-progress" hidden>
        <div class="st-chat-progress-bar"><div class="st-chat-progress-fill" id="st-chat-progress-fill"></div></div>
        <span class="st-chat-progress-txt" id="st-chat-progress-txt"></span>
      </div>
    </div>

    <div class="st-chat-window" id="st-chat-window" aria-live="polite"></div>

    <form class="st-chat-form" id="st-chat-form" autocomplete="off">
      <input type="text" class="st-chat-input" id="st-chat-input"
             data-st-i18n-ph="ch_placeholder" />
      <button type="submit" class="st-btn st-btn-primary" id="st-chat-send" data-st-i18n="ch_send"></button>
    </form>

    <div class="st-chat-disclaimer" data-st-i18n="ch_disclaimer"></div>
  `;
  applyI18n(root);
  // placeholders aren't covered by applyI18n (textContent only)
  root.querySelectorAll('[data-st-i18n-ph]').forEach((el) => {
    el.setAttribute('placeholder', t(el.getAttribute('data-st-i18n-ph')));
  });

  els = {
    modeBtns: root.querySelectorAll('.st-mode-btn'),
    modeHint: $('#st-chat-modehint', root),
    localSetup: $('#st-chat-localsetup', root),
    modelSel: $('#st-chat-model', root),
    loadBtn: $('#st-chat-load', root),
    progress: $('#st-chat-progress', root),
    progressFill: $('#st-chat-progress-fill', root),
    progressTxt: $('#st-chat-progress-txt', root),
    window: $('#st-chat-window', root),
    form: $('#st-chat-form', root),
    input: $('#st-chat-input', root),
    send: $('#st-chat-send', root),
  };

  // Populate model selector
  els.modelSel.innerHTML = webllm
    .listModels()
    .map(
      (m) =>
        `<option value="${m.id}" ${m.default ? 'selected' : ''}>${escapeHtml(
          m.label,
        )} · ~${m.sizeMb}MB</option>`,
    )
    .join('');
}

function pushBubble(role, text, opts = {}) {
  const div = document.createElement('div');
  div.className = `st-bubble st-bubble-${role}`;
  if (opts.id) div.id = opts.id;
  div.innerHTML = `<div class="st-bubble-body">${escapeHtml(text)}</div>`;
  els.window.appendChild(div);
  els.window.scrollTop = els.window.scrollHeight;
  return div;
}

function setModeUI() {
  els.modeBtns.forEach((b) =>
    b.classList.toggle('on', b.dataset.mode === state.mode),
  );
  const isLocal = state.mode === 'local';
  els.localSetup.hidden = !isLocal;

  if (state.mode === 'cloud') {
    els.modeHint.textContent = t('ch_hint_cloud');
    els.modeHint.className = 'st-chat-modehint st-ok';
  } else if (isLocal) {
    if (!webllm.isWebGpuAvailable()) {
      els.modeHint.textContent = t('ch_no_webgpu');
      els.modeHint.className = 'st-chat-modehint st-error';
      els.loadBtn.disabled = true;
    } else {
      els.modeHint.textContent = state.modelReady
        ? t('ch_hint_local_ready')
        : t('ch_hint_local');
      els.modeHint.className = 'st-chat-modehint';
    }
  }
  // Disable send until local model loaded (local mode only).
  els.send.disabled = state.busy || (isLocal && !state.modelReady);
  els.input.disabled = state.busy;
}

/* ── Event wiring ───────────────────────────────────────────────── */

function wire() {
  els.modeBtns.forEach((btn) => {
    btn.addEventListener('click', () => {
      if (btn.dataset.mode === 'cloud' && !state.cloudAvailable) {
        els.modeHint.textContent = t('ch_cloud_unavailable');
        els.modeHint.className = 'st-chat-modehint st-error';
        return;
      }
      state.mode = btn.dataset.mode;
      setModeUI();
    });
  });

  els.modelSel.addEventListener('change', () => {
    state.modelId = els.modelSel.value;
    state.modelReady = false;
    setModeUI();
  });

  els.loadBtn.addEventListener('click', async () => {
    if (state.busy) return;
    state.busy = true;
    els.loadBtn.disabled = true;
    els.progress.hidden = false;
    try {
      await Promise.all([
        webllm.getEngine(state.modelId, (r) => {
          els.progressFill.style.width = `${Math.round(r.progress * 100)}%`;
          els.progressTxt.textContent = r.text || `${Math.round(r.progress * 100)}%`;
        }),
        browserRag.warmup((r) => {
          els.progressTxt.textContent = r.text || els.progressTxt.textContent;
        }),
      ]);
      state.modelReady = true;
      els.progressTxt.textContent = t('ch_model_ready');
    } catch (err) {
      console.warn('local model load failed:', err);
      els.progressTxt.textContent = t('ch_model_fail');
      els.modeHint.textContent =
        err?.message === 'NO_WEBGPU' ? t('ch_no_webgpu') : t('ch_model_fail');
      els.modeHint.className = 'st-chat-modehint st-error';
    } finally {
      state.busy = false;
      els.loadBtn.disabled = false;
      setModeUI();
    }
  });

  els.form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const message = els.input.value.trim();
    if (!message || state.busy) return;
    els.input.value = '';
    await runTurn(message);
  });

  window.addEventListener('synergix:langchange', () => {
    // Refresh static labels + placeholder
    els.input.setAttribute('placeholder', t('ch_placeholder'));
    setModeUI();
  });
}

async function runTurn(message) {
  state.busy = true;
  setModeUI();
  pushBubble('user', message);

  const answerEl = pushBubble('oracle', '');
  const body = answerEl.querySelector('.st-bubble-body');
  body.innerHTML = `<span class="st-typing"><i></i><i></i><i></i></span>`;

  let acc = '';
  const onChunk = (tok) => {
    acc += tok;
    body.textContent = acc;
    els.window.scrollTop = els.window.scrollHeight;
  };

  state.abort = new AbortController();
  try {
    let result;
    if (state.mode === 'cloud') {
      result = await cloudChat(message, onChunk, state.abort.signal);
    } else {
      result = await localChat(message, onChunk, null, state.abort.signal);
    }
    const finalText = (result.full || acc).trim();
    body.textContent = finalText || '…';
    if (result.memoryCount > 0) {
      const foot = document.createElement('div');
      foot.className = 'st-bubble-foot';
      foot.textContent = t('ch_memory_used').replace('{n}', result.memoryCount);
      answerEl.appendChild(foot);
    }
    // Persist to history
    state.history.push({ role: 'user', content: message });
    state.history.push({ role: 'assistant', content: finalText });
    if (state.history.length > 20) state.history = state.history.slice(-20);
  } catch (err) {
    console.warn('chat turn failed:', err);
    body.textContent = t('ch_error');
    body.classList.add('st-error');
  } finally {
    state.busy = false;
    state.abort = null;
    setModeUI();
  }
}

/* ── Boot ───────────────────────────────────────────────────────── */

export async function mountChat() {
  const root = $('#st-synergix-chat');
  if (!root) return;

  renderShell(root);
  wire();

  // Auto-detect mode.
  state.cloudAvailable = await probeCloud();
  if (state.cloudAvailable) {
    state.mode = 'cloud';
  } else {
    state.mode = 'local';
  }
  setModeUI();

  // Greeting bubble.
  pushBubble('oracle', t('ch_greeting'));
}
