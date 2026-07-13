# Synergix — Sovereign Ghost Node AI & Proof-of-Knowledge Protocol

Synergix is a decentralized Telegram bot powered by local LLMs and **Irys** (permanent decentralized storage on Arweave) as its only persistent database. The server holds no mutable state: every user profile, contribution, rank, and configuration lives as a DataItem on the Irys network. If the server is wiped, the node reconstructs itself entirely from Irys by querying tags.

On top of the sovereign bot sits the **Proof-of-Knowledge protocol** — a set of products that turn verified human knowledge into an economy: territorial/individual **nodes**, **Knowledge Bounties**, a learn-to-earn **Academy**, an **Atlas of Problems & Solutions**, a public **Knowledge API** that pays the humans it cites, and a portable **Passport** credential. All reasoning runs on **local LLMs**; the only external AI dependency is image generation (Fal.ai).

---

## Architecture Overview

```
Telegram User
      │
      ▼
 aiogram 3 Bot  ──── FSM (L1 RAM cache + Irys fallback)
      │
      ├── Judge 1  (Qwen2.5-1.5B-Q8 @ :8080)    — local quality scoring 0–10 (approve ≥ 5.0)
      ├── Judge 2  (Oracle Jury)                — human 🔮 Oráculo stakers vote on disputes
      ├── Judge 3  (anti_gaming.py)             — pure-code anti-gaming / Sybil / farming guard
      ├── Thinker  (Qwen2.5-7B-Instruct-Q4 @ :8081) — conversation + RAG generation
      │
      ├── irys-uploader (:8083)                 — Node.js microservice (all on-chain signing)
      │     └── @irys/upload-ethereum SDK
      │
      ├── public API (:8090)                    — read-only Knowledge API over Irys (Starlette)
      │
      ▼
 FAISS Vector Index (multilingual, 4 specialised brains)
      │
      ▼
 Irys Network (Arweave permanent storage) ─── Source of truth
      └── gateway.irys.xyz/<txId>              — public immutable URLs
```

**Ghost Protocol:** Telegram UIDs are never stored on-chain. Every UID is hashed once — `SHA-256("Synergix_" + uid)[:12]` — before touching the storage layer. The real identity behind any profile is permanently unknowable from the blockchain.

**Three Judges:** every contribution is validated by (1) a **local Qwen Judge** that scores quality 0–10 and approves at ≥ 5.0, (2) an **Oracle Jury** of human 🔮 Oráculo stakers for disputed cases, and (3) `anti_gaming.py`, a deterministic code-only guard against self-voting, Sybil rings, and reward farming.

**irys-uploader microservice:** All Irys DataItem signing is delegated to a Node.js container running `@irys/upload-ethereum`. The Python bot sends `{data: base64, tags: [...]}` via HTTP and receives the `txId`. This eliminates all Web3 cryptography from Python.

---

## Data Model (Irys Tags)

All data is stored as immutable DataItems on Irys. Records are discovered by querying the GraphQL endpoint (`https://uploader.irys.xyz/graphql`) for matching tag combinations. The most recent matching DataItem wins (newest timestamp).

| `data-type` tag | Content-Type | Description |
|-----------------|-------------|-------------|
| `user-profile` | `application/json` | Points, rank, language, trust score, daily count |
| `user-profile-pointer` | `application/json` | Latest profile DataItem per Ghost ID |
| `aporte` | `text/plain; charset=utf-8` | Contribution text + author uid + category |
| `emergency-lock` | `application/json` | Presence = all writes blocked |
| `system-config` | `application/json` | Quality thresholds, trust deltas |
| `ai-guard` | `text/plain; charset=utf-8` | Anti-jailbreak pattern list |
| `challenge` | `application/json` | Weekly challenge description |
| `brain-pointer` / `brain-pointer-global` | `application/json` | Latest FAISS index version per brain |
| `brain-index` | `application/octet-stream` | Serialised FAISS index binary |
| `brain-meta` | `application/json` | FAISS index metadata (doc count, etc.) |
| `log` | `application/gzip` | Daily compressed log archive |

