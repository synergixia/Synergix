/**
 * synergix-config.js
 *
 * Single source of truth for on-chain addresses, public endpoints and
 * tag conventions used by the website.  Mirror of the bot's Python
 * constants — keep in sync if either side changes.
 *
 * Nothing secret may live here: this file is shipped to every visitor.
 */

export const SYNERGIX_CONFIG = Object.freeze({
  /* ── Token / chain ──────────────────────────────────────────── */
  TOKEN_ADDRESS: '0x6485907278c389e70c572f441ce7052da58effff',
  CHAIN_ID: 56,
  CHAIN_HEX: '0x38',
  CHAIN_NAME: 'BNB Smart Chain',
  BSC_RPC_URL: 'https://bsc-dataseed1.binance.org',

  /* ── Irys ───────────────────────────────────────────────────── */
  IRYS_GRAPHQL: 'https://uploader.irys.xyz/graphql',
  IRYS_GATEWAY: 'https://gateway.irys.xyz',
  IRYS_APP_NAME: 'Synergix',

  /* ── Owner address that signs Irys uploads ─────────────────────
   * The bot derives this from PRIVATE_KEY at runtime; here we keep
   * it null and let queries match by App-Name=Synergix only.  Set
   * via window.SYNERGIX_OWNER before the script loads to add an
   * extra owner filter to every GraphQL call.                      */
  IRYS_OWNER: (typeof window !== 'undefined' && window.SYNERGIX_OWNER) || null,

  /* ── Four.meme bonding-curve managers ───────────────────────── */
  FOUR_MEME_MANAGERS: [
    '0x5c952063c7fc8610FFDB798152D69F0B9550762b',
    '0xEc4549caDcE5DA21Df6E6422d448034B5233bFbC',
  ],

  /* ── DexScreener ────────────────────────────────────────────── */
  DEXSCREENER_API: 'https://api.dexscreener.com/latest/dex/tokens',

  /* ── Public links ───────────────────────────────────────────── */
  LINKS: {
    fourMeme: 'https://four.meme/token/0x6485907278c389e70c572f441ce7052da58effff',
    bscscan: 'https://bscscan.com/token/0x6485907278c389e70c572f441ce7052da58effff',
    telegram: 'https://t.me/+E5ndVSGPuKYwMDlh',
    telegramBot: 'https://t.me/synergix_ai_bot',
    twitter: 'https://x.com/synergix_a',
  },

  /* ── SIWE (matches aisynergix/services/wallet_verify.py) ───── */
  SIWE: {
    domain: 'synergix.bot',
    uri: 'https://synergix.bot',
    statement:
      'I confirm ownership of this wallet for Synergix Identity Verification. ' +
      'This signature is gasless and cannot move funds or execute transactions.',
    challengeTtlSec: 600,
  },

  /* ── Tag conventions used in queries ────────────────────────── */
  TAGS: {
    APORTE: 'aporte',
    USER_PROFILE: 'user-profile',
    USER_POINTER: 'user-profile-pointer',
    BRAIN_INDEX: 'brain-index',
    BRAIN_META: 'brain-meta',
    EMERGENCY_LOCK: 'emergency-lock',
    AI_GUARD: 'ai-guard',
    SYSTEM_CONFIG: 'system-config',
  },

  /* ── Polling intervals (ms) ─────────────────────────────────── */
  POLL: {
    market: 60_000,           // DexScreener cache window
    bondingCurve: 90_000,
    memoryFeed: 120_000,
    leaderboard: 180_000,
  },
});

/** Convenience helper: build the gateway URL for a tx id. */
export const gw = (txId) => `${SYNERGIX_CONFIG.IRYS_GATEWAY}/${txId}`;

/** Format a BSC address for display: 0xABCD…1234 */
export const shortAddr = (addr) =>
  addr ? `${addr.slice(0, 6)}…${addr.slice(-4)}` : '';
