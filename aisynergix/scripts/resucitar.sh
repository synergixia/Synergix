#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════════
# aisynergix/scripts/resucitar.sh
# Script Soberano de Recuperación desde DCellar/BNB Greenfield
#
# Uso: bash resucitar.sh [--full] [--model-only] [--db-only]
#
# ¿Qué hace?
#   1. Verifica que Ollama esté corriendo con Qwen 2.5-1.5B
#   2. Restaura la DB local desde el último backup en aisynergix/backups/
#   3. Descarga el cerebro más reciente desde aisynergix/SYNERGIXAI/
#   4. (--full) Restaura el modelo Qwen desde aisynergix/ai/Qwen2.5-1.5B/
#   5. Reinicia el bot con PM2
# ═══════════════════════════════════════════════════════════════════════════════

set -euo pipefail

BLUE='\033[0;34m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

APP_DIR="${APP_DIR:-/home/synergix/app}"
OLLAMA_MODEL="${OLLAMA_MODEL:-qwen2.5:1.5b}"
GF_BUCKET="${GF_BUCKET:-synergix}"
GF_ROOT="aisynergix"

log()  { echo -e "${BLUE}[SYNERGIX]${NC} $*"; }
ok()   { echo -e "${GREEN}[OK]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
fail() { echo -e "${RED}[FAIL]${NC} $*"; exit 1; }

echo ""
echo -e "${BLUE}╔══════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║   SYNERGIX — RESURRECCIÓN SOBERANA           ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════════╝${NC}"
echo ""

# ── Argumentos ────────────────────────────────────────────────────────────────
FULL=false
MODEL_ONLY=false
DB_ONLY=false

for arg in "$@"; do
  case $arg in
    --full)       FULL=true ;;
    --model-only) MODEL_ONLY=true ;;
    --db-only)    DB_ONLY=true ;;
  esac
done

cd "$APP_DIR" || fail "No se encontró el directorio $APP_DIR"

# ── PASO 1: Verificar Ollama ──────────────────────────────────────────────────
log "Verificando Ollama + Qwen 2.5-1.5B..."
if ! command -v ollama &>/dev/null; then
  warn "Ollama no encontrado. Instalando..."
  curl -fsSL https://ollama.com/install.sh | sh
  systemctl enable ollama 2>/dev/null || true
  systemctl start ollama  2>/dev/null || true
  sleep 3
fi

if ! ollama list | grep -q "qwen2.5"; then
  log "Descargando modelo Qwen 2.5-1.5B..."
  ollama pull "$OLLAMA_MODEL"
fi
ok "Ollama + Qwen 2.5-1.5B listo"

if [ "$MODEL_ONLY" = true ]; then
  log "Solo modelo — resurrección completa omitida."
  pm2 restart synergix 2>/dev/null || python aisynergix/bot/bot.py &
  exit 0
fi

# ── PASO 2: Restaurar DB desde Greenfield ─────────────────────────────────────
if [ "$DB_ONLY" = true ] || [ "$FULL" = true ] || [ ! -f "aisynergix/data/synergix_db.json" ]; then
  log "Restaurando DB desde aisynergix/backups/..."

  # Listar backups disponibles y tomar el más reciente
  LATEST_BACKUP=$(node -e "
    require('dotenv').config();
    const { Client } = require('@bnb-chain/greenfield-js-sdk');
    const client = Client.create(
      process.env.GF_RPC_URL || 'https://greenfield-chain.bnbchain.org',
      process.env.GF_CHAIN_ID || '1017'
    );
    (async () => {
      try {
        const res = await client.object.listObjects({
          bucketName: '${GF_BUCKET}',
          query: new URLSearchParams({ prefix: '${GF_ROOT}/backups/', 'max-keys': '10' }),
        });
        const objs = (res.body?.GfSpListObjectsByBucketNameResponse?.Objects || [])
          .map(o => o.ObjectInfo?.ObjectName || '')
          .filter(n => n.endsWith('.bak'))
          .sort()
          .reverse();
        console.log(objs[0] || '');
      } catch(e) { console.log(''); }
    })();
  " 2>/dev/null || echo "")

  if [ -n "$LATEST_BACKUP" ]; then
    log "Backup encontrado: $LATEST_BACKUP"
    mkdir -p aisynergix/data
    node -e "
      require('dotenv').config();
      const { Client } = require('@bnb-chain/greenfield-js-sdk');
      const { ethers } = require('ethers');
      const fs = require('fs');
      const client = Client.create(
        process.env.GF_RPC_URL || 'https://greenfield-chain.bnbchain.org',
        process.env.GF_CHAIN_ID || '1017'
      );
      let pk = process.env.PRIVATE_KEY || '';
      if (!pk.startsWith('0x')) pk = '0x' + pk;
      (async () => {
        const res = await client.object.getObject(
          { bucketName: '${GF_BUCKET}', objectName: '${LATEST_BACKUP}' },
          { type: 'ECDSA', privateKey: pk }
        );
        const buf = Buffer.from(await res.body.arrayBuffer());
        fs.writeFileSync('aisynergix/data/synergix_db.json', buf.toString('utf8'));
        console.log('DB restaurada desde Greenfield');
      })().catch(e => console.error('Error:', e.message));
    "
    ok "DB restaurada desde $LATEST_BACKUP"
  else
    warn "No se encontró backup en Greenfield. Usando DB local si existe."
  fi
fi

# ── PASO 3: Restaurar cerebro desde Greenfield ────────────────────────────────
if [ "$DB_ONLY" = false ]; then
  log "Restaurando cerebro desde aisynergix/SYNERGIXAI/..."
  mkdir -p aisynergix/SYNERGIXAI

  # Usar Jina Reader para leer el cerebro público (más simple)
  BRAIN_URL="https://gnfd-mainnet-sp1.bnbchain.org/view/${GF_BUCKET}/${GF_ROOT}/SYNERGIXAI/Synergix_ia.txt"
  if curl -sf "$BRAIN_URL" -o aisynergix/SYNERGIXAI/Synergix_ia.txt --max-time 30; then
    ok "Cerebro restaurado desde Greenfield"
  else
    warn "No se pudo restaurar el cerebro. Se generará en el próximo ciclo de fusión."
  fi
fi

# ── PASO 4: Verificar dependencias Python ─────────────────────────────────────
log "Verificando dependencias Python..."
pip install -r requirements.txt --break-system-packages -q 2>/dev/null || \
pip install -r requirements.txt -q 2>/dev/null || \
warn "pip install falló — continúa si ya están instaladas"

# ── PASO 5: Crear directorios necesarios ──────────────────────────────────────
mkdir -p aisynergix/data aisynergix/logs aisynergix/SYNERGIXAI
ok "Directorios listos"

# ── PASO 6: Reiniciar bot ─────────────────────────────────────────────────────
log "Reiniciando Synergix con PM2..."
if command -v pm2 &>/dev/null; then
  pm2 restart synergix 2>/dev/null || pm2 start aisynergix/bot/bot.py --name synergix --interpreter python3
  pm2 save 2>/dev/null || true
  ok "Bot reiniciado con PM2"
else
  warn "PM2 no encontrado. Iniciando bot directamente..."
  nohup python3 aisynergix/bot/bot.py > aisynergix/logs/bot.log 2>&1 &
  ok "Bot iniciado (PID: $!)"
fi

echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║   ✅ SYNERGIX RESUCITADO CON ÉXITO           ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════╝${NC}"
echo ""
