/**
 * synergix-tools.js
 *
 * Entry point for the on-chain tool widgets.  This file is loaded
 * once from index.html with `<script type="module" defer>` and
 * mounts each widget into its container.
 *
 * Containers expected in the DOM (created by the HTML edits):
 *   #st-live-stats        → live market + bonding curve
 *   #st-memory-explorer   → recent aportes feed
 *   #st-top-minds         → leaderboard
 *   #st-wallet-verify     → MetaMask + SIWE
 *
 * Failure of any single widget is contained — the rest still mount.
 */

import { SYNERGIX_CONFIG, gw, shortAddr } from './synergix-config.js';
import {
  listAportes,
  computeTopMinds,
  fetchAporteText,
} from './synergix-irys.js';
import { getMarket, getBondingCurve } from './synergix-market.js';
import {
  detectWallet,
  connect as walletConnect,
  buildChallenge,
  signChallenge,
  recoverSigner,
  onAccountsChanged,
  onChainChanged,
} from './synergix-wallet.js';
import {
  applyI18n,
  bindLanguageBar,
  t,
  getI18nLang,
} from './synergix-i18n.js';
import { mountChat } from './synergix-chat.js';
import { mountContribute } from './synergix-contribute.js';

/* ── Tiny helpers ───────────────────────────────────────────────── */

const $ = (sel, root = document) => root.querySelector(sel);
const fmtUsd = (n, digits = 4) => {
  if (!n || !isFinite(n)) return '$0';
  if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `$${(n / 1_000).toFixed(2)}K`;
  if (n >= 1) return `$${n.toFixed(2)}`;
  return `$${n.toFixed(digits)}`;
};
const fmtPct = (n) => {
  if (n == null || !isFinite(n)) return '—';
  const sign = n > 0 ? '+' : '';
  return `${sign}${n.toFixed(2)}%`;
};
const timeAgo = (sec) => {
  if (!sec) return '—';
  // Irys timestamps are ms since unix epoch (large) or seconds — guess.
  const ts = sec > 1e12 ? sec : sec * 1000;
  const diff = Math.max(0, Date.now() - ts);
  const m = Math.floor(diff / 60_000);
  if (m < 1) return 'just now';
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  return `${d}d ago`;
};
const escapeHtml = (s = '') =>
  s.replace(
    /[&<>"']/g,
    (c) =>
      ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;',
      }[c]),
  );

/* ── Widget: live stats (price + bonding curve) ─────────────────── */

