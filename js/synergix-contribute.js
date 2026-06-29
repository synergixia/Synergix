/**
 * synergix-contribute.js
 *
 * Phase B — contribute to the Immortal Memory directly from the website.
 *
 * Flow:
 *   1. Write text → "Evaluate" calls /api/judge (preview score+feedback).
 *   2. If approved (≥6.0) → connect wallet + sign SIWE challenge.
 *   3. "Seal on Irys" calls /api/contribute; the bot re-verifies the
 *      signature server-side, runs the Judge again, and uploads the
 *      aporte to Irys tagged with the signer wallet.  Returns the txId.
 *
 * Cloud-only: judging + Irys signing live on the bot side (the Irys
 * PRIVATE_KEY never touches the browser).  When no upstream is
 * configured the widget shows a notice pointing to the Telegram bot.
 *
 * Mounts into #st-contribute.
 */

import { gw } from './synergix-config.js';
import { t, applyI18n, getI18nLang } from './synergix-i18n.js';
import {
  detectWallet,
  connect as walletConnect,
  buildChallenge,
  signChallenge,
} from './synergix-wallet.js';

const $ = (sel, root = document) => root.querySelector(sel);
const escapeHtml = (s = '') =>
  s.replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));

const API = { health: '/api/synergix-health', judge: '/api/judge', contribute: '/api/contribute' };

const state = {
  cloudAvailable: false,
  evaluation: null,
  address: null,
  challenge: null,
  signature: null,
  busy: false,
};

let els = {};

async function probeCloud() {
  try {
    const res = await fetch(API.health);
    if (!res.ok) return false;
    const j = await res.json();
    return Boolean(j?.upstreamConfigured);
  } catch {
    return false;
  }
}

function render(root) {
  root.innerHTML = `
    <div class="sh">
      <div class="sh-eye"><span class="eye-inner" data-st-i18n="co_eye"></span></div>
      <h2 class="sh-title"><span class="wh" data-st-i18n="co_title"></span></h2>
      <p class="sh-sub" data-st-i18n="co_sub"></p>
    </div>

    <div class="st-co-body" id="st-co-body"></div>
  `;
  applyI18n(root);
  renderBody(root);
}

function renderBody(root) {
  const body = $('#st-co-body', root);

  if (!state.cloudAvailable) {
    body.innerHTML = `
      <div class="st-co-notice">
        <p data-st-i18n="co_cloud_off"></p>
        <a class="st-btn st-btn-primary" target="_blank" rel="noopener"
           href="https://t.me/synergix_ai_bot">🤖 <span data-st-i18n="co_use_bot"></span></a>
      </div>`;
    applyI18n(body);
    return;
  }

  const evalBlock = state.evaluation
    ? renderEvaluation(state.evaluation)
    : '';

  const approved = state.evaluation?.approved;
  const walletBlock = approved
    ? `
      <div class="st-co-seal">
        ${
          state.address
            ? `<div class="st-co-wallet">🔐 ${escapeHtml(state.address.slice(0, 6))}…${escapeHtml(state.address.slice(-4))}</div>`
            : ''
        }
        ${
          !state.address
            ? `<button type="button" class="st-btn st-btn-ghost" id="st-co-connect" data-st-i18n="co_connect_wallet"></button>`
            : !state.signature
              ? `<button type="button" class="st-btn st-btn-ghost" id="st-co-sign" data-st-i18n="co_sign"></button>`
              : `<button type="button" class="st-btn st-btn-primary" id="st-co-seal" data-st-i18n="co_seal"></button>`
        }
      </div>`
    : '';

  body.innerHTML = `
    <textarea class="st-co-textarea" id="st-co-text"
              maxlength="5000" data-st-i18n-ph="co_placeholder"></textarea>
    <div class="st-co-actions">
      <button type="button" class="st-btn st-btn-primary" id="st-co-eval" data-st-i18n="co_evaluate"></button>
      <span class="st-co-count" id="st-co-count">0 / 5000</span>
    </div>
    ${evalBlock}
    ${walletBlock}
    <div class="st-co-result" id="st-co-result"></div>
  `;
  applyI18n(body);
  body.querySelectorAll('[data-st-i18n-ph]').forEach((el) => {
    el.setAttribute('placeholder', t(el.getAttribute('data-st-i18n-ph')));
  });

  wireBody(root);
}

