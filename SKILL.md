---
name: synergix
description: >
  Use this skill whenever working with the Synergix project: a decentralized Telegram bot with
  permanent storage on Irys (Arweave). Triggers include: adding or modifying bot commands/handlers,
  working with the Irys storage layer, changing the AI pipeline (Judge/Thinker/Programmer LLMs),
  editing the RAG engine or FAISS index, modifying the rank/points system, adding languages,
  changing wallet verification logic, debugging Docker/docker-compose deployments, or any task
  involving the irys-uploader microservice. Also use when writing new scheduled tasks, modifying
  the FSM state machine, or updating environment configuration.
---

# Synergix — Sovereign Ghost Node AI

Synergix is a **decentralized Telegram bot** whose entire persistent state lives on
**Irys (Arweave permanent storage)**. The server holds no mutable database: every user profile,
contribution, rank, config, and FAISS index snapshot is a DataItem on Irys. A wiped server
reconstructs itself fully from Irys on the next startup.

---

## Architecture at a Glance

```
Telegram User
      │
      ▼
 aiogram 3 Bot  ──── FSM (L1 RAM cache TTL 3600s, max 500 entries + Irys fallback)
      │
      ├── Judge     (Qwen3-0.6B  @ :8080)  — quality scoring 0–10
      ├── Thinker   (Qwen3-1.7B  @ :8081)  — conversation + RAG responses
      └── Programmer(StarCoder2-3B @ :8082) — code generation (verified users only)
      │
      ├── irys-uploader (:8083)  — Node.js 20 microservice
      │     └── @irys/upload-ethereum SDK — all on-chain signing
      │
      ▼
 FAISS Vector Index (multilingual, rebuilt from Irys on startup)
      │
      ▼
 Irys Network (Arweave permanent storage) — single source of truth
      └── gateway.irys.xyz/<txId>  — public immutable URLs
```

**Ghost Protocol:** Telegram UIDs are never stored on-chain.
Hash used everywhere: `SHA-256("Synergix_" + uid)[:12]`

---

## Project Structure

```
Synergix/
├── aisynergix/
│   ├── ai/
│   │   ├── manager.py       # AI orchestration, contribution processing pipeline
│   │   └── local_ia.py      # LLM client wrappers (thinker / judge / programmer)
│   ├── bot/
│   │   ├── bot.py           # aiogram 3 handlers, commands, inline keyboards
│   │   ├── fsm.py           # FSM states + L1 RAM cache (TTL 3600s, max 500)
│   │   ├── identity.py      # UserProfile dataclass, UserCache (TTL 30s), IdentityManager
│   │   ├── locales.py       # i18n loader
│   │   └── locales/         # JSON string tables — 10 languages
│   └── services/
│       ├── irys.py          # Primary storage: Irys DataItem read/write via irys-uploader
│       ├── greenfield.py    # Legacy BNB Greenfield wrapper (reference only)
│       ├── rag_engine.py    # FAISS index + sentence-transformers
│       ├── wallet_verify.py # BscScan nonce challenge + ecrecover verification
│       ├── trading.py       # PancakeSwap V2 read-only + deep-links
│       ├── dexscreener.py   # DexScreener market data API
│       └── four_meme.py     # Bonding curve progress (Four.Meme)
├── irys-uploader/
│   ├── server.js            # Express HTTP server wrapping @irys/upload-ethereum
│   ├── package.json         # Node.js 20 deps
│   └── Dockerfile           # node:20-bookworm-slim, non-root user
├── scripts/
│   ├── sync_brain.py        # APScheduler cron daemon (daily reset, top10, ai-guard reload)
│   ├── fusion_brain.py      # Offline brain aggregation utility
│   └── irys_fund.py         # Fund the Irys node wallet (one-time setup)
├── docker/
│   ├── Dockerfile           # Python bot image
│   └── docker-compose.yml   # 5 services: thinker, judge, programmer, irys-uploader, bot
├── .env.example
└── requirements.txt
```

---

## Irys Data Model

All state is stored as **immutable DataItems** on Irys. Latest matching DataItem wins (newest
timestamp). Every DataItem has the tag `App-Name: Synergix`.

| `data-type` tag       | Content-Type                    | Description                                      |
|-----------------------|---------------------------------|--------------------------------------------------|
| `user-profile`        | `application/json`              | Points, rank, language, trust score, daily count |
| `aporte`              | `text/plain; charset=utf-8`     | Contribution text + author UID hash + category   |
| `emergency-lock`      | `application/json`              | Presence = all writes blocked                    |
| `system-config`       | `application/json`              | Quality thresholds, trust deltas                 |
| `ai-guard`            | `text/plain; charset=utf-8`     | Adversarial prompt filter patterns               |
| `challenge`           | `application/json`              | Weekly challenge description                     |
| `brain-pointer`       | `application/json`              | Latest FAISS index version per LLM               |
| `brain-pointer-global`| `application/json`              | Global FAISS pointer                             |
| `brain-index`         | `application/octet-stream`      | Serialised FAISS index binary                    |
| `brain-meta`          | `application/json`              | FAISS index metadata (doc count, etc.)           |
| `log`                 | `application/gzip`              | Daily compressed log archive                     |

