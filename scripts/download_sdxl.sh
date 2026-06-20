#!/usr/bin/env bash
#
# download_sdxl.sh — fetch the Stable Diffusion XL base 1.0 checkpoint used by
# the image-gen service. SDXL base is ungated and OpenRAIL-M licensed (commercial
# use allowed), so no Hugging Face token is required.
#
# Usage:
#   ./scripts/download_sdxl.sh
#
# Downloads into aisynergix/ai/models/image/ (gitignored), which is bind-mounted
# into the image-gen container at /models/image.
set -euo pipefail

DEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/aisynergix/ai/models/image"
MODEL_FILE="sd_xl_base_1.0.safetensors"
# Resolve the LFS object directly so we get the real ~6.9 GB weights, not a pointer.
MODEL_URL="https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/resolve/main/${MODEL_FILE}?download=true"

mkdir -p "$DEST_DIR"
DEST_PATH="$DEST_DIR/$MODEL_FILE"

if [[ -f "$DEST_PATH" ]]; then
    echo "✓ Already present: $DEST_PATH"
    exit 0
fi

echo "Downloading SDXL base 1.0 (~6.9 GB) → $DEST_PATH"
echo "This can take a while depending on your connection."

# curl with resume (-C -) so an interrupted download can be re-run.
if command -v curl >/dev/null 2>&1; then
    curl -L --fail --retry 5 --retry-delay 5 -C - -o "$DEST_PATH" "$MODEL_URL"
elif command -v wget >/dev/null 2>&1; then
    wget --continue --tries=5 -O "$DEST_PATH" "$MODEL_URL"
else
    echo "ERROR: need curl or wget installed." >&2
    exit 1
fi

echo "✓ Done. SDXL is ready at $DEST_PATH"
echo "  Rebuild/start the service:  docker compose -f docker/docker-compose.yml up -d --build image-gen"