function renderEvaluation(ev) {
  const score = Number(ev.quality_score ?? 0);
  const approved = ev.approved;
  const cls = approved ? 'st-ok' : 'st-error';
  const fb = approved ? '' : escapeHtml(ev.constructive_feedback || '');
  return `
    <div class="st-co-eval ${cls}">
      <div class="st-co-eval-head">
        <span class="st-co-score">★ ${score.toFixed(1)}</span>
        <span class="st-co-cat">${escapeHtml(ev.category || 'filosofia')}</span>
        <span class="st-co-verdict">${approved ? t('co_approved') : t('co_rejected')}</span>
      </div>
      <p class="st-co-reason">${escapeHtml(ev.reason || '')}</p>
      ${fb ? `<p class="st-co-feedback">💡 ${fb}</p>` : ''}
    </div>`;
}

function wireBody(root) {
  const textEl = $('#st-co-text', root);
  const countEl = $('#st-co-count', root);
  if (textEl) {
    // Preserve any in-progress text across re-renders.
    if (state._draft) textEl.value = state._draft;
    countEl.textContent = `${textEl.value.length} / 5000`;
    textEl.addEventListener('input', () => {
      state._draft = textEl.value;
      countEl.textContent = `${textEl.value.length} / 5000`;
    });
  }

  $('#st-co-eval', root)?.addEventListener('click', onEvaluate);
  $('#st-co-connect', root)?.addEventListener('click', onConnect);
  $('#st-co-sign', root)?.addEventListener('click', onSign);
  $('#st-co-seal', root)?.addEventListener('click', onSeal);
}

async function onEvaluate() {
  const text = (state._draft || '').trim();
  const result = $('#st-co-result');
  if (text.length < 20) {
    if (result) {
      result.textContent = t('co_too_short');
      result.className = 'st-co-result st-error';
    }
    return;
  }
  state.busy = true;
  const btn = $('#st-co-eval');
  if (btn) { btn.disabled = true; btn.textContent = t('co_evaluating'); }
  try {
    const res = await fetch(API.judge, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, lang: getI18nLang() }),
    });
    const data = await res.json();
    if (data?.error) throw new Error(data.error);
    state.evaluation = data;
    state.signature = null; // re-evaluation invalidates any prior seal step
  } catch (err) {
    console.warn('judge failed:', err);
    state.evaluation = null;
    if (result) {
      result.textContent = t('co_eval_error');
      result.className = 'st-co-result st-error';
    }
  } finally {
    state.busy = false;
    rerender();
  }
}

async function onConnect() {
  if (!detectWallet()) {
    const result = $('#st-co-result');
    if (result) {
      result.textContent = t('wv_no_metamask');
      result.className = 'st-co-result st-error';
    }
    return;
  }
  try {
    const conn = await walletConnect();
    state.address = conn.address;
    state.challenge = buildChallenge(conn.address);
    state.signature = null;
  } catch (err) {
    console.warn('connect failed:', err);
  } finally {
    rerender();
  }
}

async function onSign() {
  if (!state.address || !state.challenge) return;
  try {
    state.signature = await signChallenge(state.address, state.challenge);
  } catch (err) {
    console.warn('sign failed:', err);
    state.signature = null;
  } finally {
    rerender();
  }
}

async function onSeal() {
  const text = (state._draft || '').trim();
  if (!text || !state.address || !state.signature) return;
  state.busy = true;
  const btn = $('#st-co-seal');
  if (btn) { btn.disabled = true; btn.textContent = t('co_sealing'); }
  const result = $('#st-co-result');
  try {
    const res = await fetch(API.contribute, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        text,
        address: state.address,
        challenge: state.challenge,
        signature: state.signature,
        lang: getI18nLang(),
      }),
    });
    const data = await res.json();
    if (data?.status === 'success' && data.txId) {
      const url = data.gatewayUrl || gw(data.txId);
      if (result) {
        result.innerHTML = `${t('co_sealed')} <a href="${url}" target="_blank" rel="noopener">${data.txId.slice(0, 10)}… ↗</a>`;
        result.className = 'st-co-result st-ok';
      }
      // Reset draft after a successful seal.
      state._draft = '';
      state.evaluation = null;
      state.signature = null;
    } else if (data?.status === 'rejected') {
      state.evaluation = data.evaluation || state.evaluation;
      if (result) {
        result.textContent = t('co_rejected_seal');
        result.className = 'st-co-result st-error';
      }
    } else {
      throw new Error(data?.error || 'seal failed');
    }
  } catch (err) {
    console.warn('seal failed:', err);
    if (result) {
      result.textContent = t('co_seal_error');
      result.className = 'st-co-result st-error';
    }
  } finally {
    state.busy = false;
    rerender();
  }
}

let _root = null;
function rerender() {
  if (_root) renderBody(_root);
}

export async function mountContribute() {
  const root = $('#st-contribute');
  if (!root) return;
  _root = root;
  state.cloudAvailable = await probeCloud();
  render(root);
  window.addEventListener('synergix:langchange', () => render(root));
}