async function mountLiveStats() {
  const root = $('#st-live-stats');
  if (!root) return;

  const render = ({ market, curve, error }) => {
    const priceCell = market
      ? `<span class="st-stat-num">${fmtUsd(market.priceUsd)}</span>`
      : `<span class="st-stat-num st-dim">${error ? '—' : t('st_loading')}</span>`;

    const change = market ? market.priceChange24h : 0;
    const changeClass = change >= 0 ? 'st-up' : 'st-down';

    const curveBlock = curve
      ? curve.graduated && curve.percent >= 100
        ? `<div class="st-curve-graduated">${t('st_curve_graduated')}</div>`
        : `
          <div class="st-curve-bar">
            <div class="st-curve-fill" style="width:${Math.max(2, curve.percent).toFixed(2)}%"></div>
          </div>
          <div class="st-curve-meta">
            <span><strong>${curve.percent.toFixed(2)}%</strong></span>
            <span>${curve.raisedBnb.toFixed(3)} / ${curve.targetBnb.toFixed(3)} BNB</span>
          </div>`
      : `<div class="st-dim">${t('st_loading')}</div>`;

    root.innerHTML = `
      <div class="st-card-title">
        <span class="st-pulse-dot"></span>
        <span data-st-i18n="st_stats_title">${t('st_stats_title')}</span>
      </div>
      <p class="st-card-sub" data-st-i18n="st_stats_sub">${t('st_stats_sub')}</p>

      <div class="st-stats-grid">
        <div class="st-stat">
          <div class="st-stat-lbl" data-st-i18n="st_price">${t('st_price')}</div>
          ${priceCell}
          <div class="st-stat-extra ${changeClass}">${fmtPct(change)} <span class="st-dim" data-st-i18n="st_change_24h">${t('st_change_24h')}</span></div>
        </div>
        <div class="st-stat">
          <div class="st-stat-lbl" data-st-i18n="st_volume">${t('st_volume')}</div>
          <span class="st-stat-num">${market ? fmtUsd(market.volume24hUsd, 0) : '—'}</span>
        </div>
        <div class="st-stat">
          <div class="st-stat-lbl" data-st-i18n="st_liquidity">${t('st_liquidity')}</div>
          <span class="st-stat-num">${market ? fmtUsd(market.liquidityUsd, 0) : '—'}</span>
        </div>
        <div class="st-stat">
          <div class="st-stat-lbl" data-st-i18n="st_marketcap">${t('st_marketcap')}</div>
          <span class="st-stat-num">${market ? fmtUsd(market.marketCap || market.fdv, 0) : '—'}</span>
        </div>
      </div>

      <div class="st-curve-block">
        <div class="st-curve-lbl" data-st-i18n="st_curve_title">${t('st_curve_title')}</div>
        ${curveBlock}
      </div>

      <div class="st-card-actions">
        <a href="${SYNERGIX_CONFIG.LINKS.fourMeme}" target="_blank" rel="noopener" class="st-btn st-btn-primary">🚀 four.meme</a>
        <a href="${market?.pairUrl || SYNERGIX_CONFIG.LINKS.bscscan}" target="_blank" rel="noopener" class="st-btn st-btn-ghost">📊 DexScreener</a>
      </div>
    `;
  };

  // Initial skeleton render so the user sees structure immediately
  render({ market: null, curve: null });

  const refresh = async () => {
    const [market, curve] = await Promise.allSettled([
      getMarket(),
      getBondingCurve(),
    ]);
    render({
      market: market.status === 'fulfilled' ? market.value : null,
      curve: curve.status === 'fulfilled' ? curve.value : null,
    });
  };

  await refresh();
  setInterval(refresh, SYNERGIX_CONFIG.POLL.market);
}

/* ── Widget: Memory Explorer ────────────────────────────────────── */

