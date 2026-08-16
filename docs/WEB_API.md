# Synergix Web API (phase B)

The Web API lets the **synergix.lol** website talk to the real Synergix
engine — the same Thinker, Judge and RAG index that power the Telegram
bot — so visitors can chat with on-chain RAG and contribute to the
Immortal Memory directly from the browser.

It runs **inside the bot process** (opt-in), so there is no second model
download and no duplicated brain. The website never talks to it
directly: a thin Vercel Edge proxy injects the API key server-side and
the browser only ever calls same-origin `/api/*`.

```
Browser ──/api/chat──▶ Vercel Edge proxy ──X-API-Key──▶ Cloudflare Tunnel ──▶ bot:8090 (Web API)
                                                                                   │
                                                          shares ▶ RAG · Thinker · Judge · irys-uploader
```

## What it exposes

| Method | Path | Purpose |
|---|---|---|
| `GET`  | `/health` | Liveness + thinker/judge health (no key needed) |
| `POST` | `/v1/chat` | SSE stream — RAG-grounded answer in the request's language |
| `POST` | `/v1/judge` | Score a candidate aporte (preview, no write) |
| `POST` | `/v1/contribute` | Verify wallet signature → judge → seal on Irys |

Every non-health request must carry `X-API-Key: <SYNERGIX_WEB_API_KEY>`.
Requests are rate-limited per IP (default 20 / 60 s).

### Statelessness & identity
Web visitors have **no Telegram identity**, so:
- Chat does **not** read or write the points/profile system, and uses the
  language from the request (not a stored profile).
- Contributions are sealed on Irys tagged with the contributor's **wallet
  signature** as author (`signature` tag) and a `source=web` tag, but do
  **not** award points. Points still accrue only via the Telegram bot.

## Enabling it

Set these in your `.env` (see `.env.example`):

```bash
WEB_API_ENABLED=true
WEB_API_KEY=$(openssl rand -hex 32)        # share this with Vercel
WEB_CORS_ORIGINS=https://www.synergix.lol,https://synergix.lol
```

Then restart the bot. The compose file already binds the API to
`127.0.0.1:8090` (localhost only — never expose `0.0.0.0` to the public
internet; the tunnel handles ingress).

## Exposing it with Cloudflare Tunnel (recommended)

Cloudflare Tunnel gives you a public HTTPS hostname with automatic TLS and
DDoS protection, without opening any inbound port on your Hetzner box.

1. Install `cloudflared` on the host:
   ```bash
   curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 \
     -o /usr/local/bin/cloudflared && chmod +x /usr/local/bin/cloudflared
   cloudflared tunnel login
   ```
2. Create a named tunnel and route a hostname to the local API:
   ```bash
   cloudflared tunnel create synergix-api
   cloudflared tunnel route dns synergix-api api.synergix.lol
   ```
3. Config `~/.cloudflared/config.yml`:
   ```yaml
   tunnel: synergix-api
   credentials-file: /root/.cloudflared/<TUNNEL_ID>.json
   ingress:
     - hostname: api.synergix.lol
       service: http://127.0.0.1:8090
     - service: http_status:404
   ```
4. Run it as a service:
   ```bash
   cloudflared service install
   systemctl enable --now cloudflared
   ```
5. Verify (the API key is required for everything except `/health`):
   ```bash
   curl https://api.synergix.lol/health
   ```

> Prefer running the tunnel as a sibling compose service? Add a
> `cloudflare/cloudflared:latest` service on `synergix-net` with
> `command: tunnel run --token <TOKEN>` and point ingress at
> `http://bot:8090` instead of `127.0.0.1:8090`.

## Wiring the website (Vercel)

In **Vercel → Project → Settings → Environment Variables** set:

| Variable | Value |
|---|---|
| `SYNERGIX_UPSTREAM` | `https://api.synergix.lol` |
| `SYNERGIX_UPSTREAM_KEY` | the same `WEB_API_KEY` from above |

Redeploy. The site's chat widget will auto-detect the upstream via
`/api/synergix-health` and default to **cloud** mode. If the upstream is
absent or down, the widget transparently falls back to **local** mode
(WebLLM in the browser).

## Capacity note

The Thinker runs `--parallel 1`, so website chat and Telegram chat share a
single generation slot (requests queue in `asyncio`, no timeout risk). For
a beta this is fine; if web traffic grows, run a second llama.cpp Thinker
and point a dedicated `THINKER_HOST` at it for the web process.

## Security checklist

- [x] API key required on every non-health endpoint
- [x] Per-IP rate limiting
- [x] CORS locked to your site origins
- [x] Bound to `127.0.0.1` — public ingress only via the tunnel
- [x] Irys `PRIVATE_KEY` never leaves the bot host
- [x] Wallet signatures re-verified server-side before any Irys write
