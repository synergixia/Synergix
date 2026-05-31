/**
 * synergix-irys.js
 *
 * Read-only client for the Irys Data Ledger that powers the Synergix
 * Immortal Memory.  Mirrors the GraphQL queries used by the bot's
 * `aisynergix/services/irys.py`, but everything here runs in the
 * browser against the public gateway.  No secrets, no signing.
 *
 * Public surface:
 *   listAportes(opts)           → recent contributions feed
 *   listProfiles(opts)          → user-profile snapshots
 *   computeTopMinds(opts)       → top contributors leaderboard
 *   fetchAporteText(txId)       → raw aporte body
 *   getApp NameStats()          → totals for the live counters
 */

import { SYNERGIX_CONFIG, gw } from './synergix-config.js';

const { IRYS_GRAPHQL, IRYS_APP_NAME, IRYS_OWNER, TAGS } = SYNERGIX_CONFIG;

const IRYS_PAGE_MAX = 1000;

/* ── GraphQL helpers ────────────────────────────────────────────── */

function _tagFilterFragment(tags) {
  return tags
    .map(
      ({ name, values }) =>
        `{name: "${name}", values: [${values.map((v) => `"${v}"`).join(',')}]}`,
    )
    .join(', ');
}

function _ownerFragment() {
  return IRYS_OWNER ? `, owners: ["${IRYS_OWNER}"]` : '';
}

async function gql(query, { signal } = {}) {
  const res = await fetch(IRYS_GRAPHQL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query }),
    signal,
  });
  if (!res.ok) {
    throw new Error(`Irys GraphQL HTTP ${res.status}`);
  }
  const json = await res.json();
  if (json.errors?.length) {
    // Don't throw — Irys returns partial data with errors sometimes.
    console.warn('Irys GraphQL errors:', json.errors);
  }
  return json.data ?? {};
}

function nodeTags(node) {
  const out = {};
  for (const t of node?.tags ?? []) out[t.name] = t.value;
  return out;
}

/* ── Pagination wrapper ─────────────────────────────────────────── */

async function queryAll(tagPairs, { limit = 100, signal } = {}) {
  const tagFilter = _tagFilterFragment([
    { name: 'App-Name', values: [IRYS_APP_NAME] },
    ...tagPairs,
  ]);

  const nodes = [];
  let after = null;
  let remaining = Math.max(0, Number(limit) | 0);

  while (remaining > 0) {
    const pageSize = Math.min(remaining, IRYS_PAGE_MAX);
    const afterClause = after ? `, after: "${after}"` : '';
    const q = `
      {
        transactions(
          tags: [${tagFilter}]${_ownerFragment()},
          first: ${pageSize}${afterClause}
        ) {
          pageInfo { hasNextPage }
          edges {
            cursor
            node { id address timestamp tags { name value } }
          }
        }
      }
    `;
    let data;
    try {
      data = await gql(q, { signal });
    } catch (err) {
      console.warn('Irys queryAll failed:', err);
      break;
    }
    const tx = data?.transactions ?? {};
    const edges = tx.edges ?? [];
    if (edges.length === 0) break;

    for (const edge of edges) {
      if (edge?.node) nodes.push(edge.node);
    }
    remaining -= edges.length;
    const lastCursor = edges[edges.length - 1]?.cursor;
    if (!tx.pageInfo?.hasNextPage || !lastCursor || edges.length < pageSize) {
      break;
    }
    after = lastCursor;
  }

  // Irys returns DESC by block by default, but timestamps are the
  // authoritative ordering for our UI.
  nodes.sort((a, b) => (b.timestamp ?? 0) - (a.timestamp ?? 0));
  return nodes;
}

/* ── Public API: aportes ────────────────────────────────────────── */

/**
 * Recent contributions (aportes) feed.
 *
 * @param {object} opts
 * @param {number} [opts.limit=20]   max items (capped at 200)
 * @param {string} [opts.category]   filter to a single category
 * @param {string} [opts.language]   filter by language code
 * @param {AbortSignal} [opts.signal]
 * @returns {Promise<Array<Aporte>>}
 */
