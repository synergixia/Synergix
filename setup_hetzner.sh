#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════════
# setup_hetzner.sh — Synergix en Hetzner CX22
# Bucket: synergixai | Modelo: Qwen 2.5-1.5B | Sin APIs externas
# ═══════════════════════════════════════════════════════════════════════════════
set -euo pipefail

GREEN='\033[0;32m'; BLUE='\033[0;34m'; YELLOW='\033[1;33m'; NC='\033[0m'
log()  { echo -e "${BLUE}[SETUP]${NC} $*"; }
ok()   { echo -e "${GREEN}[OK]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }

APP_DIR="${APP_DIR:-/root/Synergix}"
BOT_SCRIPT="$APP_DIR/aisynergix/bot/bot.py"
OLLAMA_MODEL="qwen2.5:1.5b"

echo ""
echo -e "${BLUE}╔═══════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║  SYNERGIX — Hetzner CX22 Setup               ║${NC}"
echo -e "${BLUE}║  Qwen 2.5-1.5B | synergixai bucket           ║${NC}"
echo -e "${BLUE}╚═══════════════════════════════════════════════╝${NC}"
echo ""

# ── Sistema ───────────────────────────────────────────────────────────────────
log "Actualizando sistema..."
apt-get update -qq && apt-get upgrade -y -qq
apt-get install -y --no-install-recommends \
    curl wget git build-essential ffmpeg \
    python3 python3-pip ca-certificates gnupg

# ── Node.js 20 ────────────────────────────────────────────────────────────────
if ! node --version 2>/dev/null | grep -q "v20"; then
    log "Instalando Node.js 20..."
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
    apt-get install -y nodejs
fi
ok "Node $(node --version) | npm $(npm --version)"

# ── Ollama ────────────────────────────────────────────────────────────────────
if ! command -v ollama &>/dev/null; then
    log "Instalando Ollama..."
    curl -fsSL https://ollama.com/install.sh | sh
fi
systemctl enable ollama 2>/dev/null || true
systemctl start ollama  2>/dev/null || true
sleep 3

log "Descargando $OLLAMA_MODEL (~1 GB)..."
ollama pull "$OLLAMA_MODEL"
ok "Qwen 2.5-1.5B listo"

# ── PM2 ───────────────────────────────────────────────────────────────────────
command -v pm2 &>/dev/null || npm install -g pm2
ok "PM2 $(pm2 --version)"

# ── Directorios ───────────────────────────────────────────────────────────────
mkdir -p "$APP_DIR/aisynergix/data"
mkdir -p "$APP_DIR/aisynergix/logs"
mkdir -p "$APP_DIR/aisynergix/SYNERGIXAI"
mkdir -p "$APP_DIR/aisynergix/backups"
ok "Directorios creados"

# ── Dependencias ──────────────────────────────────────────────────────────────
if [ -f "$APP_DIR/package.json" ]; then
    log "npm install..."
    cd "$APP_DIR" && npm install --omit=dev
fi

if [ -f "$APP_DIR/requirements.txt" ]; then
    log "pip install..."
    pip install -r "$APP_DIR/requirements.txt" --break-system-packages -q
fi

pip install faster-whisper yt-dlp --break-system-packages -q
ok "faster-whisper + yt-dlp listos"

# ── Inicializar bucket synergixai en Greenfield (1 sola vez) ─────────────────
if [ -f "$APP_DIR/aisynergix/scripts/init_bucket.js" ]; then
    log "Inicializando bucket synergixai en Greenfield..."
    cd "$APP_DIR" && node aisynergix/scripts/init_bucket.js || \
        warn "init_bucket: algunos objetos ya existen (normal si no es primera vez)"
fi

# ── Registrar en PM2 ──────────────────────────────────────────────────────────
pm2 delete synergix 2>/dev/null || true

log "Registrando bot en PM2..."
cd "$APP_DIR"
pm2 start "$BOT_SCRIPT" \
    --name synergix \
    --interpreter python3 \
    --cwd "$APP_DIR" \
    --restart-delay 3000 \
    --max-restarts 10

pm2 startup systemd -u root --hp /root 2>/dev/null || true
pm2 save
ok "PM2 configurado"

echo ""
echo -e "${GREEN}╔═══════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║  ✅ SYNERGIX LISTO                            ║${NC}"
echo -e "${GREEN}╚═══════════════════════════════════════════════╝${NC}"
echo ""
echo "  pm2 logs synergix       ← ver logs en tiempo real"
echo "  pm2 restart synergix    ← reiniciar bot"
echo "  pm2 status              ← estado general"
echo ""
echo "  Bot:    $BOT_SCRIPT"
echo "  Bucket: synergixai (BNB Greenfield)"
echo "  Modelo: $OLLAMA_MODEL"
echo ""