**Critical:** `Content-Type` tag is **case-sensitive** on Irys. Always use exact casing.

Query endpoint: `https://uploader.irys.xyz/graphql`

---

## irys-uploader Microservice

The Python bot **never handles private keys for Irys**. All DataItem signing is delegated to
the Node.js microservice at `:8083`.

| Endpoint  | Method | Description                                               |
|-----------|--------|-----------------------------------------------------------|
| `/health` | GET    | Returns `{status, address, token, node}`                  |
| `/balance`| GET    | Returns atomic balance on the Irys node                   |
| `/upload` | POST   | `{data: <base64>, tags: [{name, value}]}` → `{id, timestamp, size}` |

Python calls this via `httpx` with `follow_redirects=True` (gateway responds 302).

Public URL pattern after upload: `https://gateway.irys.xyz/<txId>`

---

## AI Services

| Service    | Model                | Port  | Role                                          | Resources          |
|------------|----------------------|-------|-----------------------------------------------|--------------------|
| Thinker    | `qwen3-1.7b.gguf`    | 8081  | Conversations, Oracle, RAG generation         | 2 CPU / 1.8 GB RAM |
| Judge      | `qwen3-0.6b.gguf`    | 8080  | Quality scoring, contribution validation      | 1 CPU / 0.8 GB RAM |
| Programmer | `starcoder2-3b.gguf` | 8082  | Code generation (verified users only)         | 2 CPU / 2.5 GB RAM |
| irys-uploader | Node.js 20        | 8083  | Irys DataItem signing                         | 0.5 CPU / 512 MB RAM |

All LLMs run as `llama.cpp` server containers inside Docker network `synergix-net`
(subnet `172.28.0.0/16`).

**RAG model:** `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
(384-dim embeddings, `faiss-cpu`). Index rebuilt from all `aporte` DataItems on startup.

GGUF model files must be placed in `aisynergix/ai/models/` before building:
- `qwen3-1.7b.gguf`
- `qwen3-0.6b.gguf`
- `starcoder2-3b.gguf`

---

## Contribution Flow (Core Pipeline)

```
User text
  │
  ├─ length check (min 20 chars)
  ├─ daily quota check (rank.daily_limit)
  ├─ duplicate check (FAISS cosine similarity threshold)
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
  ├─ Elite bonus   (score ≥ 9.0): +8 pts
  ├─ Legendary     (score ≥ 9.5): +15 pts
  └─ Challenge     (on-topic):    +5 pts
Trust score ± delta (default 5.0, clipped 0–10)
  │
  ▼
write_aporte() → POST irys-uploader:8083/upload
  Returns txId → public URL: https://gateway.irys.xyz/<txId>
  │
  ▼
RAG index ← add contribution (FAISS + metadata)
  │
  ▼
check_and_update_rank() → write_user_tags() → Irys
  │
  ▼
Bot reply: CID = txId (shown to user)
```

---

## Rank System

| Rank             | Min Points | Daily Limit | Multiplier |
|------------------|-----------|-------------|------------|
| 🌱 Iniciado      | 0         | 5           | ×1.0       |
| 📈 Activo        | 100       | 12          | ×1.2       |
| 🧬 Sincronizado  | 500       | 25          | ×1.5       |
| 🏗️ Arquitecto   | 1,500     | 40          | ×2.0       |
| 🧠 Mente Colmena | 5,000     | 60          | ×2.5       |
| 🔮 Oráculo       | 15,000    | ∞           | ×3.0       |

Ranks auto-promote on every successful contribution.

---

## Wallet Verification Flow

1. User taps **Synergix → Verify Wallet**
2. Bot generates a nonce-based challenge (no address required upfront):
   ```
   Synergix Identity Verification
   Nonce: <16 hex chars>
   Issued At: <ISO 8601>
   This signature is gasless and does not move funds.
   ```
3. Bot displays challenge + link to `https://bscscan.com/verifiedSignatures`
4. User signs in their wallet (MetaMask, Trust Wallet, etc.) via BscScan
5. User pastes the hex signature back in the bot
6. Bot recovers signer address via `eth_account.Account.recover_message` (ecrecover)
7. Address stored as `wallet_address` in user-profile DataItem on Irys
8. `human_verified = true` — unlocks Programmer mode

Challenge expires after **10 minutes**. Works across all 10 supported languages.

---

## Supported Languages