**Proof-of-Knowledge & economy DataItems**

| `data-type` tag | Description |
|-----------------|-------------|
| `node` / `node-member` / `node-bond` | Territorial/individual node, its members, and its locked 20k SYNERGIX anti-spam bond |
| `bounty` / `bounty-claim` | Knowledge Bounty pool + approved claims against it |
| `problem` / `problem-confirm` | Atlas problem report + distinct confirmations (Sybil-guarded) |
| `project` / `project-fund` / `project-vote` | Community project, its funding, and votes (creator-first payout, evidence-gated release) |
| `provider` | Registered SYNX-paid service provider |
| `proposal` / `proposal-vote` | Governance proposal + votes |
| `oracle-stake` / `oracle-vote` / `oracle-review` | 100k SYNERGIX Oracle stake, jury votes, and reviews |
| `credential` / `passport` | Verified skill credential + portable Ghost-ID Passport |
| `lesson-result` | Academy lesson grade + reward record |
| `knowledge-gap` | Detected topic gap driving bounty/gap multipliers |
| `impact-counter` / `impact-royalty` | Proof of Impact Real: per-`aporte` usage counter + perpetual royalties |
| `api-key` / `api-usage` | Knowledge API access keys + paid-query settlement ledger |
| `synx-payment` / `redemption` | SYNX transfers and SYNERGIX redemptions |
| `custodial-wallet` | Sealed keystore V3 for the user's custodial wallet |

Every DataItem also receives the tag `App-Name: Synergix` for global namespace isolation. All GraphQL queries filter by `owners` (the bot wallet) so forged DataItems from other wallets are ignored.

---

## Rank System

| Rank | Min Points | Daily Limit | Multiplier |
|------|-----------|-------------|-----------|
| 🌱 Iniciado | 0 | 5 | ×1.0 |
| 📈 Activo | 100 | 12 | ×1.2 |
| 🧬 Sincronizado | 500 | 25 | ×1.5 |
| 🏗️ Arquitecto | 1,500 | 40 | ×2.0 |
| 🧠 Mente Colmena | 5,000 | 60 | ×2.5 |
| 🔮 Oráculo | 15,000 | ∞ | ×3.0 |

Points are calculated as `int(quality_score × 2)` + tier bonus (Elite ≥9.0: +8 pts, Legendary ≥9.5: +15 pts) + challenge bonus (+5 pts if on-topic). Ranks are auto-promoted on every contribution.

---

## Contribution Flow

```
User text
    │
    ├─ length check (min 20 chars)
    ├─ daily quota check (rank.daily_limit)
    ├─ duplicate check (SHA-256 hash cache, max 2000 entries)
    │
    ▼
Judge LLM (Qwen2.5-1.5B-Q8)
    ├─ quality_score  0.0 – 10.0
    ├─ approved       bool  (threshold ≥ 5.0)
    ├─ category       string
    ├─ impact_index   float
    └─ constructive_feedback  string (when rejected)
    │
    ▼
Points = int(quality × 2) + bonus
Trust score ± delta (default 5.0, clipped 0–10)
    │
    ▼
write_aporte() → POST irys-uploader:8083/upload
    Returns: txId (Irys DataItem ID)
    Public URL: https://gateway.irys.xyz/<txId>
    │
    ▼
RAG index ← add contribution (FAISS + metadata, brain routed by category)
    │
    ▼
check_and_update_rank() → write_user_tags() → Irys
    │
    ▼
Bot reply: CID = txId (displayed to user)
```

---

## Judge Content Policy

The Judge automatically rejects (quality_score = 0.0) any contribution that:

- Is spam, advertising, offensive, or irrelevant
- Contains only emojis or lacks real semantic meaning
- Is a question instead of a reflection, knowledge, or assertion
- Is fewer than 3 meaningful words
- Contains demonstrably false or dangerous information
- **References the Synergix project itself** — its mission, tokenomics, features, roadmap, pricing, team, contracts, or bot functionality (Synergix cannot immortalise itself)
- **Violates third-party privacy** — personal data, confidential information, real-world locations, identities, or doxxing in any form
- **Promotes illegal activities**, violence, hatred, discrimination, or exploitation of any person or group

