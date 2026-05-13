# Synergix — Sovereign Ghost Node AI

Synergix is a decentralized Telegram bot that uses **BNB Greenfield** as its only persistent database. The local server holds no state: every user profile, contribution, rank, and configuration lives as an object or tag in the Greenfield bucket. If the server is wiped, the node reconstructs itself entirely from the bucket.

---

## Architecture Overview

```
Telegram User
      │
      ▼
 aiogram 3 Bot  ──── FSM (L1 RAM cache + Greenfield fallback)
      │
      ├── Judge (Qwen3-0.6B @ :8080)  — quality scoring 0–10
      ├── Thinker (Qwen3-1.7B @ :8081) — conversation + RAG
      └── Programmer (StarCoder2-3B @ :8082) — code generation
      │
      ▼
 FAISS Vector Index (multilingual, rebuilt from bucket)
      │
      ▼
 BNB Greenfield bucket: synergix-v2  ─── Source of truth
```

**Ghost Protocol:** Telegram UIDs are never stored on-chain. Every UID is hashed once — `SHA-256("Synergix_" + uid)[:12]` — before touching the Greenfield layer. The real identity behind any profile is permanently unknowable from the blockchain.

---

## Bucket Structure

All data lives in the **`synergix-v2`** bucket on Greenfield mainnet (SP: `https://greenfield-sp.bnbchain.org`).

```
synergix-v2/
├── aisynergix/
│   ├── users/
│   │   └── {uid_hash}             # Ghost file ({} content). Tags:
│   │       ├── points             # integer string
│   │       ├── rank               # rank emoji + name
│   │       ├── language           # 2-letter code
│   │       └── meta               # compact JSON ≤64 bytes:
│   │                              #   {"t":trust×100,"v":0|1,"d":daily,"u":total,"l":ts}
│   ├── wallets/
│   │   └── {uid_hash}             # 42-byte file: "0x<40hex>" (EIP-55 address)
│   ├── aportes/
│   │   └── YYYY-MM/
│   │       └── {uid_hash}_{ts}.txt  # Contribution text. Tags:
│   │           ├── author_uid     # 12-char uid hash
│   │           ├── quality_score  # float string
│   │           ├── category       # string
│   │           └── meta           # {"lang":"es","impact_index":"0.5",...}
│   ├── data/
│   │   ├── brain_pointer          # 0-byte. Tag: latest_v="v1.2.3"
│   │   ├── top10.json             # Leaderboard snapshot (written once)
│   │   └── system_config.json     # Quality thresholds, trust deltas
│   └── logs/
│       └── YYYY-MM-DD.log.gz      # Daily compressed log archives
├── ai_guard.txt                   # Anti-jailbreak patterns
└── emergency_lock                 # Presence = all writes blocked
```

**Greenfield tag constraints:** max 4 tags/object, max 64 bytes UTF-8 per tag value. The `meta` tag packs all remaining fields as compact single-letter-key JSON to stay under the limit. `wallet_address` (42 chars) lives in its own dedicated object because it cannot fit inside `meta`.

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

Ranks are auto-promoted on every contribution. Points are calculated as `int(quality_score × 2)` + tier bonus (Elite ≥9.0: +8pts, Legendary ≥9.5: +15pts) + challenge bonus (+5pts if on-topic).

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
write_aporte() → Greenfield aportes/YYYY-MM/{hash}_{ts}.txt
    Returns: real blockchain tx_hash (64 hex chars)
    │
    ▼
RAG index ← add contribution (FAISS + metadata)
    │
    ▼
check_and_update_rank() → MsgSetTag on user object
    │
    ▼