`es` 🇪🇸 · `en` 🇬🇧 · `zh` 🇨🇳 · `hi` 🇮🇳 · `ar` 🇸🇦 · `fr` 🇫🇷 · `bn` 🇧🇩 · `pt` 🇵🇹 · `id` 🇮🇩 · `ur` 🇵🇰

Language is persisted in `user-profile` DataItem. Auto-detected from first message.
String tables live in `aisynergix/bot/locales/<lang>.json`.

---

## Bot Commands & Buttons

| Trigger         | Action                                                            |
|-----------------|-------------------------------------------------------------------|
| `/start`        | Welcome + detect language; restore profile from Irys              |
| `/admin lock`   | Activate emergency lock (admin only)                             |
| `/admin unlock` | Deactivate emergency lock (admin only)                           |
| 🔥 Contribute   | Enter `awaiting_contribution` FSM state                           |
| 📊 View Status  | Display points, rank, daily quota, trust score                   |
| 🧠 My Memory    | List last 10 contributions from Irys                             |
| 🌐 Language     | Inline language selector (10 options)                            |
| 👨‍💻 Programmer  | Code generation mode (requires verified wallet)                  |
| 💰 Synergix     | Trading menu (verify, buy/sell links, balance, price, curve)     |
| 🏆 Top Mentes   | Leaderboard top-10 (rebuilt every 10 min)                        |

Admin IDs configured via `SYNERGIX_ADMIN_IDS` (comma-separated Telegram UIDs).

---

## Scheduled Tasks (`scripts/sync_brain.py`)

| Schedule          | Task                                                               |
|-------------------|--------------------------------------------------------------------|
| Daily 00:00 UTC   | `reset_all_daily_counts()` — zero daily quota for all users        |
| Every 10 min      | `rebuild_top10()` — rescan user DataItems, sort by points          |
| Hourly            | `load_ai_guard()` — reload adversarial prompt filter patterns from Irys    |
| Startup           | `load_system_config()` — load quality thresholds from Irys         |

---

## Trading Integration

| Feature       | Details                                                              |
|---------------|----------------------------------------------------------------------|
| Chain         | BNB Chain (BSC mainnet, chain ID 56)                                 |
| DEX           | PancakeSwap V2                                                       |
| Price source  | `getReserves()` on-chain (cache TTL 120 s)                           |
| Market data   | DexScreener API (price, 24h change, volume, liquidity, market cap)   |
| Bonding curve | Four.Meme curve manager contract                                     |
| Trade exec    | Deep-link to PancakeSwap — user signs in own wallet, no custody      |
| Min buy       | 0.0001 BNB                                                           |
| Min sell      | 0.0002 BNB equivalent                                                |
| Slippage      | 1%                                                                   |

---

## Environment Variables

### Required

| Variable          | Description                                      |
|-------------------|--------------------------------------------------|
| `TELEGRAM_TOKEN`  | Bot token from BotFather                        |
| `PRIVATE_KEY`     | ECDSA private key (hex, with or without `0x`)   |

### Irys Storage

| Variable             | Default                       | Description                         |
|----------------------|-------------------------------|-------------------------------------|
| `IRYS_NODE_URL`      | `https://uploader.irys.xyz`   | Irys upload node endpoint           |
| `IRYS_GATEWAY_URL`   | `https://gateway.irys.xyz`    | Irys public read gateway            |
| `IRYS_TOKEN`         | `bnb`                         | Token used for uploads (`bnb`/`ethereum`) |
| `IRYS_UPLOADER_URL`  | `http://irys-uploader:8083`   | Internal URL of the microservice    |

### AI Infrastructure

| Variable                    | Default                    | Description              |
|-----------------------------|----------------------------|--------------------------|
| `SYNERGIX_THINKER_HOST`     | `http://thinker:8081`      | Thinker LLM endpoint     |
| `SYNERGIX_JUDGE_HOST`       | `http://judge:8080`        | Judge LLM endpoint       |
| `SYNERGIX_PROGRAMMER_HOST`  | `http://programmer:8082`   | Programmer LLM endpoint  |

### Application

| Variable                  | Default                       | Description                              |
|---------------------------|-------------------------------|------------------------------------------|
| `SYNERGIX_ADMIN_IDS`      | —                             | Comma-separated Telegram UIDs for admins |
| `SYNERGIX_SIWE_DOMAIN`    | `synergix.bot`                | Domain for SIWE-style challenges         |
| `SYNERGIX_SIWE_URI`       | `https://synergix.bot`        | URI for SIWE-style challenges            |
| `SYNERGIX_SIWE_CHAIN_ID`  | `56`                          | Chain ID for challenges (BSC = 56)       |
| `SYNERGIX_CACHE_TTL`      | `12`                          | Hours for miscellaneous cache TTL        |
| `BSC_RPC_URL`             | `https://bsc-dataseed1.binance.org` | BSC RPC for on-chain price reads  |