---

## Proof-of-Knowledge Protocol

Six products turn verified human knowledge into a self-sustaining economy. All AI runs locally (Judge, Thinker, RAG); the only external AI call is image generation.

| Product | What it does | Reward / gate |
|---------|--------------|---------------|
| **Nodes** | Territorial or individual knowledge communities (see below) | 20k SYNERGIX anti-spam bond to create |
| **Knowledge Bounties** | Anyone funds a SYNX pool for answers on a topic; approved contributions are paid from it | pool ≥ 50 SYNX, reward ≥ 5 SYNX/claim; auto-refund on expiry |
| **Academy** | Learn-to-earn: the Thinker generates a RAG-grounded lesson, the user answers, the Judge grades it | pass ≥ 5.0 → +2 SYNX, credential + PIR; cap 5 rewarded lessons/day |
| **Atlas of Problems & Solutions** | Geolocated map of reported problems and verified solutions | active membership required; 5 reports/day cap; solved at 3 distinct confirmations → +10 SYNX |
| **Knowledge API** | Public paid endpoint answering questions from the collective brain | 1 SYNX/query, **70% paid to the humans cited**, 30% to the protocol |
| **Passport** | Portable, verifiable credential of a Ghost ID's skills and impact | issued from earned `credential` DataItems |

**Proof of Impact Real (PIR):** every `aporte` carries an `impact-counter`. Each time it is cited (RAG answer, API query, lesson) the counter increments and `impact-royalty` pays the original author perpetual royalties — knowledge keeps earning long after it is written.

---

## Token Economy

Two distinct units keep accounting and real value cleanly separated:

| Unit | Nature | Lives in |
|------|--------|----------|
| **SYNX** | Internal accounting balance (points-of-value for rewards, bounties, API, Academy) | Irys DataItems (`synx-payment`) |
| **SYNERGIX** | Real ERC-20 on BNB Chain — `0xbe5df4a40ac939ef641430e86a2dce94d071e0f6` (fee-on-transfer) | On-chain; custodial wallet 1:1 |

**Real-SYNERGIX utility**

| Mechanism | Amount | Purpose |
|-----------|--------|---------|
| Node bond | **20,000 SYNERGIX** | Locked to create a node — anti-spam, refundable after a 7-day unbond timer |
| Oracle stake | **100,000 SYNERGIX** | Locked to become a 🔮 Oráculo juror (Judge 2); +15 SYNX per correct vote, −50 SYNX penalty for wrong votes |
| Membership tiers | see below | Holding real SYNERGIX in the custodial wallet grants daily-quota bonuses and funding priority |

**Membership tiers ("Tenencia con beneficios")** — computed from the custodial-wallet SYNERGIX balance (cached 300 s):

| Tier | Min held | Daily bonus | Funding priority |
|------|----------|-------------|------------------|
| 💎 Diamante | 200,000 | +30 aportes/day | ✅ |
| 🥇 Oro | 50,000 | +15 | ✅ |
| 🥈 Plata | 10,000 | +7 | — |
| 🥉 Bronce | 1,000 | +3 | — |

Node bonds and Oracle stakes share a **single lock ledger** (`bonds.locked_synergix = sum(node bonds) + oracle stake`) so every withdraw/sell respects all active locks. Custodial wallets use keystore V3 with an HMAC-derived password, sealed to Irys; swaps run through PancakeSwap V2 `...SupportingFeeOnTransferTokens`.

---

## Territorial & Individual Nodes

Nodes can be a-geographic (thematic/global) or geolocated. Creation flow: **name → type → language → country → scope → place → topics**, with free country selection (type any country, canonised to ISO if it matches the built-in list).

| Node type | Icon | Geo | Scope asked |
|-----------|------|-----|-------------|
| `individual` | 👤 | ✅ | ✅ (personal geolocated node) |
| `barrio` | 🏘️ | ✅ | ✅ (local community) |
| `pais` | 🌍 | ✅ | country only |
| `tematico` | 📚 | — | — |
| `profesional` | 💼 | — | — |
| `global` | 🌐 | — | — |

