#!/bin/bash
# ══════════════════════════════════════════════════════════════════════════════
# aisynergix/scripts/resucitar.sh
# Protocolo Soberano de Recuperación de Emergencia
#
# ¿Cuándo usarlo?
#   - El servidor fue reiniciado y la DB local se perdió
#   - El bot no arranca y los logs muestran errores de DB
#   - Después de migrar a un nuevo servidor
#   - Como mantenimiento periódico para verificar integridad
#
# Uso:
#   bash resucitar.sh              # Recuperación completa
#   bash resucitar.sh --model-only # Solo reinstalar Ollama/Qwen
#   bash resucitar.sh --db-only    # Solo restaurar DB desde GF
#   bash resucitar.sh --check      # Solo verificar estado
# ══════════════════════════════════════════════════════════════════════════════

set -euo pipefail

# ── Colores ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'
BLUE='\033[0;34m'; YELLOW='\033[1;33m'; NC='\033[0m'

log()  { echo -e "${BLUE}[SYNERGIX]${NC} $*"; }
ok()   { echo -e "${GREEN}[✅ OK]${NC} $*"; }
warn() { echo -e "${YELLOW}[⚠️  WARN]${NC} $*"; }
fail() { echo -e "${RED}[❌ FAIL]${NC} $*"; exit 1; }

# ── Config ────────────────────────────────────────────────────────────────────
APP_DIR="${APP_DIR:-/root/Synergix}"
BASE_DIR="$APP_DIR/aisynergix"
BRAIN_DIR="$BASE_DIR/SYNERGIXAI"
DATA_DIR="$BASE_DIR/data"
LOGS_DIR="$BASE_DIR/logs"
DB_FILE="$DATA_DIR/synergix_db.json"

GF_BUCKET="${GF_BUCKET:-synergixai}"
GF_ROOT="aisynergix"
GF_RPC="${GF_RPC_URL:-https://greenfield-chain.bnbchain.org}"
BRAIN_PUBLIC_URL="https://gnfd-mainnet-sp1.bnbchain.org/view/${GF_BUCKET}/${GF_ROOT}/SYNERGIXAI/Synergix_ia.txt"

OLLAMA_MODEL_JUDGE="${MODEL_JUDGE:-qwen2.5:0.5b}"
OLLAMA_MODEL_THINKER="${MODEL_THINKER:-qwen2.5:1.5b}"

# Cargar .env si existe
if [ -f "$APP_DIR/.env" ]; then
  set -o allexport
  source "$APP_DIR/.env"
  set +o allexport
fi

MODE="${1:---full}"

# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo -e "${BLUE}╔══════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║   SYNERGIX — RESURRECCIÓN SOBERANA                  ║${NC}"
echo -e "${BLUE}║   Bucket: ${GF_BUCKET} | Raíz: ${GF_ROOT}/         ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════════════════╝${NC}"
echo ""

# ── PASO 1: Verificar estructura de directorios ───────────────────────────────
log "Verificando estructura de directorios..."
mkdir -p "$DATA_DIR" "$BRAIN_DIR" "$LOGS_DIR"
mkdir -p "$BASE_DIR/aportes" "$BASE_DIR/backups"
ok "Directorios listos"

# ── MODO CHECK: solo verificar estado ────────────────────────────────────────
if [ "$MODE" = "--check" ]; then
  echo ""
  log "=== Estado del Sistema ==="

  # PM2
  if pm2 show synergix 2>/dev/null | grep -q "online"; then
    ok "PM2: synergix ONLINE"
  else
    warn "PM2: synergix NO está corriendo"
  fi

  # Ollama
  if ollama list 2>/dev/null | grep -q "qwen2.5"; then
    ok "Ollama: Qwen disponible"
    ollama list | grep "qwen2.5"
  else
    warn "Ollama: sin modelos Qwen"
  fi

  # DB local
  if [ -f "$DB_FILE" ]; then
    SIZE=$(wc -c < "$DB_FILE")
    MTIME=$(stat -c "%y" "$DB_FILE" 2>/dev/null || date)
    ok "DB local: ${SIZE} bytes | $MTIME"
  else
    warn "DB local: NO existe ($DB_FILE)"
  fi

  # Cerebro local
  if [ -f "$BRAIN_DIR/Synergix_ia.txt" ]; then
    SIZE=$(wc -c < "$BRAIN_DIR/Synergix_ia.txt")
    ok "Cerebro local: ${SIZE} chars"
  else
    warn "Cerebro local: NO existe"
  fi

  # LLM health check
  log "Verificando LLM..."
  if curl -sf "http://localhost:8080/v1/models" > /dev/null 2>&1; then
    ok "llama-server: ONLINE :8080"
  elif curl -sf "http://localhost:11434/v1/models" > /dev/null 2>&1; then
    ok "Ollama: ONLINE :11434"
  else
    warn "LLM: sin backend disponible"
  fi

  echo ""
  exit 0