async function mountMemoryExplorer() {
  const root = $('#st-memory-explorer');
  if (!root) return;

  const CATEGORIES = [
    { key: '', label: () => t('mx_filter_all'), icon: '∞' },
    { key: 'filosofia', label: () => 'Filosofía', icon: '🧠' },
    { key: 'ciencia', label: () => 'Ciencia', icon: '🔬' },
    { key: 'tecnologia', label: () => 'Tecnología', icon: '⚙️' },
    { key: 'programacion', label: () => 'Código', icon: '💻' },
    { key: 'arte', label: () => 'Arte', icon: '🎨' },
    { key: 'innovacion', label: () => 'Innovación', icon: '🚀' },
    { key: 'sociedad', label: () => 'Sociedad', icon: '🌍' },
    { key: 'economia', label: () => 'Economía', icon: '💰' },
    { key: 'naturaleza', label: () => 'Naturaleza', icon: '🌿' },
    { key: 'vida', label: () => 'Vida', icon: '✨' },
    { key: 'espiritualidad', label: () => 'Espíritu', icon: '🕯' },
  ];

  let activeCategory = '';
  let pageSize = 12;

  // Build static frame
  root.innerHTML = `
    <div class="sh">
      <div class="sh-eye"><span class="eye-inner" data-st-i18n="mx_eye"></span></div>
      <h2 class="sh-title"><span class="wh" data-st-i18n="mx_title"></span></h2>
      <p class="sh-sub" data-st-i18n="mx_sub"></p>
    </div>
    <div class="st-mx-filters" id="st-mx-filters"></div>
    <div class="st-mx-grid" id="st-mx-grid"></div>
    <div class="st-mx-actions">
      <button type="button" class="st-btn st-btn-ghost" id="st-mx-more" data-st-i18n="mx_loadmore"></button>
    </div>
  `;
  applyI18n(root);

  const filtersEl = $('#st-mx-filters', root);
  filtersEl.innerHTML = CATEGORIES.map(
    (c, i) => `
      <button type="button" class="st-chip ${i === 0 ? 'on' : ''}" data-cat="${c.key}">
        <span class="st-chip-ic">${c.icon}</span><span class="st-chip-lbl">${c.label()}</span>
      </button>`,
  ).join('');

  const gridEl = $('#st-mx-grid', root);

  const card = (a) => {
    const summary = a.contentSummary || '';
    const wallet = a.signatureWallet
      ? `<a href="https://bscscan.com/address/${a.signatureWallet}" target="_blank" rel="noopener" class="st-wallet-pill">🔐 ${shortAddr(a.signatureWallet)}</a>`
      : `<span class="st-wallet-pill st-dim">👤 ${a.author.slice(0, 8)}…</span>`;
    return `
      <article class="st-mx-card">
        <header class="st-mx-card-head">
          <span class="st-mx-cat">${escapeHtml(a.category || 'filosofia')}</span>
          <span class="st-mx-quality" title="${t('mx_quality')}">★ ${a.qualityScore.toFixed(1)}</span>
        </header>
        <p class="st-mx-summary">${escapeHtml(summary || '…')}</p>
        <footer class="st-mx-card-foot">
          ${wallet}
          <span class="st-mx-time">${timeAgo(a.timestamp)}</span>
        </footer>
        <a href="${a.gatewayUrl}" target="_blank" rel="noopener" class="st-mx-link">
          ${t('mx_view_raw')} ↗
        </a>
      </article>
    `;
  };

  const refresh = async () => {
    gridEl.innerHTML = `<div class="st-mx-loading">${t('mx_loading')}</div>`;
    try {
      const aportes = await listAportes({
        limit: pageSize,
        category: activeCategory || undefined,
      });
      if (!aportes.length) {
        gridEl.innerHTML = `<div class="st-mx-empty">${t('mx_empty')}</div>`;
        return;
      }
      gridEl.innerHTML = aportes.map(card).join('');
    } catch (err) {
      console.warn('Memory explorer failed:', err);
      gridEl.innerHTML = `<div class="st-mx-empty st-error">${t('mx_error')}</div>`;
    }
  };

  filtersEl.addEventListener('click', (e) => {
    const btn = e.target.closest?.('.st-chip');
    if (!btn) return;
    filtersEl.querySelectorAll('.st-chip').forEach((b) => b.classList.remove('on'));
    btn.classList.add('on');
    activeCategory = btn.dataset.cat || '';
    pageSize = 12;
    refresh();
  });

  $('#st-mx-more', root).addEventListener('click', () => {
    pageSize = Math.min(pageSize + 12, 60);
    refresh();
  });

  // Re-render category labels when language changes
  window.addEventListener('synergix:langchange', () => {
    filtersEl.querySelectorAll('.st-chip').forEach((btn, i) => {
      const lblEl = btn.querySelector('.st-chip-lbl');
      if (lblEl && CATEGORIES[i]) lblEl.textContent = CATEGORIES[i].label();
    });
    refresh();
  });

  await refresh();
  setInterval(refresh, SYNERGIX_CONFIG.POLL.memoryFeed);
}

/* ── Widget: Top Minds leaderboard ──────────────────────────────── */

