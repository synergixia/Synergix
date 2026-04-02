#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════════
# setup_hetzner.sh
# Script de instalación automática para Hetzner CX22 (Ubuntu 24.04)
#
# Uso: bash setup_hetzner.sh
#
# Instala:
#   - Node.js 20 LTS
#   - Python 3.11 + pip
#   - Ollama + Qwen 2.5-1.5B
#   - faster-whisper (transcripción local)
#   - yt-dlp (Agent-Reach YouTube)
#   - PM2 (gestor de procesos)
#   - Dependencias npm (@bnb-chain/greenfield-js-sdk, ethers)
#   - Dependencias Python (aiogram, httpx, etc.)
# ═══════════════════════════════════════════════════════════════════════════════

set -euo pipefail

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${BLUE}[SETUP]${NC} $*"; }
ok()   { echo -e "${GREEN}[OK]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }

APP_DIR="${APP_DIR:-/home/synergix/app}"
OLLAMA_MODEL="qwen2.5:1.5b"

echo ""
echo -e "${BLUE}╔══════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║  SYNERGIX — INSTALACIÓN HETZNER CX22         ║${NC}"
echo -e "${BLUE}║  Qwen 2.5-1.5B | 4GB RAM | 100% Soberano    ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════════╝${NC}"
echo ""

# ── PASO 1: Sistema base ──────────────────────────────────────────────────────
log "Actualizando sistema..."
apt-get update -qq && apt-get upgrade -y -qq
apt-get install -y --no-install-recommends \
    curl wget git build-essential \
    python3 python3-pip python3-venv \
    ffmpeg ca-certificates gnupg \
    nginx supervisor
ok "Sistema base listo"

# ── PASO 2: Node.js 20 LTS ───────────────────────────────────────────────────
if ! command -v node &>/dev/null || [[ $(node --version) != v20* ]]; then
    log "Instalando Node.js 20 LTS..."
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
    apt-get install -y nodejs
fi
ok "Node.js $(node --version) | npm $(npm --version)"

# ── PASO 3: Ollama ────────────────────────────────────────────────────────────
if ! command -v ollama &>/dev/null; then
    log "Instalando Ollama..."
    curl -fsSL https://ollama.com/install.sh | sh
fi

# Configurar Ollama como servicio del sistema
systemctl enable ollama 2>/dev/null || true
systemctl start ollama  2>/dev/null || true
sleep 3

ok "Ollama $(ollama --version 2>/dev/null || echo 'instalado')"

# ── PASO 4: Descargar Qwen 2.5-1.5B ──────────────────────────────────────────
log "Descargando Qwen 2.5-1.5B (aprox. 1.0 GB)..."
ollama pull "$OLLAMA_MODEL"
ok "Qwen 2.5-1.5B listo"

# ── PASO 5: PM2 ──────────────────────────────────────────────────────────────
if ! command -v pm2 &>/dev/null; then
    log "Instalando PM2..."
    npm install -g pm2
fi
ok "PM2 $(pm2 --version)"

# ── PASO 6: Directorios del proyecto ──────────────────────────────────────────
log "Creando estructura de directorios..."
mkdir -p "$APP_DIR"
mkdir -p "$APP_DIR/aisynergix/data"
mkdir -p "$APP_DIR/aisynergix/logs"
mkdir -p "$APP_DIR/aisynergix/SYNERGIXAI"
mkdir -p "$APP_DIR/aisynergix/backups"
chmod -R 755 "$APP_DIR"
ok "Directorios creados en $APP_DIR"

# ── PASO 7: Dependencias npm ─────────────────────────────────────────────────
if [ -f "$APP_DIR/package.json" ]; then
    log "Instalando dependencias npm..."
    cd "$APP_DIR" && npm install --omit=dev
    ok "npm deps instaladas"
else
    warn "No se encontró package.json en $APP_DIR — instala las deps manualmente"
fi

# ── PASO 8: Dependencias Python ───────────────────────────────────────────────
if [ -f "$APP_DIR/requirements.txt" ]; then
    log "Instalando dependencias Python..."
    pip install -r "$APP_DIR/requirements.txt" --break-system-packages -q
    ok "Python deps instaladas"
fi

# ── PASO 9: faster-whisper y yt-dlp ──────────────────────────────────────────
log "Instalando faster-whisper y yt-dlp..."
pip install faster-whisper yt-dlp --break-system-packages -q
ok "faster-whisper + yt-dlp instalados"

# ── PASO 10: Configurar PM2 para arranque automático ─────────────────────────
pm2 startup systemd -u root --hp /root 2>/dev/null || \
pm2 startup 2>/dev/null || true

# ── RESUMEN FINAL ─────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║  ✅ INSTALACIÓN COMPLETADA                   ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════╝${NC}"
echo ""
echo "Próximos pasos:"
echo ""
echo "  1. Copiar archivos del proyecto a $APP_DIR"
echo "  2. Crear .env a partir de .env.example"
echo "  3. Inicializar bucket en Greenfield:"
echo "     cd $APP_DIR && node aisynergix/scripts/init_bucket.js"
echo ""
echo "  4. Arrancar el bot:"
echo "     cd $APP_DIR && pm2 start aisynergix/bot/bot.py --name synergix --interpreter python3"
echo "     pm2 save"
echo ""
echo "  5. Ver logs:"
echo "     pm2 logs synergix"
echo ""
echo "  Modelo IA: Qwen 2.5-1.5B ($(ollama list | grep qwen | head -1 || echo 'instalado'))"
echo "  App dir:   $APP_DIR"
echo ""