Bot reply: CID = first 16 chars of tx_hash + "…"
```

If Greenfield write fails (SP error, network timeout), the bot shows `…(pendiente)` and logs the full traceback at ERROR level. The contribution still enters the RAG index.

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

Language is persisted in the `language` tag and auto-detected from the first message.

---

## Bot Commands & Buttons

| Trigger | Action |
|---------|--------|
| `/start` | Welcome + detect language; restore profile from Greenfield |
| `/admin lock` | Activate emergency lock (admin only) |
| `/admin unlock` | Deactivate emergency lock (admin only) |
| 🔥 Contribute | Enter `awaiting_contribution` FSM state |
| 📊 View Status | Display points, rank, daily quota, trust score |
| 🧠 My Memory | List last 10 aportes from Greenfield |
| 🌐 Language | Inline language selector (10 options) |
| 👨‍💻 Programmer | Code generation mode (requires verified wallet) |
| 💰 Synergix | Trading menu (verify wallet, buy/sell links, balance, price, bonding curve) |
| 🏆 Top Mentes | Leaderboard top-10 (rebuilt every 10 min) |

**Admin IDs** are configured via `SYNERGIX_ADMIN_IDS` (comma-separated Telegram UIDs).

---

## Wallet Verification (EIP-4361 SIWE)

1. User taps **Synergix → Verify Wallet**
2. Bot generates a signed EIP-4361 challenge (expires 10 min)
3. User signs the challenge in MetaMask / Trust Wallet
4. User pastes the signature back
5. Bot recovers the signer address and confirms match
6. Address persisted to `aisynergix/wallets/{uid_hash}` (separate object, not a tag)
7. `human_verified = true` — unlocks Programmer mode and wallet-signed aporte tags

---

## AI Services

| Service | Model | Port | Role | Resources |
|---------|-------|------|------|-----------|
| Thinker | `qwen3-1.7b.gguf` | 8081 | Conversations, Oracle, RAG generation | 2 CPU / 1.8 GB RAM |
| Judge | `qwen3-0.6b.gguf` | 8080 | Quality scoring, contribution validation | 1 CPU / 0.8 GB RAM |
| Programmer | `starcoder2-3b.gguf` | 8082 | Code generation (verified users) | 2 CPU / 2.5 GB RAM |

All three run as `llama.cpp` server containers. The bot container communicates over Docker internal network (`synergix-net`).

**RAG Engine:** `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (384-dimensional embeddings, `faiss-cpu`). Index rebuilt from all Greenfield aportes on startup. Duplicate detection threshold and semantic search run against the same FAISS index.

---

## Trading Integration

| Feature | Details |
|---------|---------|
| Chain | BNB Chain (BSC mainnet, chain ID 56) |
| DEX | PancakeSwap V2 |
| Price source | getReserves() on-chain (cache TTL 120s) |
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
| Every 10 min | `rebuild_top10()` — rescan all user objects, sort by points, update RAM cache |
| Hourly | `load_ai_guard()` — reload anti-jailbreak patterns from Greenfield |
| Startup | `load_system_config()` — load quality thresholds from `data/system_config.json` |

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
│   │   ├── fsm.py              # FSM states, L1 RAM cache (TTL 3600s, max 500)
│   │   ├── identity.py         # UserProfile dataclass, UserCache (TTL 30s), IdentityManager
│   │   └── locales.py          # i18n string tables (10 languages)
│   └── services/
│       ├── greenfield.py       # Greenfield SDK wrapper (all bucket I/O)
│       ├── rag_engine.py       # FAISS index + sentence-transformers
│       ├── wallet_verify.py    # EIP-4361 SIWE verification
│       ├── trading.py          # PancakeSwap V2 read-only + deep-links
│       ├── dexscreener.py      # DexScreener market data API
│       └── four_meme.py        # Bonding curve progress
├── scripts/
│   ├── sync_brain.py           # APScheduler cron daemon
│   └── fusion_brain.py         # Offline brain aggregation utility
├── docker/
│   ├── Dockerfile
│   └── docker-compose.yml
└── requirements.txt
```

---

## Environment Variables

### Required

| Variable | Description |
|----------|-------------|
| `TELEGRAM_TOKEN` | Bot token from BotFather |
| `PRIVATE_KEY` | ECDSA private key (with or without `0x` prefix) for signing Greenfield transactions |

### Greenfield SDK (read directly by SDK, no prefix)

| Variable | Default | Description |
|----------|---------|-------------|
| `HOST` | `https://greenfield-chain.bnbchain.org` | Greenfield RPC endpoint |
| `PORT` | `443` | RPC port |
| `CHAIN_ID` | `1017` | Greenfield chain ID |