---

## Deployment

```bash
# 1. Clone
git clone https://github.com/synergixia/Synergix.git && cd Synergix

# 2. Configure environment variables
#    Duplicate the file "env.example" → rename it to ".env"
#    Then set TELEGRAM_TOKEN and PRIVATE_KEY inside it

# 3. Place GGUF models in aisynergix/ai/models/
#    qwen3-1.7b.gguf, qwen3-0.6b.gguf, starcoder2-3b.gguf

# 4. Fund the Irys node wallet (one-time)
python scripts/irys_fund.py

# 5. Build and start all 5 services
cd docker
docker-compose up -d --build

# 6. Check logs
docker-compose logs -f bot
docker-compose logs -f irys-uploader
```

### Self-Healing on Restart

On startup the bot automatically:
1. Connects to `irys-uploader`, verifies wallet address and Irys balance
2. Loads `system-config` DataItem (quality thresholds)
3. Loads `ai-guard` DataItem (adversarial prompt filter patterns)
4. Checks `emergency-lock` presence
5. Rebuilds FAISS index from all `aporte` DataItems on Irys
6. Health-checks thinker, judge, and programmer LLMs

If Irys is unreachable, falls back to in-memory defaults and retries on next scheduled cycle.

---

## Key Design Decisions & Gotchas

| Constraint | Solution |
|---|---|
| Web3 crypto unreliable in Python | Delegated to Node.js `irys-uploader` with official SDK |
| Immutable storage = no updates | Latest DataItem with matching tags wins; old records superseded, not deleted |
| `Content-Type` tag case-sensitive on Irys | Always use exact casing in all uploads |
| Gateway returns 302 redirects | `httpx` client must use `follow_redirects=True` |
| Wallet recovery without user input | Nonce-based challenge + `ecrecover` from BscScan |
| UID privacy | SHA-256 hash prefix; raw Telegram UID never stored |
| FAISS index persistence | Serialised binary uploaded as `brain-index` DataItem; rebuilt on startup |
| Python has no mutable DB | All state must be written to Irys; in-memory caches are ephemeral |

---

## Common Tasks

### Adding a new bot command

1. Add handler in `aisynergix/bot/bot.py` using `@router.message()` or `@router.callback_query()`
2. Add FSM state in `aisynergix/bot/fsm.py` if the command has multi-step interaction
3. Add i18n strings to all 10 locale JSON files in `aisynergix/bot/locales/`
4. If the command writes data, use `irys.py` → POST to `irys-uploader:8083/upload` with proper `data-type` tag

### Adding a new Irys data type

1. Define the new `data-type` tag value (use kebab-case, e.g. `my-new-type`)
2. Add read function in `aisynergix/services/irys.py` using GraphQL query with tag filter
3. Add write function that POSTs base64 data + tags to `irys-uploader:8083/upload`
4. Always include `App-Name: Synergix` tag for namespace isolation
5. Latest DataItem wins — no delete needed, just write a new one

### Adding a new language

1. Create `aisynergix/bot/locales/<lang_code>.json` with all required string keys
2. Add the language code and flag to the inline keyboard in `aisynergix/bot/bot.py`
3. Add the language to `aisynergix/bot/locales.py` loader
4. The language is auto-persisted in the user's `user-profile` DataItem

### Debugging Irys connectivity

```bash
# Check irys-uploader health
curl http://localhost:8083/health

# Check wallet balance on Irys node
curl http://localhost:8083/balance

# Test upload manually
curl -X POST http://localhost:8083/upload \
  -H "Content-Type: application/json" \
  -d '{"data": "<base64>", "tags": [{"name": "App-Name", "value": "Synergix"}, {"name": "data-type", "value": "test"}]}'
```

### Querying Irys GraphQL

```javascript
// Find latest user-profile for a hashed UID
const query = `{
  transactions(
    tags: [
      { name: "App-Name", values: ["Synergix"] },
      { name: "data-type", values: ["user-profile"] },
      { name: "uid-hash", values: ["<uid_hash>"] }
    ],
    order: DESC,
    limit: 1
  ) {
    edges { node { id timestamp } }
  }
}`;
// POST to: https://uploader.irys.xyz/graphql
```

---

## Service Ports Reference

| Service      | Port  | Protocol |
|--------------|-------|----------|
| Bot health   | 7860  | HTTP     |
| Judge LLM    | 8080  | HTTP (llama.cpp) |
| Thinker LLM  | 8081  | HTTP (llama.cpp) |
| Programmer   | 8082  | HTTP (llama.cpp) |
| irys-uploader| 8083  | HTTP (Express) |

Internal Docker network: `synergix-net` · Subnet: `172.28.0.0/16`