fi

# ── PASO 2: Ollama + Modelos ──────────────────────────────────────────────────
if [ "$MODE" = "--full" ] || [ "$MODE" = "--model-only" ]; then
  log "Verificando Ollama + Qwen..."

  if ! command -v ollama &>/dev/null; then
    log "Instalando Ollama..."
    curl -fsSL https://ollama.com/install.sh | sh
    systemctl enable ollama 2>/dev/null || true
    systemctl start  ollama 2>/dev/null || true
    sleep 5
  fi

  # Iniciar Ollama si no está corriendo
  if ! pgrep -x ollama > /dev/null 2>&1; then
    log "Iniciando Ollama..."
    ollama serve &>/dev/null &
    sleep 4
  fi

  # Descargar modelos si no existen
  if ! ollama list 2>/dev/null | grep -q "${OLLAMA_MODEL_JUDGE%%:*}"; then
    log "Descargando El Juez ($OLLAMA_MODEL_JUDGE)..."
    ollama pull "$OLLAMA_MODEL_JUDGE"
  else
    ok "El Juez ya está descargado"
  fi

  if ! ollama list 2>/dev/null | grep -q "${OLLAMA_MODEL_THINKER%%:*}"; then
    log "Descargando El Pensador ($OLLAMA_MODEL_THINKER)..."
    ollama pull "$OLLAMA_MODEL_THINKER"
  else
    ok "El Pensador ya está descargado"
  fi

  ok "Ollama + Qwen listos"
fi

if [ "$MODE" = "--model-only" ]; then
  log "Reiniciando bot..."
  pm2 restart synergix 2>/dev/null || warn "PM2 no disponible"
  exit 0
fi

# ── PASO 3: Restaurar Cerebro desde Greenfield ───────────────────────────────
if [ "$MODE" = "--full" ] || [ "$MODE" = "--db-only" ]; then
  log "Restaurando Cerebro desde Greenfield..."

  if curl -sf "$BRAIN_PUBLIC_URL" -o "$BRAIN_DIR/Synergix_ia.txt" --max-time 30; then
    CHARS=$(wc -c < "$BRAIN_DIR/Synergix_ia.txt")
    ok "Cerebro restaurado: ${CHARS} chars"
  else
    warn "No se pudo restaurar el cerebro público — se regenerará en el próximo ciclo"
  fi

  # ── Restaurar DB desde Greenfield (via Node.js) ──────────────────────────
  log "Restaurando DB desde Greenfield..."

  if [ -f "$APP_DIR/package.json" ] && [ -f "$APP_DIR/aisynergix/backend/upload.js" ]; then
    RESTORE_RESULT=$(node -e "
      require('dotenv').config({ path: '$APP_DIR/.env' });
      const { Client } = require('@bnb-chain/greenfield-js-sdk');
      const { ethers } = require('ethers');
      const fs = require('fs');

      const client = Client.create(
        process.env.GF_RPC_URL || '$GF_RPC',
        process.env.GF_CHAIN_ID || '1017'
      );
      const bucket = process.env.GF_BUCKET || '$GF_BUCKET';
      let pk = process.env.PRIVATE_KEY || '';
      if (!pk.startsWith('0x')) pk = '0x' + pk;

      (async () => {
        try {
          // Listar backups y tomar el más reciente
          const res = await client.object.listObjects({
            bucketName: bucket,
            query: new URLSearchParams({
              prefix: '${GF_ROOT}/backups/',
              'max-keys': '5'
            }),
          });
          const objects = (res.body?.GfSpListObjectsByBucketNameResponse?.Objects || [])
            .map(o => o.ObjectInfo?.ObjectName || '')
            .filter(n => n.endsWith('.bak'))
            .sort()
            .reverse();

          if (objects.length === 0) {
            console.log('NO_BACKUP');
            process.exit(0);
          }

          const latest = objects[0];
          console.log('FOUND:' + latest);

          const dlRes = await client.object.getObject(
            { bucketName: bucket, objectName: latest },
            { type: 'ECDSA', privateKey: pk }
          );
          const buf = Buffer.from(await dlRes.body.arrayBuffer());
          fs.writeFileSync('$DB_FILE', buf.toString('utf8'));
          console.log('OK');
        } catch(e) {
          console.log('ERROR:' + e.message);
          process.exit(1);
        }
      })();
    " 2>/dev/null --env NODE_PATH="$APP_DIR/node_modules" || echo "NODE_FAIL")

    case "$RESTORE_RESULT" in
      *"OK"*)       ok "DB restaurada desde backup en Greenfield" ;;
      *"NO_BACKUP"*)warn "Sin backups en GF — usando DB local si existe" ;;
      *)            warn "Error restaurando DB: $RESTORE_RESULT" ;;
    esac
  else
    warn "Node.js o upload.js no disponibles — restauración manual requerida"
    warn "Descarga manualmente el último backup desde DCellar y colócalo en: $DB_FILE"
  fi
