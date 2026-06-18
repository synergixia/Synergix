# Synergix on RunPod — GPU Thinker + Judge

This directory builds the GPU half of the hybrid topology: the **Thinker**
(Qwen2.5-14B-Instruct Q4_K_M) and **Judge** (Qwen2.5-1.5B Q8) served by
`llama.cpp` on a single 24 GB GPU, reachable from the Hetzner bot over a
private **Tailscale** mesh.

```
Hetzner (CPU)                         RunPod (GPU 24 GB)
  synergix-bot ──tailnet──►  synergix-gpu : llama-server :8081 (Thinker 14B)
                                          : llama-server :8080 (Judge 1.5B)
  thinker-cpu (fallback) ◄── automatic failover when the GPU is down
```

VRAM budget: ~9 GB (14B Q4_K_M) + ~1.7 GB (1.5B Q8) + KV cache ≈ **12–13 GB**,
leaving headroom to raise the Thinker context (`THINKER_CTX`, default 8192).

## 1. Build & push the image

```bash
docker build -t <your-registry>/synergix-gpu:latest runpod/
docker push   <your-registry>/synergix-gpu:latest
```

The image is `ghcr.io/ggml-org/llama.cpp:server-cuda` + Tailscale + `start.sh`.

## 2. Create a RunPod network volume for the models

Models must survive pod restarts, so put them on a **network volume** mounted
at `/workspace`:

```
/workspace/models/qwen2.5-14b-instruct-q4_k_m.gguf
/workspace/models/qwen2.5-1.5b-q8.gguf
```

Download them once (e.g. from Hugging Face) into that volume.

## 3. Launch the pod

- **GPU:** any 24 GB card (RTX 4090 / L4 / A5000 / A10).
- **Image:** the one you pushed in step 1.
- **Volume:** the network volume from step 2, mounted at `/workspace`.
- **TUN:** enable `/dev/net/tun` if your template allows it (kernel-mode
  Tailscale). If not, `start.sh` automatically falls back to userspace
  networking + `tailscale serve`.
- **Environment variables:**

| Var | Required | Default | Notes |
|-----|----------|---------|-------|
| `TS_AUTHKEY` | ✅ | — | Tailscale auth key — use an **ephemeral + tagged** key so recycled pods clean up |
| `TS_HOSTNAME` | | `synergix-gpu` | Tailnet hostname for this pod |
| `THINKER_MODEL` | | `/workspace/models/qwen2.5-14b-instruct-q4_k_m.gguf` | |
| `JUDGE_MODEL` | | `/workspace/models/qwen2.5-1.5b-q8.gguf` | |
| `THINKER_CTX` | | `8192` | Thinker context window |
| `THINKER_PARALLEL` | | `2` | Parallel slots (safe on GPU) |
| `NGL` | | `999` | GPU layers to offload (all) |

## 4. Wire up the Hetzner side

Find the pod's Tailscale IP (printed in the pod logs, or `tailscale status`),
then in the Hetzner `.env`:

```ini
TS_AUTHKEY=tskey-auth-...                     # key for the Hetzner host
THINKER_HOST=http://100.x.y.z:8081            # pod Tailscale IP
JUDGE_HOST=http://100.x.y.z:8080
THINKER_FALLBACK_HOST=http://thinker-cpu:8081 # local CPU fallback
JUDGE_FALLBACK_HOST=http://judge:8080
```

Bring the stack up with the hybrid overlay:

```bash
docker compose -f docker/docker-compose.yml -f docker/docker-compose.hybrid.yml up -d
```

## 5. Verify

```bash
# From the Hetzner host (on the tailnet):
curl http://100.x.y.z:8081/health     # Thinker
curl http://100.x.y.z:8080/health     # Judge
```

Then kill the pod and confirm the bot keeps answering via `thinker-cpu` — the
`FailoverConnector` in `aisynergix/ai/local_ia.py` switches automatically and
switches back when the GPU returns.

## Security

- The pod's llama-servers are reachable **only over the tailnet** (RunPod does
  not expose arbitrary ports publicly). Lock it down further with Tailscale
  ACLs so only the Hetzner host can reach ports 8080/8081.
- No API key is needed on `llama-server` because the tailnet already provides
  authentication and encryption.