export async function listAportes({
  limit = 20,
  category,
  language,
  signal,
} = {}) {
  const tags = [{ name: 'data-type', values: [TAGS.APORTE] }];
  if (category) tags.push({ name: 'category', values: [category] });
  if (language) tags.push({ name: 'language', values: [language] });

  const nodes = await queryAll(tags, { limit: Math.min(limit, 200), signal });
  return nodes.map((node) => {
    const t = nodeTags(node);
    return {
      txId: node.id,
      gatewayUrl: gw(node.id),
      timestamp: Number(node.timestamp ?? 0),
      author: t['uid-hash'] ?? '',
      category: t.category ?? 'filosofia',
      language: t.language ?? t.lang ?? 'es',
      qualityScore: Number(t['quality-score'] ?? 0),
      impact: Number(t.impact ?? 0),
      contentSummary: t['content-summary'] ?? '',
      signatureWallet: t.signature ?? null,
    };
  });
}

/** Fetch raw aporte text from the public gateway (UTF-8 plain text). */
export async function fetchAporteText(txId, { signal } = {}) {
  const res = await fetch(gw(txId), { signal });
  if (!res.ok) throw new Error(`Gateway HTTP ${res.status}`);
  return res.text();
}

/* ── Public API: profiles + leaderboard ─────────────────────────── */

/**
 * List user-profile snapshots, deduped by uid-hash (latest wins).
 */
export async function listProfiles({ limit = 1000, signal } = {}) {
  const nodes = await queryAll(
    [{ name: 'data-type', values: [TAGS.USER_PROFILE] }],
    { limit, signal },
  );
  const seen = new Map();
  for (const node of nodes) {
    const t = nodeTags(node);
    const uid = t['uid-hash'];
    if (!uid || seen.has(uid)) continue;
    seen.set(uid, {
      uidHash: uid,
      points: Number(t.points ?? 0) || 0,
      rank: t.rank ?? '🌱 Iniciado',
      contributionCount: Number(t['contribution-count'] ?? 0) || 0,
      totalUsesCount: Number(t['total-uses-count'] ?? 0) || 0,
      language: t.language ?? 'es',
      lastSeenTs: Number(t['last-seen-ts'] ?? 0) || 0,
      walletAddress: t['wallet-address'] ?? null,
      humanVerified: t['human-verified'] === 'true',
      lastTx: node.id,
    });
  }
  return [...seen.values()];
}

/**
 * Top contributors with on-chain aporte counts as the authoritative
 * metric (matches the `compute_top10` logic in the bot).
 */
export async function computeTopMinds({ topN = 10, signal } = {}) {
  const [profiles, aportes] = await Promise.all([
    listProfiles({ limit: 1000, signal }),
    queryAll([{ name: 'data-type', values: [TAGS.APORTE] }], {
      limit: 5000,
      signal,
    }),
  ]);

  const aporteCount = new Map();
  for (const node of aportes) {
    const uid = nodeTags(node)['uid-hash'];
    if (uid) aporteCount.set(uid, (aporteCount.get(uid) ?? 0) + 1);
  }

  const enriched = profiles.map((p) => ({
    ...p,
    contributionCount: Math.max(
      p.contributionCount,
      aporteCount.get(p.uidHash) ?? 0,
    ),
  }));
  enriched.sort((a, b) => b.points - a.points);
  return enriched.slice(0, topN);
}

/* ── Public API: aggregate stats for the hero ───────────────────── */

/**
 * Total counts for the Immortal Memory.  Cheap aggregation of head
 * counts only — useful for the live counters in the hero.
 */
export async function getMemoryStats({ signal } = {}) {
  // pageInfo + a single small page is enough to know if there is
  // anything; for the count we paginate aggressively but cap at 5k.
  const aportes = await queryAll(
    [{ name: 'data-type', values: [TAGS.APORTE] }],
    { limit: 5000, signal },
  );
  const profiles = await queryAll(
    [{ name: 'data-type', values: [TAGS.USER_PROFILE] }],
    { limit: 1000, signal },
  );
  const uniqUsers = new Set();
  for (const node of profiles) {
    const uid = nodeTags(node)['uid-hash'];
    if (uid) uniqUsers.add(uid);
  }

  // Most recent aporte timestamp = "last brain heartbeat"
  const lastTs = aportes[0]?.timestamp ?? 0;

  return {
    totalAportes: aportes.length,
    totalUsers: uniqUsers.size,
    lastUpdateTs: lastTs,
  };
}

/* ── Type hint for editors ──────────────────────────────────────── */
/**
 * @typedef {Object} Aporte
 * @property {string} txId
 * @property {string} gatewayUrl
 * @property {number} timestamp
 * @property {string} author        uid-hash (privacy-preserving)
 * @property {string} category
 * @property {string} language
 * @property {number} qualityScore  0–10
 * @property {number} impact        0–1
 * @property {string} contentSummary
 * @property {string|null} signatureWallet
 */