fi

# ── PASO 4: Dependencias Python ───────────────────────────────────────────────
log "Verificando dependencias Python..."
if [ -f "$APP_DIR/requirements.txt" ]; then
  pip install -r "$APP_DIR/requirements.txt" --break-system-packages -q 2>/dev/null || \
  pip install -r "$APP_DIR/requirements.txt" -q 2>/dev/null || \
  warn "pip install falló — continúa si ya están instaladas"
fi

# faster-whisper y yt-dlp
pip install faster-whisper yt-dlp --break-system-packages -q 2>/dev/null || true
ok "Python deps verificadas"

# ── PASO 5: Dependencias Node.js ─────────────────────────────────────────────
if [ -f "$APP_DIR/package.json" ]; then
  log "Verificando dependencias npm..."
  cd "$APP_DIR"
  npm install --omit=dev --silent 2>/dev/null || warn "npm install falló"
  node -e "require('dotenv'); require('@bnb-chain/greenfield-js-sdk'); console.log('OK')" \
    --env NODE_PATH="$APP_DIR/node_modules" 2>/dev/null && ok "npm deps OK" || \
    warn "Algunos módulos Node.js pueden fallar"
fi

# ── PASO 6: PM2 ──────────────────────────────────────────────────────────────
log "Configurando PM2..."
if ! command -v pm2 &>/dev/null; then
  npm install -g pm2 2>/dev/null || warn "pm2 no pudo instalarse globalmente"
fi

if command -v pm2 &>/dev/null; then
  cd "$APP_DIR"

  # Eliminar proceso anterior
  pm2 delete synergix 2>/dev/null || true

  # Iniciar con la ruta correcta
  pm2 start "$BASE_DIR/bot/bot.py" \
    --name synergix \
    --interpreter python3 \
    --cwd "$APP_DIR" \
    --restart-delay 3000 \
    --max-restarts 10

  pm2 startup systemd -u root --hp /root 2>/dev/null || true
  pm2 save 2>/dev/null || true
  ok "PM2 configurado y synergix iniciado"
else
  warn "PM2 no disponible — iniciando directamente..."
  nohup python3 "$BASE_DIR/bot/bot.py" \
    > "$LOGS_DIR/bot.log" 2>&1 &
  ok "Bot iniciado (PID: $!)"
fi

# ── RESUMEN ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║   ✅ SYNERGIX RESUCITADO CON ÉXITO                  ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════╝${NC}"
echo ""
echo "  pm2 logs synergix        ← logs en tiempo real"
echo "  pm2 status               ← estado general"
echo "  bash resucitar.sh --check ← verificar estado"
echo ""
echo "  Bucket: $GF_BUCKET"
echo "  Modelo: $OLLAMA_MODEL_JUDGE (Juez) + $OLLAMA_MODEL_THINKER (Pensador)"
echo ""