async function mountTopMinds() {
  const root = $('#st-top-minds');
  if (!root) return;

  root.innerHTML = `
    <div class="sh">
      <div class="sh-eye"><span class="eye-inner" data-st-i18n="tm_eye"></span></div>
      <h2 class="sh-title"><span class="wh" data-st-i18n="tm_title"></span></h2>
      <p class="sh-sub" data-st-i18n="tm_sub"></p>
    </div>
    <div class="st-tm-table-wrap">
      <table class="st-tm-table" id="st-tm-table">
        <thead>
          <tr>
            <th data-st-i18n="tm_col_rank"></th>
            <th data-st-i18n="tm_col_who"></th>
            <th data-st-i18n="tm_col_points"></th>
            <th data-st-i18n="tm_col_aportes"></th>
            <th data-st-i18n="tm_col_uses"></th>
          </tr>
        </thead>
        <tbody id="st-tm-body">
          <tr><td colspan="5" class="st-tm-loading">${t('tm_loading')}</td></tr>
        </tbody>
      </table>
    </div>
  `;
  applyI18n(root);

  const refresh = async () => {
    const tbody = $('#st-tm-body', root);
    try {
      const minds = await computeTopMinds({ topN: 10 });
      if (!minds.length) {
        tbody.innerHTML = `<tr><td colspan="5" class="st-tm-loading">${t('mx_empty')}</td></tr>`;
        return;
      }
      tbody.innerHTML = minds
        .map((m, i) => {
          const medal = i === 0 ? '🥇' : i === 1 ? '🥈' : i === 2 ? '🥉' : `#${i + 1}`;
          const id = m.walletAddress
            ? `<a href="https://bscscan.com/address/${m.walletAddress}" target="_blank" rel="noopener">🔐 ${shortAddr(m.walletAddress)}</a>`
            : `<span class="st-dim">👤 ${m.uidHash.slice(0, 8)}…</span>`;
          return `
            <tr class="st-tm-row${i < 3 ? ' st-tm-podium' : ''}">
              <td class="st-tm-medal">${medal}</td>
              <td>
                <div class="st-tm-who">
                  ${id}
                  <span class="st-tm-rank">${escapeHtml(m.rank || '🌱 Iniciado')}</span>
                </div>
              </td>
              <td class="st-tm-num">${m.points.toLocaleString()}</td>
              <td class="st-tm-num">${m.contributionCount.toLocaleString()}</td>
              <td class="st-tm-num">${m.totalUsesCount.toLocaleString()}</td>
            </tr>
          `;
        })
        .join('');
    } catch (err) {
      console.warn('Top minds failed:', err);
      tbody.innerHTML = `<tr><td colspan="5" class="st-tm-loading st-error">${t('mx_error')}</td></tr>`;
    }
  };

  await refresh();
  setInterval(refresh, SYNERGIX_CONFIG.POLL.leaderboard);
  window.addEventListener('synergix:langchange', refresh);
}

/* ── Widget: Wallet verify ──────────────────────────────────────── */

