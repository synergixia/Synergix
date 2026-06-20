#!/usr/bin/env bash
#
# download_sd15.sh — fetch the Stable Diffusion 1.5 checkpoint used by the
# image-gen service. SD 1.5 is ungated and CreativeML OpenRAIL-M licensed
# (commercial use allowed), so no Hugging Face token is required.
#
# Usage:
#   ./scripts/download_sd15.sh
#
# Downloads into aisynergix/ai/models/image/ (gitignored), which is bind-mounted
# into the image-gen container at /models/image.
set -euo pipefail

DEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/aisynergix/ai/models/image"
MODEL_FILE="v1-5-pruned-emaonly.safetensors"
# Community-maintained, ungated mirror of the original SD 1.5 weights.
MODEL_URL="https://huggingface.co/stable-diffusion-v1-5/stable-diffusion-v1-5/resolve/main/${MODEL_FILE}?download=true"

mkdir -p "$DEST_DIR"
DEST_PATH="$DEST_DIR/$MODEL_FILE"

if [[ -f "$DEST_PATH" ]]; then
    echo "✓ Already present: $DEST_PATH"
    exit 0
fi

echo "Downloading Stable Diffusion 1.5 (~4 GB) → $DEST_PATH"

if command -v curl >/dev/null 2>&1; then
    curl -L --fail --retry 5 --retry-delay 5 -C - -o "$DEST_PATH" "$MODEL_URL"
elif command -v wget >/dev/null 2>&1; then
    wget --continue --tries=5 -O "$DEST_PATH" "$MODEL_URL"
else
    echo "ERROR: need curl or wget installed." >&2
    exit 1
fi

echo "✓ Done. SD 1.5 is ready at $DEST_PATH"
echo "  Rebuild/start the service:  docker compose -f docker/docker-compose.yml up -d --build image-gen"
