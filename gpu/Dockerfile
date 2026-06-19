# GPU node image: llama.cpp (CUDA) + Tailscale + the Synergix start script.
# Provider-agnostic — works on Vast.ai, RunPod, or any 24 GB GPU Docker host.
#
# Build & push to a registry the host can pull from, then launch it on a 24 GB
# GPU (e.g. RTX 4090). Keep the .gguf model files on persistent instance
# storage mounted at /workspace so they survive restarts.
FROM ghcr.io/ggml-org/llama.cpp:server-cuda

# Tailscale (static binaries via the official install script) plus the few
# utilities start.sh relies on.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl ca-certificates iptables iproute2 \
    && curl -fsSL https://tailscale.com/install.sh | sh \
    && rm -rf /var/lib/apt/lists/*

COPY start.sh /start.sh
RUN chmod +x /start.sh

# Thinker / Judge HTTP ports (reached over the tailnet, not exposed publicly).
EXPOSE 8080 8081

ENTRYPOINT ["/start.sh"]
