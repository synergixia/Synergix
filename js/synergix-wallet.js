/**
 * synergix-wallet.js
 *
 * Browser-side wallet verification.  Mirrors the SIWE flow used by
 * the bot in aisynergix/services/wallet_verify.py, but the user
 * signs with their own MetaMask (or any EIP-1193 wallet) — no need
 * to copy/paste anything into Etherscan and no server interaction.
 *
 * Public surface:
 *   detectWallet()                 → 'metamask' | 'unknown' | null
 *   connect()                      → { address, chainId }
 *   buildChallenge(address)        → SIWE message string
 *   signChallenge(address, msg)    → 0x… signature
 *   recoverSigner(msg, signature)  → recovered address (verification)
 *
 * The flow consumed by the UI in synergix-tools.js is:
 *   1. connect()
 *   2. buildChallenge(address)
 *   3. signChallenge(address, msg)
 *   4. recoverSigner(msg, sig) — must match address
 */

import { SYNERGIX_CONFIG } from './synergix-config.js';

const { CHAIN_ID, CHAIN_HEX, CHAIN_NAME, BSC_RPC_URL, SIWE } = SYNERGIX_CONFIG;

/* ── Provider detection ─────────────────────────────────────────── */

export function getProvider() {
  if (typeof window === 'undefined') return null;
  // Standard EIP-1193 provider injected by MetaMask, Trust, etc.
  return window.ethereum ?? null;
}

export function detectWallet() {
  const p = getProvider();
  if (!p) return null;
  if (p.isMetaMask) return 'metamask';
  if (p.isTrust) return 'trust';
  if (p.isCoinbaseWallet) return 'coinbase';
  return 'unknown';
}

/* ── Connect + chain switch ─────────────────────────────────────── */

async function ensureChain(provider) {
  let currentHex;
  try {
    currentHex = await provider.request({ method: 'eth_chainId' });
  } catch {
    return; // not all wallets support chainId reads — skip silently
  }
  if (currentHex?.toLowerCase() === CHAIN_HEX) return;
  try {
    await provider.request({
      method: 'wallet_switchEthereumChain',
      params: [{ chainId: CHAIN_HEX }],
    });
  } catch (err) {
    // 4902 = chain not added
    if (err?.code === 4902) {
      await provider.request({
        method: 'wallet_addEthereumChain',
        params: [
          {
            chainId: CHAIN_HEX,
            chainName: CHAIN_NAME,
            nativeCurrency: { name: 'BNB', symbol: 'BNB', decimals: 18 },
            rpcUrls: [BSC_RPC_URL],
            blockExplorerUrls: ['https://bscscan.com'],
          },
        ],
      });
    } else if (err?.code !== 4001) {
      // 4001 = user rejected
      console.warn('Chain switch rejected:', err);
    }
  }
}

export async function connect() {
  const provider = getProvider();
  if (!provider) {
    throw new Error('NO_WALLET');
  }
  const accounts = await provider.request({ method: 'eth_requestAccounts' });
  if (!accounts?.length) throw new Error('NO_ACCOUNTS');
  await ensureChain(provider);
  const chainHex = await provider.request({ method: 'eth_chainId' });
  return {
    address: accounts[0].toLowerCase(),
    chainId: parseInt(chainHex, 16),
    onCorrectChain: parseInt(chainHex, 16) === CHAIN_ID,
  };
}

/* ── SIWE challenge ─────────────────────────────────────────────── */

function _randomNonce(bytes = 8) {
  const arr = new Uint8Array(bytes);
  crypto.getRandomValues(arr);
  return [...arr].map((b) => b.toString(16).padStart(2, '0')).join('');
}

function _isoUtc(d = new Date()) {
  return d.toISOString().replace(/\.\d{3}Z$/, 'Z');
}

/** Build a strict EIP-4361 message bound to `address`. */
export function buildChallenge(address) {
  if (!/^0x[0-9a-fA-F]{40}$/.test(address)) {
    throw new Error('BAD_ADDRESS');
  }
  const nonce = _randomNonce(8);
  const issuedAt = _isoUtc();
  const expiresAt = _isoUtc(
    new Date(Date.now() + SIWE.challengeTtlSec * 1000),
  );
  return [
    `${SIWE.domain} wants you to sign in with your Ethereum account:`,
    address,
    '',
    SIWE.statement,
    '',
    `URI: ${SIWE.uri}`,
    'Version: 1',
    `Chain ID: ${CHAIN_ID}`,
    `Nonce: ${nonce}`,
    `Issued At: ${issuedAt}`,
    `Expiration Time: ${expiresAt}`,
  ].join('\n');
}