async function mountWalletVerify() {
  const root = $('#st-wallet-verify');
  if (!root) return;

  let state = {
    address: null,
    chainId: null,
    onCorrectChain: false,
    challenge: null,
    signature: null,
    verified: null, // 'ok' | 'fail' | null
    irysProfile: null, // populated lazily after verify
    busy: false,
  };

  const wallet = detectWallet();

  const render = () => {
    if (!wallet) {
      root.innerHTML = `
        <div class="sh">
          <div class="sh-eye"><span class="eye-inner" data-st-i18n="wv_eye"></span></div>
          <h2 class="sh-title"><span class="wh" data-st-i18n="wv_title"></span></h2>
          <p class="sh-sub" data-st-i18n="wv_sub"></p>
        </div>
        <div class="st-wv-error" data-st-i18n="wv_no_metamask"></div>
        <div class="st-card-actions">
          <a href="https://metamask.io/download/" target="_blank" rel="noopener" class="st-btn st-btn-primary">🦊 Get MetaMask</a>
        </div>
      `;
      applyI18n(root);
      return;
    }

    const connectedBlock = state.address
      ? `
        <div class="st-wv-connected">
          <div class="st-wv-line">
            <span class="st-dim" data-st-i18n="wv_connected_as"></span>
            <strong class="st-wv-addr">${shortAddr(state.address)}</strong>
          </div>
          ${
            !state.onCorrectChain
              ? `<div class="st-wv-warn" data-st-i18n="wv_chain_wrong"></div>`
              : ''
          }
          ${
            state.verified === 'ok'
              ? `<div class="st-wv-ok" data-st-i18n="wv_signed_ok"></div>`
              : ''
          }
          ${
            state.verified === 'fail'
              ? `<div class="st-wv-fail" data-st-i18n="wv_signed_fail"></div>`
              : ''
          }
          ${
            state.irysProfile
              ? `<div class="st-wv-irys">
                  <span data-st-i18n="wv_known_in_irys"></span>
                  <strong>${escapeHtml(state.irysProfile.rank)}</strong>
                  · ${state.irysProfile.points.toLocaleString()} pts
                </div>`
              : ''
          }
        </div>
      `
      : '';

    const challengePreview = state.challenge
      ? `<pre class="st-wv-challenge">${escapeHtml(state.challenge)}</pre>`
      : '';

    const primaryBtn = !state.address
      ? `<button type="button" class="st-btn st-btn-primary" id="st-wv-connect" ${state.busy ? 'disabled' : ''} data-st-i18n="wv_connect"></button>`
      : `<button type="button" class="st-btn st-btn-primary" id="st-wv-sign" ${state.busy ? 'disabled' : ''} data-st-i18n="wv_sign"></button>`;

    root.innerHTML = `
      <div class="sh">
        <div class="sh-eye"><span class="eye-inner" data-st-i18n="wv_eye"></span></div>
        <h2 class="sh-title"><span class="wh" data-st-i18n="wv_title"></span></h2>
        <p class="sh-sub" data-st-i18n="wv_sub"></p>
      </div>
      ${connectedBlock}
      ${challengePreview}
      <div class="st-card-actions">
        ${primaryBtn}
      </div>
    `;
    applyI18n(root);

    $('#st-wv-connect', root)?.addEventListener('click', onConnect);
    $('#st-wv-sign', root)?.addEventListener('click', onSign);
  };

  const lookupIrysProfile = async (address) => {
    try {
      // Wallet → uid-hash mapping is private (bot-side). Best we can do
      // from the browser is search profiles whose wallet-address tag
      // matches.  We import lazily to avoid loading the heavy GraphQL
      // path for users who never click "Connect".
      const { listProfiles } = await import('./synergix-irys.js');
      const profiles = await listProfiles({ limit: 1000 });
      const match = profiles.find(
        (p) =>
          p.walletAddress &&
          p.walletAddress.toLowerCase() === address.toLowerCase(),
      );
      return match || null;
    } catch (err) {
      console.warn('Irys profile lookup failed:', err);
      return null;
    }
  };

  const onConnect = async () => {
    state.busy = true;
    render();
    try {
      const conn = await walletConnect();
      state.address = conn.address;
      state.chainId = conn.chainId;
      state.onCorrectChain = conn.onCorrectChain;
      state.challenge = buildChallenge(conn.address);
      state.verified = null;
      state.irysProfile = await lookupIrysProfile(conn.address);
    } catch (err) {
      console.warn('Wallet connect failed:', err);
    } finally {
      state.busy = false;
      render();
    }
  };

  const onSign = async () => {
    if (!state.address || !state.challenge) return;
    state.busy = true;
    render();
    try {
      const sig = await signChallenge(state.address, state.challenge);
      state.signature = sig;
      const recovered = await recoverSigner(state.challenge, sig);
      state.verified =
        recovered && recovered.toLowerCase() === state.address.toLowerCase()
          ? 'ok'
          : 'fail';
    } catch (err) {
      console.warn('Sign failed:', err);
      state.verified = err?.code === 4001 ? null : 'fail';
    } finally {
      state.busy = false;
      render();
    }
  };

  // React to wallet-side changes
  onAccountsChanged((addr) => {
    state.address = addr;
    state.signature = null;
    state.verified = null;
    state.irysProfile = null;
    state.challenge = addr ? buildChallenge(addr) : null;
    render();
    if (addr) lookupIrysProfile(addr).then((p) => {
      state.irysProfile = p;
      render();
    });
  });
  onChainChanged((id) => {
    state.chainId = id;
    state.onCorrectChain = id === SYNERGIX_CONFIG.CHAIN_ID;
    render();
  });

  render();
}

/* ── Boot ───────────────────────────────────────────────────────── */

function expose() {
  window.SynergixTools = Object.freeze({
    config: SYNERGIX_CONFIG,
    listAportes,
    computeTopMinds,
    fetchAporteText,
    getMarket,
    getBondingCurve,
    setLang(code) {
      window.dispatchEvent(
        new CustomEvent('synergix:setlang', { detail: { lang: code } }),
      );
    },
  });
}

async function boot() {
  bindLanguageBar();
  expose();
  // Mount in parallel; each widget is self-contained so a single
  // failure won't take down the others.
  const widgets = [
    mountLiveStats(),
    mountMemoryExplorer(),
    mountTopMinds(),
    mountWalletVerify(),
    mountChat(),
    mountContribute(),
  ];
  for (const p of widgets) {
    p.catch((err) => console.warn('Synergix tool widget failed:', err));
  }
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', boot, { once: true });
} else {
  boot();
}