**Geographic scopes:** 🏙️ ciudad · 🏘️ barrio · 🌾 zona_rural · 🗺️ region. Nodes and the Atlas can be filtered by country and scope, so problems and projects are discoverable exactly where they occur.

**Community projects** pay the owner/creator **first**, and no funds are released until a mandatory verification step: the creator must submit **evidence** (milestones/documentation, 20–400 chars) via `request_completion` before `project-fund` is disbursed.

---

## AI Conversation

### Streaming Response
All free-conversation messages are handled via token-streaming from Qwen2.5-7B-Instruct. The bot shows a live typing preview that updates every ~0.9 s and performs a final clean edit at stream end. Supports reasoning-model think-traces (`<think>…</think>`) transparently.

### Immortal Memory (RAG)
On every conversation turn, Synergix searches the FAISS index across 4 specialised brains in parallel:

| Brain | Categories |
|-------|-----------|
| `prog` | programacion |
| `tech` | tecnologia |
| `cien` | ciencia |
| `know` | filosofia, arte, vida, espiritualidad, economia, naturaleza, sociedad, innovacion |

- Minimum relevance threshold: **0.45** (below this, context is not injected)
- Same-language results are prioritised over cross-lingual results
- Cross-lingual fragments are annotated with `[lang]` so the model synthesises them in the user's language
- When immortal memory is used, a footer is appended to the response:
  `📜 N memorias inmortales resonaron aquí` (in the user's language)

### Emoji Responses
When a user sends an emoji-only message, Synergix responds with a single empathetic emoji reaction — no verbose explanation. The model's `[[STICKER:X]]` output token is used; if absent, emojis are extracted from the response text.

### Response Post-Processing (output pipeline)
Every response passes through:
1. `_strip_thinking` — removes `<think>…</think>` blocks (reasoning models)
2. `_strip_name_prefix` — removes `"Synergix: "` or similar name prefixes the model might add
3. `_extract_sticker` — extracts `[[STICKER:emoji]]` for Telegram sticker replies
4. `_strip_filler` — strips customer-service greeting/closing phrases

---

## Wallet Verification (BscScan)

1. User taps **Synergix → Verify Wallet**
2. Bot generates a nonce-based challenge:
   ```
   Synergix Identity Verification
   Nonce: <16 hex chars>
   Issued At: <ISO 8601>
   This signature is gasless and does not move funds.
   ```
3. Bot displays the challenge + link to **https://bscscan.com/verifiedSignatures**
4. User signs in their wallet via BscScan (MetaMask, Trust Wallet, etc.)
5. User pastes the hex signature back in the bot
6. Bot recovers the signer address via `eth_account.Account.recover_message` (ecrecover)
7. Address stored as `wallet_address` in the user-profile DataItem on Irys
8. `human_verified = true` unlocked

Challenge expires after 10 minutes. Works in all 10 supported languages.

---

## Supported Languages

| Code | Language |
|------|----------|
| `es` | Español 🇪🇸 |
| `en` | English 🇬🇧 |
| `zh` | 中文 🇨🇳 |
| `hi` | हिन्दी 🇮🇳 |
| `ar` | العربية 🇸🇦 |
| `fr` | Français 🇫🇷 |
| `bn` | বাংলা 🇧🇩 |
| `pt` | Português 🇵🇹 |
| `id` | Bahasa Indonesia 🇮🇩 |
| `ur` | اردو 🇵🇰 |

Language is persisted in the `user-profile` DataItem and auto-detected from the first message.

---

## Bot Commands & Buttons

| Trigger | Action |
|---------|--------|
| `/start` | Welcome + detect language; restore profile from Irys |
| `/admin lock` | Activate emergency lock (admin only) |
| `/admin unlock` | Deactivate emergency lock (admin only) |
| 🔥 Contribute | Enter `awaiting_contribution` FSM state |
| 📊 View Status | Display points, rank, daily quota, trust score |
| 🧠 My Memory | List last 10 contributions from Irys |
| 🌐 Language | Inline language selector (10 options) |
| 💰 Synergix | Trading menu (verify wallet, buy/sell links, balance, price, bonding curve) |
| 🏆 Top Mentes | Leaderboard top-10 (rebuilt every 10 min) |

**Admin IDs** are configured via `SYNERGIX_ADMIN_IDS` (comma-separated Telegram UIDs).

---

## AI Services

| Service | Model | Port | Role | Resources |
|---------|-------|------|------|-----------|
| Thinker | `qwen2.5-7b-instruct-q4_k_m.gguf` | 8081 | Conversations, Oracle, RAG generation | 6–12 CPU / 6–12 GB RAM |
| Judge | `qwen2.5-1.5b-q8.gguf` | 8080 | Quality scoring, contribution validation | 1–6 CPU / 2–4 GB RAM |
| irys-uploader | Node.js 20 | 8083 | Irys DataItem signing via `@irys/upload-ethereum` | 0.5 CPU / 512 MB RAM |
| api | Starlette / uvicorn | 8090 | Read-only public Knowledge API over Irys (bound to `127.0.0.1`) | 0.5 CPU / 512 MB RAM |

All LLMs run as `llama.cpp` server containers (`ghcr.io/ggml-org/llama.cpp:server`). The bot and irys-uploader communicate over the Docker internal network (`synergix-net`, subnet `172.28.0.0/16`).

**RAG Engine:** `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (384-dimensional embeddings, `faiss-cpu`, IVF-PQ index). Index partitioned into 4 brains by knowledge category. Rebuilt from Irys `aporte` DataItems on startup.

---

## Trading Integration

| Feature | Details |
|---------|---------|
| Chain | BNB Chain (BSC mainnet, chain ID 56) |
| DEX | PancakeSwap V2 |
| Price source | `getReserves()` on-chain (cache TTL 120 s) |
| Market data | DexScreener API (price, 24h change, volume, liquidity, market cap) |
| Bonding curve | Four.Meme curve manager contract (raised / target BNB, graduation %) |
| Trade execution | Deep-link to PancakeSwap — user signs in their own wallet, no custody |
| Min buy | 0.0001 BNB |
| Min sell | 0.0002 BNB equivalent |
| Slippage | 1% |

---

## Scheduled Tasks (`scripts/sync_brain.py`)

| Schedule | Task |
|----------|------|
| Daily 00:00 UTC | `reset_all_daily_counts()` — zero daily quota for all users |
| Every 10 min | `rebuild_top10()` — rescan all user DataItems, sort by points, update RAM cache |
| Hourly | `load_ai_guard()` — reload anti-jailbreak patterns from Irys |
| Startup | `load_system_config()` — load quality thresholds from Irys |

---

## Project Structure

```
Synergix/
├── aisynergix/
│   ├── ai/
│   │   ├── manager.py          # AI orchestration, contribution processing, RAG pipeline
│   │   └── local_ia.py         # LLM client wrappers (Thinker / Judge), prompts, streaming
│   ├── bot/
│   │   ├── bot.py              # aiogram 3 handlers, commands, keyboards, emoji logic
│   │   ├── fsm.py              # FSM states, L1 RAM cache (TTL 3600 s, max 500)
│   │   ├── identity.py         # UserProfile dataclass, UserCache (TTL 30 s), IdentityManager
│   │   ├── locales.py          # i18n loader
│   │   └── locales/            # JSON string tables (10 languages)
│   ├── nodes/
│   │   ├── node_manager.py     # Node dataclass, NODE_TYPES, TOPICS, create_node (+bond lock)
│   │   └── geo.py              # Countries, scopes, free country selection, geo filtering
│   ├── api.py                  # Starlette read-only public Knowledge API (:8090)
│   └── services/
│       ├── irys.py             # Primary storage layer: Irys DataItem read/write
│       ├── rag_engine.py       # FAISS index + sentence-transformers, 4-brain architecture
│       ├── wallet_verify.py    # BscScan nonce challenge + ecrecover verification
│       ├── custody.py          # Custodial wallets (keystore V3), PancakeSwap V2 swaps
│       ├── bonds.py            # Unified SYNERGIX lock ledger (node bonds + oracle stakes)
│       ├── oracle.py           # 🔮 Oráculo staking (100k) + jury voting (Judge 2)
│       ├── tiers.py            # Membership tiers by real SYNERGIX held
│       ├── bounties.py         # Knowledge Bounties (pool + claims + lazy expiry refund)
│       ├── academy.py          # Learn-to-earn lessons, grading, credentials, PIR
│       ├── problems.py         # Atlas of Problems & Solutions (membership-gated)
│       ├── projects.py         # Community projects (creator-first, evidence-gated)
│       ├── governance.py       # Proposals + votes
│       ├── impact.py           # Proof of Impact Real: counters + royalties
│       ├── knowledge_api.py    # Paid Q&A engine (70% to cited authors)
│       ├── trading.py          # PancakeSwap V2 read-only + deep-links
│       ├── dexscreener.py      # DexScreener market data API
│       └── four_meme.py        # Bonding curve progress
├── irys-uploader/
│   ├── server.js               # Express HTTP server wrapping @irys/upload-ethereum
│   ├── package.json            # Node.js 20 deps: @irys/upload, @irys/upload-ethereum, express
│   └── Dockerfile              # node:20-bookworm-slim, non-root user
├── scripts/
│   ├── sync_brain.py           # APScheduler cron daemon
│   ├── fusion_brain.py         # Offline brain aggregation utility
│   └── irys_fund.py            # Utility to fund the Irys node wallet
├── docker/
│   ├── Dockerfile              # Python bot image
│   └── docker-compose.yml      # 4 services: thinker, judge, irys-uploader, bot
├── .env.example
└── requirements.txt
```

---

## Environment Variables

### Required

| Variable | Description |
|----------|-------------|
| `TELEGRAM_TOKEN` | Bot token from BotFather |
| `PRIVATE_KEY` | ECDSA private key (hex, with or without `0x`) used to sign Irys DataItems |

### Irys Storage

| Variable | Default | Description |
|----------|---------|-------------|
| `IRYS_NODE_URL` | `https://uploader.irys.xyz` | Irys upload node endpoint |
| `IRYS_GATEWAY_URL` | `https://gateway.irys.xyz` | Irys public read gateway |
| `IRYS_TOKEN` | `bnb` | Token used to pay for Irys uploads (`bnb` or `ethereum`) |
| `IRYS_UPLOADER_URL` | `http://irys-uploader:8083` | Internal URL of the irys-uploader microservice |

### AI Infrastructure

| Variable | Default | Description |
|----------|---------|-------------|
| `THINKER_HOST` | `http://thinker:8081` | Thinker LLM endpoint |
| `JUDGE_HOST` | `http://judge:8080` | Judge LLM endpoint |
| `THINKER_MAX_CONCURRENCY` | `1` | Concurrent Thinker calls (CPU runs llama.cpp `--parallel 1`) |
| `FAL_KEY` | _(empty)_ | Fal.ai API key `<id>:<secret>` |
| `FAL_MODEL` | `fal-ai/flux/schnell` | Fal model id |
| `IMAGE_GEN_ENABLED` | `true` | Master switch for in-chat image generation |
| `IMAGE_MAX_CONCURRENCY` | `1` | Concurrent images allowed |
| `IMAGE_COOLDOWN_SECONDS` | `120` | Min seconds between images per user |
| `IMAGE_DAILY_LIMIT` | `10` | Max images per user per day |
| `WEB_SEARCH_ENABLED` | `true` | Web fallback when immortal memory has no answer |
| `WEB_SEARCH_PROVIDER` | `searxng` | `searxng` (self-hosted), `brave`, or `duckduckgo` |
| `SEARXNG_URL` | `http://searxng:8080` | SearXNG endpoint (searxng provider) |
| `BRAVE_API_KEY` | _(empty)_ | Brave Search API key (brave provider) |
| `SYNERGIX_GROUP_KEYWORD` | `syn` | Trigger word (whole word, case-insensitive) for group replies |
| `SYNERGIX_GROUP_LANG` | `en` | Language the bot speaks in groups |
| `SYNERGIX_GROUP_WHITELIST` | _(empty)_ | Group IDs where the bot may answer; empty = any |
| `SYNERGIX_GROUP_COOLDOWN` | `5` | Min seconds between answers to the same user in a group |

#### Group chats

When added to a group, the bot answers **only** when a message contains the
trigger word `SYNERGIX_GROUP_KEYWORD` (default `syn`, matched as a whole word,
case-insensitive) or replies to one of the bot's messages; any other message is
ignored (absolute silence). Group replies use the Thinker +
immortal-memory RAG, but are **stateless** and never touch the points / ranks /
contributions system — that stays DM-only. Replies mirror the input type: an
emoji gets an emoji back, a sticker gets a sticker (from the same pack), and text
gets a text reply (in `SYNERGIX_GROUP_LANG`, may include emojis). Restrict which groups it serves with
`SYNERGIX_GROUP_WHITELIST`. Keep **Privacy Mode ON** in BotFather so Telegram only
delivers mentions, replies and commands to the bot.

### Application

| Variable | Default | Description |
|----------|---------|-------------|
| `SYNERGIX_ADMIN_IDS` | — | Comma-separated Telegram UIDs for `/admin` commands |
| `SYNERGIX_CACHE_TTL` | `12` | Hours for miscellaneous cache TTL |
| `BSC_RPC_URL` | `https://bsc-dataseed1.binance.org` | BSC RPC for on-chain price reads |
| `SYNERGIX_NODE_BOND` | `20000` | Real SYNERGIX locked to create a node (anti-spam) |
| `SYNERGIX_ORACLE_STAKE` | `100000` | Real SYNERGIX locked to become a 🔮 Oráculo juror |
| `SYNERGIX_UNBOND_DAYS` | `7` | Unbonding timer before a released node bond returns |

---

## Deployment

### Prerequisites

- Docker + Docker Compose
- GGUF model files placed in `aisynergix/ai/models/`:
  - `qwen2.5-7b-instruct-q4_k_m.gguf` (Thinker)
  - `qwen2.5-1.5b-q8.gguf` (Judge)
- Image generation runs through the managed **Fal.ai** API — see
  [Image generation (Fal.ai)](#image-generation-falai) below.
- A BNB wallet with enough BNB on Irys to cover uploads (`scripts/irys_fund.py`)

### Steps

```bash
# 1. Clone the repository
git clone https://github.com/synergixia/Synergix.git
cd Synergix

# 2. Create .env from example
cp .env.example .env
# Fill in: TELEGRAM_TOKEN, PRIVATE_KEY, FAL_KEY

# 3. Fund the Irys node wallet (one-time setup)
python scripts/irys_fund.py

# 4. Build and start all services (image-gen is remote, so it is NOT started)
cd docker
docker compose up -d --build

# 5. Check logs
docker compose logs -f bot
```

### Image generation (Fal.ai)

Images are generated through **[Fal.ai](https://fal.ai)** — a managed inference
API, so there is nothing to host: no GPU, no Docker worker, no cold starts. The
bot calls `https://fal.run/<model>` with your Fal key and downloads the result.

1. Create a key at <https://fal.ai/dashboard/keys>.
2. Set it in the host `.env`:
   ```
   FAL_KEY=<id>:<secret>
   # FAL_MODEL=fal-ai/fast-sdxl   # default; also fal-ai/flux/schnell, fal-ai/flux/dev
   ```
   then `docker compose up -d bot`.

Quick check (replace key):
```bash
curl -s https://fal.run/fal-ai/fast-sdxl \
  -H "Authorization: Key <id>:<secret>" \
  -H "Content-Type: application/json" \
  -d '{"prompt":"a neon dragon over a city"}'
# -> {"images":[{"url":"https://..."}], ...}
```

### Self-Healing on Restart

On startup the bot:
1. Connects to `irys-uploader` and verifies the wallet address and Irys balance
2. Loads `system-config` DataItem from Irys (quality thresholds)
3. Loads `ai-guard` DataItem from Irys (anti-jailbreak patterns)
4. Checks `emergency-lock` presence on Irys
5. Rebuilds the FAISS index from all `aporte` DataItems on Irys (4 brains)
6. Health-checks Thinker and Judge LLMs

If Irys is unreachable during startup, the bot falls back to in-memory defaults and retries on the next scheduled cycle.

---

## irys-uploader Microservice

A lightweight Express server (`irys-uploader/server.js`) that wraps the official `@irys/upload-ethereum` SDK. The Python bot never handles private keys for Irys — all DataItem signing happens here.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Returns `{status, address, token, node}` |
| `/balance` | GET | Returns atomic balance on the Irys node |
| `/upload` | POST | `{data: <base64>, tags: [{name, value}]}` → `{id, timestamp, size}` |

Every successful upload logs `TxID generado: <id>` to stdout. The Python layer logs the full gateway URL: `https://gateway.irys.xyz/<txId>`.

---

## Public Knowledge API

A read-only Starlette service (`aisynergix/api.py`, `uvicorn aisynergix.api:app --port 8090`) exposes the collective brain over HTTP. Everything it serves is already public on Arweave; it simply makes it queryable without touching GraphQL. Bound to `127.0.0.1` behind a reverse proxy.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/health` | GET | Service liveness |
| `/api/stats` | GET | Global network stats |
| `/api/top10` | GET | Leaderboard |
| `/api/nodes` | GET | All nodes (filterable by country/scope) |
| `/api/nodes/{node_id}` | GET | Node detail + knowledge map |
| `/api/nodes/{node_id}/bounties` | GET | Open bounties for a node |
| `/api/nodes/{node_id}/problems` | GET | Atlas problems for a node |
| `/api/atlas` | GET | Global problems & solutions map |
| `/api/ask` | POST | Paid Q&A — 1 SYNX/query, **70% to the humans cited** |
| `/api/passport/{ghost_id}` | GET | Portable credential for a Ghost ID |
| `/api/impact/{aporte_tx}` | GET | Proof of Impact Real for a contribution |

---

## Key Design Decisions

| Constraint | Solution |
|-----------|----------|
| Web3 crypto unreliable in Python | Delegated to Node.js `irys-uploader` using the official SDK |
| Immutable storage = no updates | Latest DataItem with matching tags wins; old records are superseded, not deleted |
| Irys `Content-Type` tag is case-sensitive | All uploads use the exact casing `Content-Type` |
| `follow_redirects` on gateway reads | httpx client configured with `follow_redirects=True` (gateway responds 302) |
| Wallet address recovery without user input | nonce-based challenge + `ecrecover` from BscScan signature |
| UID privacy | SHA-256 hash prefix used everywhere; raw Telegram UID never stored |
| FAISS index persistence | Serialised binary uploaded to Irys as `brain-index` DataItem; rebuilt from it on startup |
| Model name prefix in responses | Post-processing pipeline strips `"Synergix: "` prefix that small LLMs emit |
| Cross-lingual RAG | Same-language results ranked first; cross-lingual fragments annotated with `[lang]` tag |
| SYNX vs SYNERGIX | Internal accounting (SYNX in Irys) kept separate from the real ERC-20 (SYNERGIX on BSC); custodial wallet bridges 1:1 |
| Node/oracle locks must never double-spend | Single `bonds.locked_synergix` ledger sums node bonds + oracle stakes; every withdraw/sell respects it |
| No external AI except images | Judge/Thinker/RAG all run locally; only image generation calls out (Fal.ai) |
| Atlas Sybil farming | Confirmations/solutions require active node membership and are idempotent; reports capped at 5/day |
| Bounty escrow stuck on expiry | `list_bounties` lazily expires + auto-refunds pools past their deadline |
| Knowledge API compute DoS | API key validated and question length capped (500) **before** any RAG compute |
| Funds released without proof | Community-project payout is creator-first and gated on mandatory evidence (20–400 chars) |
