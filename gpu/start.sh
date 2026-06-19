#!/usr/bin/env bash
#
# start.sh — GPU node entrypoint for the Synergix Thinker + Judge.
#
# Provider-agnostic: runs on Vast.ai, RunPod, or any GPU Docker host. Brings up
# Tailscale (so the Hetzner bot can reach this node over a private, encrypted
# mesh) and launches two llama.cpp servers on the GPU:
#   • Thinker  → Qwen2.5-14B-Instruct Q4_K_M  (port 8081)
#   • Judge    → Qwen2.5-1.5B Q8               (port 8080)
#
# Both models fit comfortably in 24 GB of VRAM (~9 GB + ~1.7 GB) — e.g. RTX 4090.
#
set -euo pipefail

# ── Config (override via the host's env vars) ───────────────────────────────
: "${TS_AUTHKEY:?TS_AUTHKEY is required (Tailscale auth key — use ephemeral + tagged)}"
TS_HOSTNAME="${TS_HOSTNAME:-synergix-gpu}"

MODELS_DIR="${MODELS_DIR:-/workspace/models}"
THINKER_MODEL="${THINKER_MODEL:-$MODELS_DIR/qwen2.5-14b-instruct-q4_k_m.gguf}"
JUDGE_MODEL="${JUDGE_MODEL:-$MODELS_DIR/qwen2.5-1.5b-q8.gguf}"

THINKER_PORT="${THINKER_PORT:-8081}"
JUDGE_PORT="${JUDGE_PORT:-8080}"

# Context windows — 24 GB lets the Thinker run a far larger context than the
# 4096 used on CPU.  Tune down if you also push a bigger Thinker model.
THINKER_CTX="${THINKER_CTX:-8192}"
JUDGE_CTX="${JUDGE_CTX:-4096}"

# -ngl 999 offloads every layer to the GPU.  --parallel >1 is safe on GPU
# (unlike the CPU build pinned to 1); the bot's asyncio semaphore can be
# relaxed once the GPU primary is active.
NGL="${NGL:-999}"
THINKER_PARALLEL="${THINKER_PARALLEL:-2}"

LLAMA_BIN="${LLAMA_BIN:-/app/llama-server}"

# ── 1. Tailscale ────────────────────────────────────────────────────────────
mkdir -p /var/run/tailscale /var/lib/tailscale

if [[ -e /dev/net/tun ]]; then
    # Kernel networking: llama-server can bind the tailnet interface directly.
    tailscaled \
        --state=/var/lib/tailscale/tailscaled.state \
        --socket=/var/run/tailscale/tailscaled.sock &
    BIND_HOST="0.0.0.0"
    USERSPACE=0
else
    # No TUN device on this pod — fall back to userspace networking and expose
    # the ports to the tailnet with `tailscale serve` further down.
    echo "[start] /dev/net/tun unavailable — using userspace networking"
    tailscaled \
        --tun=userspace-networking \
        --state=/var/lib/tailscale/tailscaled.state \
        --socket=/var/run/tailscale/tailscaled.sock &
    BIND_HOST="127.0.0.1"
    USERSPACE=1
fi

# Wait for the daemon socket to come up.
for _ in $(seq 1 30); do
    tailscale status >/dev/null 2>&1 && break
    sleep 1
done

tailscale up \
    --authkey="${TS_AUTHKEY}" \
    --hostname="${TS_HOSTNAME}" \
    --accept-routes=false \
    --ssh=false

echo "[start] Tailscale up as '${TS_HOSTNAME}' — IP: $(tailscale ip -4 2>/dev/null || echo '?')"

# ── 2. Verify models are present ─────────────────────────────────────────────
for m in "$THINKER_MODEL" "$JUDGE_MODEL"; do
    if [[ ! -f "$m" ]]; then
        echo "ERROR: model file not found: $m" >&2
        echo "Place the .gguf files in $MODELS_DIR (use persistent instance storage so they survive restarts)." >&2
        exit 1
    fi
done

# ── 3. Launch llama.cpp servers on the GPU ───────────────────────────────────
echo "[start] launching Thinker (${THINKER_MODEL##*/}) on :${THINKER_PORT}"
"$LLAMA_BIN" \
    -m "$THINKER_MODEL" \
    --host "$BIND_HOST" --port "$THINKER_PORT" \
    -c "$THINKER_CTX" -ngl "$NGL" --parallel "$THINKER_PARALLEL" \
    --temp 0.8 --top-k 40 --top-p 0.9 --repeat-penalty 1.05 -n 2048 \
    --no-mmap &

echo "[start] launching Judge (${JUDGE_MODEL##*/}) on :${JUDGE_PORT}"
"$LLAMA_BIN" \
    -m "$JUDGE_MODEL" \
    --host "$BIND_HOST" --port "$JUDGE_PORT" \
    -c "$JUDGE_CTX" -ngl "$NGL" --parallel 1 \
    --temp 0.1 --top-k 20 --repeat-penalty 1.0 -n 480 \
    --no-mmap &

# In userspace mode the servers bind 127.0.0.1; bridge them onto the tailnet.
if [[ "$USERSPACE" -eq 1 ]]; then
    sleep 5
    tailscale serve --bg --tcp "$THINKER_PORT" "tcp://127.0.0.1:$THINKER_PORT" || true
    tailscale serve --bg --tcp "$JUDGE_PORT"  "tcp://127.0.0.1:$JUDGE_PORT"  || true
fi

# Exit (and let the host restart the container) if either server dies.
wait -n
