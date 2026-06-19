# Synergix GPU node — Thinker + Judge (Vast.ai / RTX 4090)

This directory builds the GPU half of the hybrid topology: the **Thinker**
(Qwen2.5-14B-Instruct Q4_K_M) and **Judge** (Qwen2.5-1.5B Q8) served by
`llama.cpp` on a single 24 GB GPU, reachable from the Hetzner bot over a
private **Tailscale** mesh.

The image is **provider-agnostic** — these instructions target **Vast.ai**
with an **RTX 4090**, but the same image runs on RunPod or any GPU Docker host.

```
Hetzner (CPU)                         Vast.ai (RTX 4090, 24 GB)
  synergix-bot ──tailnet──►  synergix-gpu : llama-server :8081 (Thinker 14B)
                                          : llama-server :8080 (Judge 1.5B)
  thinker-cpu (fallback) ◄── automatic failover when the GPU is down
```

VRAM budget: ~9 GB (14B Q4_K_M) + ~1.7 GB (1.5B Q8) + KV cache ≈ **12–13 GB**,
leaving plenty of headroom on a 4090 to raise the Thinker context
(`THINKER_CTX`, default 8192).

## 1. Build & push the image

```bash
docker build -t <your-registry>/synergix-gpu:latest gpu/
docker push   <your-registry>/synergix-gpu:latest
```

(`<your-registry>` = Docker Hub, GHCR, etc. — anywhere Vast.ai can pull from.
For a private registry, add the credentials in the Vast.ai instance config.)

## 2. Launch the instance on Vast.ai

Pick an **RTX 4090** offer, then in the template / instance config:

- **Image:** `<your-registry>/synergix-gpu:latest`
- **Launch mode:** *Docker ENTRYPOINT* (so our `/start.sh` runs). Leave the
  on-start script empty — the image's `ENTRYPOINT` handles everything.
- **Disk:** ~30 GB (enough for both `.gguf` files + headroom). Vast bills
  storage, so don't over-provision.
- **Docker options** (to enable kernel-mode Tailscale; optional):
  ```
  --cap-add NET_ADMIN --device /dev/net/tun
  ```
  If the host doesn't allow this, `start.sh` automatically falls back to
  userspace networking + `tailscale serve` — no action needed.
- **Environment variables:**

| Var | Required | Default | Notes |
|-----|----------|---------|-------|
| `TS_AUTHKEY` | ✅ | — | Tailscale auth key — use an **ephemeral + tagged** key so recycled instances clean up |
| `TS_HOSTNAME` | | `synergix-gpu` | Tailnet hostname for this node |
| `THINKER_MODEL` | | `/workspace/models/qwen2.5-14b-instruct-q4_k_m.gguf` | |
| `JUDGE_MODEL` | | `/workspace/models/qwen2.5-1.5b-q8.gguf` | |
| `MODELS_DIR` | | `/workspace/models` | Change if your Vast disk mounts elsewhere |
| `THINKER_CTX` | | `8192` | Thinker context window |
| `THINKER_PARALLEL` | | `2` | Parallel slots (safe on GPU) |
| `NGL` | | `999` | GPU layers to offload (all) |

## 3. Get the models onto the instance

The `.gguf` files must live under `MODELS_DIR` (default `/workspace/models`).
SSH into the instance (Vast provides an SSH command) and download once:

```bash
mkdir -p /workspace/models && cd /workspace/models
# 14B Thinker (~9 GB) and 1.5B Judge (~1.7 GB) — example with huggingface-cli:
pip install -q huggingface_hub
huggingface-cli download Qwen/Qwen2.5-14B-Instruct-GGUF \
  qwen2.5-14b-instruct-q4_k_m.gguf --local-dir .
huggingface-cli download Qwen/Qwen2.5-1.5B-Instruct-GGUF \
  qwen2.5-1.5b-instruct-q8_0.gguf --local-dir .
```

> **Save money:** keep the models on the instance's **persistent disk** and use
> **Stop** (not Destroy) when idle — Stop keeps the disk so you never
> re-download. Destroy wipes everything.

Then restart the container (or the instance) so `start.sh` picks them up.

## 4. Wire up the Hetzner side

Find the node's Tailscale IP (instance logs, or `tailscale ip -4` over SSH),
then in the Hetzner `.env`:

```ini
TS_AUTHKEY=tskey-auth-...                     # key for the Hetzner host
THINKER_HOST=http://100.x.y.z:8081            # node's Tailscale IP
JUDGE_HOST=http://100.x.y.z:8080
THINKER_FALLBACK_HOST=http://thinker-cpu:8081 # local CPU fallback
JUDGE_FALLBACK_HOST=http://judge:8080
THINKER_MAX_CONCURRENCY=2                      # match THINKER_PARALLEL
```

Bring the stack up (from the repo root) with the hybrid overlay:

```bash
docker compose --env-file .env \
  -f docker/docker-compose.yml \
  -f docker/docker-compose.hybrid.yml up -d
```

## 5. Verify

```bash
# From the Hetzner host (on the tailnet):
curl http://100.x.y.z:8081/health     # Thinker
curl http://100.x.y.z:8080/health     # Judge
```

Then **Stop** the Vast instance and confirm the bot keeps answering via
`thinker-cpu` — the `FailoverConnector` in `aisynergix/ai/local_ia.py` switches
automatically and switches back when the GPU returns.

## Saving money on Vast.ai

- **Stop, don't Destroy** when idle: you pay only for disk, never re-download
  the models, and the bot stays alive on the CPU fallback meanwhile.
- **Interruptible (bid) instances** are much cheaper than on-demand; the CPU
  fallback absorbs interruptions transparently, so they're a great fit here.
- **Right-size the disk** (~30 GB) — Vast bills storage per GB.
- **Schedule** the instance for your active hours; outside them, run on the
  Hetzner CPU fallback only.
- Prefer hosts with a high reliability score and good bandwidth (faster initial
  model download = less paid time spent idling during setup).

## Security

- The llama-servers are reachable **only over the tailnet** — do not expose
  ports 8080/8081 via Vast's public port mapping. Tighten further with
  Tailscale ACLs so only the Hetzner host can reach them.
- No API key is needed on `llama-server` because the tailnet already provides
  authentication and encryption.