/** Ask the wallet to sign the SIWE message via personal_sign. */
export async function signChallenge(address, message) {
  const provider = getProvider();
  if (!provider) throw new Error('NO_WALLET');
  // personal_sign expects (hexMessage, address) on most wallets.
  const hex = '0x' + Array.from(new TextEncoder().encode(message))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('');
  return provider.request({
    method: 'personal_sign',
    params: [hex, address],
  });
}

/* ── Signature recovery (no external lib) ───────────────────────────
 * EIP-191 personal_sign verification.  We use the lightweight
 * `@noble/secp256k1` + `@noble/hashes` builds via dynamic import from
 * jsDelivr; both are < 30 KB and audited.
 *
 * The hash is keccak256("\x19Ethereum Signed Message:\n" + len + msg).
 * The recovered uncompressed pubkey → keccak256 → last 20 bytes.
 */

let _crypto = null;
async function _loadCrypto() {
  if (_crypto) return _crypto;
  const [secp, sha3, hashUtils] = await Promise.all([
    import('https://cdn.jsdelivr.net/npm/@noble/secp256k1@2.1.2/+esm'),
    import('https://cdn.jsdelivr.net/npm/@noble/hashes@1.4.0/sha3/+esm'),
    import('https://cdn.jsdelivr.net/npm/@noble/hashes@1.4.0/utils/+esm'),
  ]);
  _crypto = {
    secp: secp,
    keccak256: sha3.keccak_256,
    bytesToHex: hashUtils.bytesToHex,
    hexToBytes: hashUtils.hexToBytes,
    utf8ToBytes: hashUtils.utf8ToBytes,
  };
  return _crypto;
}

function _concatBytes(...arrs) {
  const total = arrs.reduce((n, a) => n + a.length, 0);
  const out = new Uint8Array(total);
  let off = 0;
  for (const a of arrs) {
    out.set(a, off);
    off += a.length;
  }
  return out;
}

/**
 * Recover the address that produced `signature` over EIP-191
 * personal-sign of `message`.  Returns the lowercased 0x address or
 * null if the signature is malformed.
 */
export async function recoverSigner(message, signature) {
  try {
    const { secp, keccak256, bytesToHex, hexToBytes, utf8ToBytes } =
      await _loadCrypto();

    const sigHex = signature.startsWith('0x') ? signature.slice(2) : signature;
    if (sigHex.length !== 130) return null;

    const r = sigHex.slice(0, 64);
    const s = sigHex.slice(64, 128);
    const vRaw = parseInt(sigHex.slice(128, 130), 16);
    // Some wallets return v ∈ {0,1}; secp expects recovery bit.
    let recovery;
    if (vRaw === 27 || vRaw === 28) recovery = vRaw - 27;
    else if (vRaw === 0 || vRaw === 1) recovery = vRaw;
    else return null;

    const msgBytes = utf8ToBytes(message);
    const prefix = utf8ToBytes(
      `\x19Ethereum Signed Message:\n${msgBytes.length}`,
    );
    const digest = keccak256(_concatBytes(prefix, msgBytes));

    const sig = new secp.Signature(BigInt('0x' + r), BigInt('0x' + s));
    const sigWithRecovery = sig.addRecoveryBit(recovery);
    const point = sigWithRecovery.recoverPublicKey(digest);
    // Uncompressed pubkey is 65 bytes starting with 0x04 — drop prefix.
    const pubBytes = point.toRawBytes(false).slice(1);
    const pubHash = keccak256(pubBytes);
    const addrBytes = pubHash.slice(-20);
    return '0x' + bytesToHex(addrBytes);
  } catch (err) {
    console.warn('recoverSigner failed:', err);
    return null;
  }
}

/* ── Live account/chain change subscription ─────────────────────── */

export function onAccountsChanged(cb) {
  const p = getProvider();
  if (!p?.on) return () => {};
  const handler = (accounts) => cb(accounts?.[0]?.toLowerCase() ?? null);
  p.on('accountsChanged', handler);
  return () => p.removeListener?.('accountsChanged', handler);
}

export function onChainChanged(cb) {
  const p = getProvider();
  if (!p?.on) return () => {};
  const handler = (chainHex) => cb(parseInt(chainHex, 16));
  p.on('chainChanged', handler);
  return () => p.removeListener?.('chainChanged', handler);
}
