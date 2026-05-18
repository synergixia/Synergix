# Synergix — Sovereign Ghost Node AI

Synergix is a decentralized Telegram bot that uses **Irys** (permanent decentralized storage on Arweave) as its only persistent database. The local server holds no mutable state: every user profile, contribution, rank, and configuration lives as a DataItem on the Irys network. If the server is wiped, the node reconstructs itself entirely from Irys by querying tags.

---

## Architecture Overview

```
Telegram User
      │
      ▼
 aiogram 3 Bot  ──── FSM (L1 RAM cache + Irys fallback)
      │
      ├── Judge (Qwen3-0.6B @ :8080)      — quality scoring 0–10
      ├── Thinker (Qwen3-1.7B @ :8081)   — conversation + RAG
      └── Programmer (StarCoder2-3B @ :8082) — code generation
      │
      ├── irys-uploader (:8083)           — Node.js microservice
      │     └── @irys/upload-ethereum SDK — handles all on-chain signing
      │
      ▼
 FAISS Vector Index (multilingual, rebuilt from Irys on startup)
      │
      ▼
 Irys Network (Arweave permanent storage) ─── Source of truth
      └── gateway.irys.xyz/<txId>         — public immutable URLs
```

**Ghost Protocol:** Telegram UIDs are never stored on-chain. Every UID is hashed once — `SHA-256("Synergix_" + uid)[:12]` — before touching the storage layer. The real identity behind any profile is permanently unknowable from the blockchain.

**irys-uploader microservice:** All Irys DataItem signing is delegated to a Node.js container running `@irys/upload-ethereum`. The Python bot sends `{data: base64, tags: [...]}` via HTTP and receives the `txId`. This eliminates all Web3 cryptography from Python.

---

## Data Model (Irys Tags)

All data is stored as immutable DataItems on Irys. Records are discovered by querying the GraphQL endpoint (`https://uploader.irys.xyz/graphql`) for matching tag combinations. The most recent matching DataItem wins (newest timestamp).

| `data-type` tag | Content-Type | Description |
|-----------------|-------------|-------------|
| `user-profile` | `application/json` | Points, rank, language, trust score, daily count |
| `aporte` | `text/plain; charset=utf-8` | Contribution text + author uid + category |
| `emergency-lock` | `application/json` | Presence = all writes blocked |
| `system-config` | `application/json` | Quality thresholds, trust deltas |
| `ai-guard` | `text/plain; charset=utf-8` | Anti-jailbreak pattern list |
| `challenge` | `application/json` | Weekly challenge description |
| `brain-pointer` | `application/json` | Latest FAISS index version per LLM |
| `brain-pointer-global` | `application/json` | Global FAISS pointer |
| `brain-index` | `application/octet-stream` | Serialised FAISS index binary |
| `brain-meta` | `application/json` | FAISS index metadata (doc count, etc.) |
| `log` | `application/gzip` | Daily compressed log archive |

Every DataItem also receives the tag `App-Name: Synergix` for global namespace isolation.

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
    ├─ duplicate check (FAISS cosine similarity)
    │
    ▼
Judge LLM (Qwen3-0.6B)
    ├─ quality_score  0.0 – 10.0
    ├─ approved       bool
    ├─ category       string
    └─ impact_index   float
    │
    ▼
Points = int(quality × 2) + bonus
Trust score ± delta (5.0 default, clipped 0–10)
    │
    ▼
write_aporte() → POST irys-uploader:8083/upload
    Returns: txId (Irys DataItem ID)
    Public URL: https://gateway.irys.xyz/<txId>
    │
    ▼
RAG index ← add contribution (FAISS + metadata)
    │
    ▼
check_and_update_rank() → write_user_tags() → Irys
    │
    ▼