### Synergix Application (prefix `SYNERGIX_`)

| Variable | Default | Description |
|----------|---------|-------------|
| `SYNERGIX_BUCKET_NAME` | `synergix-v2` | Greenfield bucket name |
| `SYNERGIX_SP_ENDPOINT` | `https://greenfield-sp.bnbchain.org` | Storage Provider endpoint |
| `SYNERGIX_DCELLAR_SP_ADDRESS` | — | SP operator address (optional) |
| `SYNERGIX_BUCKET_ID` | `0x…fd06` | Bucket object ID |
| `SYNERGIX_THINKER_HOST` | `http://thinker:8081` | Thinker LLM endpoint |
| `SYNERGIX_JUDGE_HOST` | `http://judge:8080` | Judge LLM endpoint |
| `SYNERGIX_PROGRAMMER_HOST` | `http://programmer:8082` | Programmer LLM endpoint |
| `SYNERGIX_ADMIN_IDS` | — | Comma-separated Telegram UIDs for `/admin` |
| `SYNERGIX_SIWE_DOMAIN` | `synergix.bot` | SIWE challenge domain |
| `SYNERGIX_SIWE_URI` | `https://synergix.bot` | SIWE challenge URI |
| `SYNERGIX_SIWE_CHAIN_ID` | `56` | EIP-4361 chain ID (BSC) |
| `SYNERGIX_CACHE_TTL` | `12` | Hours for misc cache |

### Blockchain & Trading

| Variable | Default | Description |
|----------|---------|-------------|
| `BSC_RPC_URL` | `https://bsc-dataseed1.binance.org` | BSC RPC for on-chain price reads |

---

## Deployment

### Prerequisites

- Docker + Docker Compose
- GGUF model files placed in `aisynergix/ai/models/`:
  - `qwen3-1.7b.gguf` (Thinker)
  - `qwen3-0.6b.gguf` (Judge)
  - `starcoder2-3b.gguf` (Programmer)
- A BNB Greenfield bucket (`synergix-v2`) already created via DCellar with the wallet matching `PRIVATE_KEY`

### Steps

```bash
# 1. Clone the repository
git clone https://github.com/synergixia/Synergix.git
cd Synergix

# 2. Create .env
cp .env.example .env
# Fill in: TELEGRAM_TOKEN, PRIVATE_KEY, and optionally the other vars

# 3. Build and start all services
cd docker
docker-compose up -d --build

# 4. Check logs
docker-compose logs -f bot
```

### Self-Healing on Restart

On startup the bot:
1. Loads `system_config.json` from Greenfield (quality thresholds)
2. Loads `ai_guard.txt` from Greenfield (anti-jailbreak patterns)
3. Checks `emergency_lock` presence
4. Rebuilds the FAISS index from all `aisynergix/aportes/` objects
5. Health-checks thinker, judge, programmer

If the Greenfield bucket is unreachable during startup, the bot falls back to in-memory defaults and retries on the next scheduled cycle.

---

## Key Constraints & Design Decisions

| Constraint | Solution |
|-----------|----------|
| Greenfield: max 4 tags/object | 3 explicit visible tags + 1 `meta` compact JSON |
| Greenfield: max 64 bytes/tag value | Single-letter compact keys + integer encoding; wallet separated out |
| `wallet_address` (42 chars) exceeds `meta` budget | Dedicated object `aisynergix/wallets/{uid_hash}` |
| MsgSetTag only reliable on SEALED objects | `write_user_tags` always calls `put_object` after `create_object` |
| SP error 50004 on re-read (CREATED state) | `read_user_tags` uses `get_object_head` (tag-only read, no SP fetch) |
| Stale cache with external writes | `UserCache` TTL = 30 s; forced re-read from Greenfield on expiry |
| SP 50004 on delete+recreate of SEALED objects | JSON data files and leaderboard written once, never overwritten |
| SP sync lag after `create_object` | `_SP_SYNC_DELAY = 12 s` sleep before `put_object` |
