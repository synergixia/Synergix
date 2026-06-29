/**
 * synergix-market.js
 *
 * Live on-chain market data for $SYNERGIX:
 *   getMarket()         → DexScreener: price/24h/liquidity/FDV
 *   getBondingCurve()   → Four.meme manager: raised vs target progress
 *
 * Both are public endpoints — no API key, no backend required.
 * Mirrors aisynergix/services/dexscreener.py and four_meme.py.
 */

import { SYNERGIX_CONFIG } from './synergix-config.js';

const { TOKEN_ADDRESS, DEXSCREENER_API, FOUR_MEME_MANAGERS, BSC_RPC_URL } =
  SYNERGIX_CONFIG;

/* ── DexScreener ────────────────────────────────────────────────── */

let _marketCache = { ts: 0, data: null };
const MARKET_TTL_MS = 60_000;

/**
 * Fetch the most-liquid BSC pair for the token.
 * @returns {Promise<MarketData|null>}
 */
export async function getMarket({ signal } = {}) {
  const now = Date.now();
  if (_marketCache.data && now - _marketCache.ts < MARKET_TTL_MS) {
    return _marketCache.data;
  }
  let payload;
  try {
    const res = await fetch(`${DEXSCREENER_API}/${TOKEN_ADDRESS}`, { signal });
    if (!res.ok) throw new Error(`DexScreener HTTP ${res.status}`);
    payload = await res.json();
  } catch (err) {
    console.warn('DexScreener fetch failed:', err);
    return _marketCache.data;
  }

  const pairs = payload?.pairs ?? [];
  if (!pairs.length) return null;
  const bsc = pairs.filter((p) => (p.chainId ?? '').toLowerCase() === 'bsc');
  const candidates = bsc.length ? bsc : pairs;
  const best = candidates.reduce((a, b) =>
    Number(a?.liquidity?.usd ?? 0) >= Number(b?.liquidity?.usd ?? 0) ? a : b,
  );

  const txns = best.txns?.h24 ?? {};
  const data = {
    dexId: best.dexId ?? '',
    pairAddress: best.pairAddress ?? '',
    pairUrl: best.url ?? '',
    priceUsd: Number(best.priceUsd ?? 0),
    priceNative: Number(best.priceNative ?? 0),
    priceChange24h: Number(best.priceChange?.h24 ?? 0),
    volume24hUsd: Number(best.volume?.h24 ?? 0),
    liquidityUsd: Number(best.liquidity?.usd ?? 0),
    fdv: Number(best.fdv ?? 0),
    marketCap: Number(best.marketCap ?? 0),
    txnsBuys: Number(txns.buys ?? 0),
    txnsSells: Number(txns.sells ?? 0),
  };
  _marketCache = { ts: now, data };
  return data;
}

/* ── Four.meme bonding curve ────────────────────────────────────── */
/* The TokenManager exposes _tokenInfos(address) returning
   (base,quote,template,totalSupply,maxOffers,maxRaising,launchTime,
    offers,funds,lastPrice,K,T,status).  We need maxRaising[5],
    funds[8] and status[12].  Read via raw eth_call so we don't drag
    the full ethers ABI runtime — saves ~150 KB on the page. */

const FN_TOKEN_INFOS_SELECTOR = '0xd23b4d83'; // _tokenInfos(address)

function _padAddress(addr) {
  const clean = addr.toLowerCase().replace(/^0x/, '');
  return '000000000000000000000000' + clean;
}

function _hexToBigInt(hex) {
  if (!hex || hex === '0x' || hex === '0x0') return 0n;
  return BigInt(hex);
}

function _wordAt(hex, index) {
  // hex is "0x" + 64-char words
  const start = 2 + index * 64;
  return '0x' + hex.slice(start, start + 64);
}

async function _ethCall(to, data, { signal } = {}) {
  const res = await fetch(BSC_RPC_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      jsonrpc: '2.0',
      id: 1,
      method: 'eth_call',
      params: [{ to, data }, 'latest'],
    }),
    signal,
  });
  if (!res.ok) throw new Error(`BSC RPC HTTP ${res.status}`);
  const json = await res.json();
  if (json.error) throw new Error(`BSC RPC: ${json.error.message}`);
  return json.result;
}

async function _readManager(manager, token, { signal } = {}) {
  const callData = FN_TOKEN_INFOS_SELECTOR + _padAddress(token);
  let raw;
  try {
    raw = await _ethCall(manager, callData, { signal });
  } catch (err) {
    console.debug(`four.meme manager ${manager} read failed:`, err.message);
    return null;
  }
  if (!raw || raw === '0x') return null;

  const baseHex = _wordAt(raw, 0);
  const base = BigInt(baseHex);
  if (base === 0n) return null; // not registered in this manager

  const maxRaising = _hexToBigInt(_wordAt(raw, 5));
  const funds = _hexToBigInt(_wordAt(raw, 8));
  const status = Number(_hexToBigInt(_wordAt(raw, 12)));

  // 1e18 in BigInt
  const ONE_E18 = 1_000_000_000_000_000_000n;
  const toBnb = (v) => Number(v) / Number(ONE_E18); // safe ≤ ~9e15 BNB

  return {
    manager,
    maxRaisingBnb: maxRaising > 0n ? toBnb(maxRaising) : 0,
    fundsBnb: funds > 0n ? toBnb(funds) : 0,
    status,
  };
}

let _bcCache = { ts: 0, data: null };
const BC_TTL_MS = 90_000;

/**
 * Bonding-curve progress for the Synergix token on Four.meme.
 * @returns {Promise<BondingCurve|null>}
 */
export async function getBondingCurve({ signal } = {}) {
  const now = Date.now();
  if (_bcCache.data && now - _bcCache.ts < BC_TTL_MS) {
    return _bcCache.data;
  }
  for (const manager of FOUR_MEME_MANAGERS) {
    const info = await _readManager(manager, TOKEN_ADDRESS, { signal });
    if (!info) continue;
    const { maxRaisingBnb, fundsBnb, status } = info;
    const percent = maxRaisingBnb > 0 ? (fundsBnb / maxRaisingBnb) * 100 : 0;
    const graduated =
      status >= 2 || (maxRaisingBnb > 0 && fundsBnb >= maxRaisingBnb);
    const data = {
      graduated,
      percent: Math.round(Math.min(percent, 100) * 100) / 100,
      raisedBnb: Math.round(fundsBnb * 1e6) / 1e6,
      targetBnb: Math.round(maxRaisingBnb * 1e6) / 1e6,
      manager,
      status,
    };
    _bcCache = { ts: now, data };
    return data;
  }
  // Not registered in any manager — already graduated or pre-listing
  const data = {
    graduated: true,
    percent: 100,
    raisedBnb: 0,
    targetBnb: 0,
    manager: '',
    status: -1,
  };
  _bcCache = { ts: now, data };
  return data;
}

/* ── Type hints ─────────────────────────────────────────────────── */
/**
 * @typedef {Object} MarketData
 * @property {string} dexId
 * @property {string} pairAddress
 * @property {string} pairUrl
 * @property {number} priceUsd
 * @property {number} priceNative
 * @property {number} priceChange24h
 * @property {number} volume24hUsd
 * @property {number} liquidityUsd
 * @property {number} fdv
 * @property {number} marketCap
 * @property {number} txnsBuys
 * @property {number} txnsSells
 *
 * @typedef {Object} BondingCurve
 * @property {boolean} graduated
 * @property {number} percent       0–100
 * @property {number} raisedBnb
 * @property {number} targetBnb
 * @property {string} manager
 * @property {number} status
 */