Bot reply: CID = txId (displayed to user)
```

---

## Wallet Verification (BscScan)

1. User taps **Synergix → Verify Wallet**
2. Bot generates a nonce-based challenge (no address required upfront):
   ```
   Synergix Identity Verification
   Nonce: <16 hex chars>
   Issued At: <ISO 8601>
   This signature is gasless and does not move funds.
   ```
3. Bot displays the challenge + link to **https://bscscan.com/verifiedSignatures**
4. User signs the message in their wallet via BscScan (MetaMask, Trust Wallet, etc.)
5. User pastes the hex signature back in the bot
6. Bot recovers the signer address via `eth_account.Account.recover_message` (ecrecover)
7. Address stored as `wallet_address` field in the user-profile DataItem on Irys
8. `human_verified = true` — unlocks Programmer mode

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
| 👨‍💻 Programmer | Code generation mode (requires verified wallet) |
| 💰 Synergix | Trading menu (verify wallet, buy/sell links, balance, price, bonding curve) |
| 🏆 Top Mentes | Leaderboard top-10 (rebuilt every 10 min) |

**Admin IDs** are configured via `SYNERGIX_ADMIN_IDS` (comma-separated Telegram UIDs).

---

## AI Services

| Service | Model | Port | Role | Resources |
|---------|-------|------|------|-----------|
| Thinker | `qwen3-1.7b.gguf` | 8081 | Conversations, Oracle, RAG generation | 2 CPU / 1.8 GB RAM |
| Judge | `qwen3-0.6b.gguf` | 8080 | Quality scoring, contribution validation | 1 CPU / 0.8 GB RAM |
| Programmer | `starcoder2-3b.gguf` | 8082 | Code generation (verified users) | 2 CPU / 2.5 GB RAM |
| irys-uploader | Node.js 20 | 8083 | Irys DataItem signing via `@irys/upload-ethereum` | 0.5 CPU / 512 MB RAM |

All LLMs run as `llama.cpp` server containers. The bot and irys-uploader communicate over the Docker internal network (`synergix-net`, subnet `172.28.0.0/16`).

**RAG Engine:** `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (384-dimensional embeddings, `faiss-cpu`). Index rebuilt from all Irys `aporte` DataItems on startup. Duplicate detection threshold and semantic search run against the same FAISS index.

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
│   │   ├── manager.py          # AI orchestration, contribution processing
│   │   └── local_ia.py         # LLM client wrappers (thinker / judge / programmer)
│   ├── bot/
│   │   ├── bot.py              # aiogram 3 handlers, commands, keyboards
│   │   ├── fsm.py              # FSM states, L1 RAM cache (TTL 3600 s, max 500)
│   │   ├── identity.py         # UserProfile dataclass, UserCache (TTL 30 s), IdentityManager
│   │   ├── locales.py          # i18n loader
│   │   └── locales/            # JSON string tables (10 languages)
│   └── services/
│       ├── irys.py             # Primary storage layer: Irys DataItem read/write via irys-uploader
│       ├── greenfield.py       # Legacy Greenfield SDK wrapper (retained for reference)
│       ├── rag_engine.py       # FAISS index + sentence-transformers
│       ├── wallet_verify.py    # BscScan nonce challenge + ecrecover verification
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
│   └── docker-compose.yml      # All 5 services: thinker, judge, programmer, irys-uploader, bot
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
| `SYNERGIX_THINKER_HOST` | `http://thinker:8081` | Thinker LLM endpoint |
| `SYNERGIX_JUDGE_HOST` | `http://judge:8080` | Judge LLM endpoint |
| `SYNERGIX_PROGRAMMER_HOST` | `http://programmer:8082` | Programmer LLM endpoint |

### Application

| Variable | Default | Description |
|----------|---------|-------------|
| `SYNERGIX_ADMIN_IDS` | — | Comma-separated Telegram UIDs for `/admin` commands |
| `SYNERGIX_SIWE_DOMAIN` | `synergix.bot` | Domain for SIWE-style challenges |
| `SYNERGIX_SIWE_URI` | `https://synergix.bot` | URI for SIWE-style challenges |
| `SYNERGIX_SIWE_CHAIN_ID` | `56` | Chain ID for challenges (BSC = 56) |
| `SYNERGIX_CACHE_TTL` | `12` | Hours for miscellaneous cache TTL |
| `BSC_RPC_URL` | `https://bsc-dataseed1.binance.org` | BSC RPC for on-chain price reads |

---

## Deployment

### Prerequisites

- Docker + Docker Compose
- GGUF model files placed in `aisynergix/ai/models/`:
  - `qwen3-1.7b.gguf` (Thinker)
  - `qwen3-0.6b.gguf` (Judge)
  - `starcoder2-3b.gguf` (Programmer)
- A BNB wallet with enough BNB on Irys to cover uploads (`scripts/irys_fund.py`)

### Steps

```bash
# 1. Clone the repository
git clone https://github.com/synergixia/Synergix.git
cd Synergix

# 2. Create .env from example
cp .env.example .env
# Fill in: TELEGRAM_TOKEN, PRIVATE_KEY

# 3. Fund the Irys node wallet (one-time setup)
python scripts/irys_fund.py

# 4. Build and start all services
cd docker
docker-compose up -d --build

# 5. Check logs
docker-compose logs -f bot
docker-compose logs -f irys-uploader
```

### Self-Healing on Restart

On startup the bot:
1. Connects to `irys-uploader` and verifies the wallet address and Irys balance
2. Loads `system-config` DataItem from Irys (quality thresholds)
3. Loads `ai-guard` DataItem from Irys (anti-jailbreak patterns)
4. Checks `emergency-lock` presence on Irys
5. Rebuilds the FAISS index from all `aporte` DataItems on Irys
6. Health-checks thinker, judge, and programmer LLMs

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
