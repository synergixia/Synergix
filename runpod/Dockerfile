# RunPod GPU image: llama.cpp (CUDA) + Tailscale + the Synergix start script.
#
# Build & push to a registry RunPod can pull from, then point a RunPod GPU pod
# (24 GB) at it.  Mount a RunPod *network volume* at /workspace so the .gguf
# model files survive pod restarts.
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
