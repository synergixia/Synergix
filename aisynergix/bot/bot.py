# bot/bot.py
"""
Synergix Bot - Producción Completa con Sincronización Greenfield.
Actualiza automáticamente:
  - users/{uid}         → perfil con metadatos (puntos, lang, rol)
  - logs/YYYY-MM-DD_events.log → auditoría inmutable
  - backups/snapshot_YYYYMMDD.bak → cerebro diario
  - SYNERGIXAI/Synergix_ia.txt   → cerebro fusionado (cada 8 min)
  - aportes/YYYY-MM/uid_ts.txt   → aportes de usuarios
"""

import asyncio
import json
import logging

# ── Motor IA local (Qwen 2.5-1.5B via Ollama) — reemplaza Groq completamente ─
from aisynergix.bot.local_ia import (
    chat as _qwen_chat,
    judge as _qwen_judge,
    summarize as _qwen_summarize,
    groq_call, groq_judge, groq_summarize,  # aliases legacy
    health as ollama_health_check,
    warmup as ollama_warmup,
    transcribe_audio,
)

# ── Configuración de paths y prompts ─────────────────────────────────────────
from aisynergix.config.paths import GF, DB_FILE as _DB_FILE, UPLOAD_JS as _UPLOAD_JS
from aisynergix.config.system_prompts import build_system_prompt

# ── Agent-Reach — motor de búsqueda en redes sociales ────────────────────────
try:
    from aisynergix.backend.services.agent_reach import (
        reach_internet, needs_internet_search,
    )
    REACH_AVAILABLE = True
except ImportError:
    try:
        from agent_reach_synergix import (
            reach_internet, needs_internet_search,
        )
        REACH_AVAILABLE = True
    except ImportError:
        REACH_AVAILABLE = False
        async def reach_internet(query, lang="es", **kw): return ""
        def needs_internet_search(query, has_rag_data): return not has_rag_data

def detect_reach_intent(text: str, has_rag_data: bool, msg_type: str) -> tuple[bool, list]:
    """
    Detecta si la query necesita búsqueda en redes sociales en tiempo real.
    Retorna (should_search: bool, platforms: list)

    Lógica:
    - Siempre busca si no hay datos en memoria inmortal (MODO B)
    - Busca si la query menciona redes sociales, noticias, tendencias
    - Busca si pregunta por algo que cambia en tiempo real
    - NO busca en saludos, emojis, preguntas muy simples
    - NO busca si ya hay datos suficientes en memoria Y la pregunta es sobre Synergix
    """
    t = text.lower().strip()

    # Nunca buscar para mensajes triviales
    if msg_type in ("sticker", "simple"):
        return False, []

    # Detectar plataformas específicas mencionadas
    platform_triggers = {
        "twitter":  ["twitter", "x.com", "tweet", "tuit", "trending x", "tendencia twitter"],
        "youtube":  ["youtube", "youtu.be", "video", "vídeo", "canal youtube"],
        "reddit":   ["reddit", "subreddit", "r/"],
        "github":   ["github", "repositorio", "repo", "código fuente", "open source"],
        "telegram": ["telegram", "canal telegram", "t.me", "grupo telegram"],
        "tiktok":   ["tiktok", "tik tok", "viral tiktok"],
        "web":      ["noticia", "noticias", "news", "artículo", "blog", "web", "página",
                     "busca en", "search for", "find online", "precio", "price"],
    }

    # Palabras que indican tiempo real / actualidad
    realtime_triggers = [
        "ahora", "hoy", "último", "últimas", "reciente", "recientes", "tendencia",
        "tendencias", "viral", "trending", "now", "today", "latest", "recent",
        "trending", "viral", "news", "noticias", "2024", "2025", "2026",
        "esta semana", "este mes", "this week", "this month", "live", "en vivo",
        "最新", "今天", "现在", "趋势", "最近",
    ]

    # Temas que siempre necesitan internet (precios, eventos, personas)
    internet_topics = [
        "precio de", "price of", "cotización", "rate", "valor actual",
        "quién es", "who is", "qué pasó", "what happened", "cuándo fue",
        "cuando fue", "when was", "dónde está", "where is",
        "review", "opinión sobre", "opinion about", "comparar", "compare",
        "vs", "mejor que", "better than",
    ]

    # Detectar plataformas explícitas
    platforms_to_search = []
    for platform, triggers in platform_triggers.items():
        if any(tr in t for tr in triggers):
            platforms_to_search.append(platform)

    # Si mencionó plataformas específicas → buscar solo esas
    if platforms_to_search:
        return True, platforms_to_search

    # Si pregunta sobre tiempo real → buscar en todas
    if any(tr in t for tr in realtime_triggers):
        return True, ["web", "twitter", "youtube", "reddit", "telegram", "tiktok"]

    # Si pregunta sobre internet topics → buscar en web + redes
    if any(tr in t for tr in internet_topics):
        return True, ["web", "twitter", "reddit", "youtube"]

    # Si no hay datos en memoria → buscar en todo
    if not has_rag_data:
        return True, ["web", "twitter", "youtube", "github", "reddit", "telegram", "tiktok"]

    return False, []
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential, before_sleep_log

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, Message, ReplyKeyboardMarkup,
)
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(dotenv_path=os.path.join(BASE_DIR, "backend", ".env"))
load_dotenv(dotenv_path=os.path.join(BASE_DIR, ".env"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("synergix.bot")

TOKEN          = os.getenv("TELEGRAM_TOKEN", "")
# ── Qwen 2.5-1.5B local — 100% soberano, sin APIs externas ──────────────────
GROQ_API_KEY   = ""  # Eliminado — ya no existe
OLLAMA_BASE    = os.getenv("OLLAMA_BASE",  "http://localhost:11434")
MODEL_CHAT     = os.getenv("OLLAMA_MODEL", "qwen2.5:1.5b")
MODEL_FAST     = MODEL_CHAT
MODEL_SLOW     = MODEL_CHAT
MAX_TOKENS_CHAT  = int(os.getenv("MAX_TOKENS_CHAT",  "400"))   # 1.5B es rápido
MAX_TOKENS_JUDGE = int(os.getenv("MAX_TOKENS_JUDGE", "150"))
MAX_TOKENS_SUM   = int(os.getenv("MAX_TOKENS_SUM",   "60"))
# En HF Spaces el WORKDIR es /app — la DB vive siempre en /app/data/
DB_FILE = os.path.join(
    os.getenv("DATA_DIR", os.path.join(BASE_DIR, "data")),
    "synergix_v2.json"
)
UPLOAD_JS      = os.path.join(BASE_DIR, "backend", "upload.js")
CTX_MAX        = 20
MIN_CHARS      = 20

# ── Desarrollador / Master ────────────────────────────────────────────────────
MASTER_UIDS: set[int] = {
    int(uid.strip())
    for uid in os.getenv("MASTER_UIDS", "").split(",")
    if uid.strip().isdigit()
}

# ── Tabla de rangos completa (spec oficial) ───────────────────────────────────
# nivel: (pts_min, multiplicador, peso_fusion, limite_dia, nombre_key)
RANK_TABLE = [
    (    0, 1.0, 1.0,   5, "rank_1"),   # Iniciado
    (  100, 1.1, 1.1,  12, "rank_2"),   # Activo
    (  500, 1.5, 1.5,  25, "rank_3"),   # Sincronizado
    ( 1500, 2.5, 2.5,  40, "rank_4"),   # Arquitecto
    ( 5000, 3.0, 3.0,  60, "rank_5"),   # Mente Colmena
    (15000, 5.0, 5.0, 999, "rank_6"),   # Oráculo
]

def get_rank_info(pts: int, uid: int = 0) -> dict:
    """Retorna info completa del rango actual del usuario."""
    if uid in MASTER_UIDS or pts >= 15000:
        return {"level": 5, "key": "rank_6", "multiplier": 5.0,
                "fusion_weight": 5.0, "daily_limit": 999,
                "min_pts": 15000, "next_pts": None}
    for i in range(len(RANK_TABLE) - 1, -1, -1):
        min_pts, mult, fw, dlimit, key = RANK_TABLE[i]
        if pts >= min_pts:
            next_pts = RANK_TABLE[i+1][0] if i+1 < len(RANK_TABLE) else None
            return {"level": i, "key": key, "multiplier": mult,
                    "fusion_weight": fw, "daily_limit": dlimit,
                    "min_pts": min_pts, "next_pts": next_pts}
    return {"level": 0, "key": "rank_1", "multiplier": 1.0,
            "fusion_weight": 1.0, "daily_limit": 5,
            "min_pts": 0, "next_pts": 100}

def calc_points(base: int, pts: int, uid: int = 0) -> int:
    """Calcula puntos finales aplicando multiplicador del rango."""
    info = get_rank_info(pts, uid)
    return round(base * info["multiplier"])

os.makedirs(os.path.join(BASE_DIR, "data"), exist_ok=True)

bot = Bot(token=TOKEN)
dp  = Dispatcher(storage=MemoryStorage())

class Form(StatesGroup):
    waiting_contribution = State()

# ═══════════════════════════════════════════════════════════════════════════════
# DB LOCAL
# ═══════════════════════════════════════════════════════════════════════════════

def load_db() -> dict:
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "reputation": {}, "memory": {}, "chat": {},
        "global_stats": {
            "total_contributions": 0,
            "challenge": "Mejor estrategia DeFi 2026",
            "collective_wisdom": "Sincronizando con la red...",
        },
    }

_save_count = 0
# ═══════════════════════════════════════════════════════════════════════════════
# GF PERSISTENCE — Greenfield como disco duro de Synergix
# La DB local es un write-buffer. GF es la fuente de verdad.
# ═══════════════════════════════════════════════════════════════════════════════

GF_DB_OBJECT    = "data/synergix_db.json"

def uid_hash(uid) -> str:
    """
    Convierte UID de Telegram en hash irreversible de 16 chars.
    Usado en nombres de archivos y metadatos GF para no exponer UIDs.
    El UID real sigue viviendo SOLO en la DB local (synergix_db.json).
    """
    import hashlib
    return hashlib.sha256(f"synergix_salt_{uid}".encode()).hexdigest()[:16]     # DB completa en GF
_gf_sync_dirty  = False                         # True cuando hay cambios sin subir a GF
_gf_sync_count  = 0                             # Contador de writes desde último sync


def save_db() -> None:
    """
    Guarda la DB localmente (write-buffer) y marca para sync a GF.
    El sync real ocurre en federation_loop cada 8 min.
    """
    global _save_count, _gf_sync_dirty, _gf_sync_count
    try:
        os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
        tmp_path = DB_FILE + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(db, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, DB_FILE)
        _save_count  += 1
        _gf_sync_dirty = True
        _gf_sync_count += 1
        # Backup local cada 50 saves
        if _save_count % 50 == 0:
            import shutil as _shutil
            _shutil.copy2(DB_FILE, DB_FILE.replace(".json", "_backup.json"))
    except Exception as e:
        logger.error("Error guardando DB local: %s", e)


async def sync_db_to_gf() -> None:
    """
    Sube la DB completa a GF como data/synergix_db.json.
    Solo si hubo cambios desde el último sync (_gf_sync_dirty).
    Se llama desde federation_loop.
    Esto garantiza que si el servidor muere, la DB se puede restaurar desde GF.
    """
    global _gf_sync_dirty, _gf_sync_count

    if not _gf_sync_dirty:
        logger.debug("⏭️  DB sync: sin cambios")
        return

    try:
        db_json    = json.dumps(db, ensure_ascii=False)
        ts_tag     = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        users_cnt  = len(db.get("reputation", {}))
        contribs   = db.get("global_stats", {}).get("total_contributions", 0)

        metadata = {
            "x-amz-meta-last-sync":  ts_tag,
            "x-amz-meta-users":      str(users_cnt),
            "x-amz-meta-total":      str(contribs),
            "x-amz-meta-type":       "database",
        }

        # Usar nombre versionado para historial
        versioned = GF.db_versioned(datetime.now().strftime("%Y%m%d_%H%M%S"))

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            lambda: gf_upload(db_json, versioned, metadata, uid="system",
                             upsert=False, only_tags=False)
        )

        # ✅ También actualizar data/synergix_db.json (nombre FIJO, público)
        # El sitio web lee este archivo directamente desde Greenfield.
        # Marcar como público en DCellar una sola vez — el upsert mantiene visibilidad.
        public_metadata = {**metadata, "x-amz-meta-public": "true"}
        await loop.run_in_executor(
            None,
            lambda: gf_upload(db_json, GF_DB_OBJECT, public_metadata, uid="system",
                             upsert=True, only_tags=False)
        )
        logger.info("✅ DB pública actualizada: %s (%d users, %d contribs)",
                    GF_DB_OBJECT, users_cnt, contribs)

        # Actualizar puntero en DB local
        db["global_stats"]["gf_db_latest"] = versioned
        # Guardar localmente sin marcar dirty otra vez
        tmp = DB_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(db, f, indent=2, ensure_ascii=False)
        os.replace(tmp, DB_FILE)

        _gf_sync_dirty  = False
        _gf_sync_count  = 0
        logger.info("✅ DB sincronizada a GF: %s (%d users, %d aportes)",
                    versioned, users_cnt, contribs)

    except Exception as e:
        logger.error("❌ Error sincronizando DB a GF: %s", e)


async def restore_db_from_gf() -> bool:
    """
    Al arrancar: descarga la DB más reciente de GF y restaura el estado.
    Prioridad:
      1. data/synergix_db_LATEST.json (puntero en DB local si existe)
      2. DB local actual (si es más reciente)
    Retorna True si se restauró desde GF.
    """
    # Ver si hay un puntero al último backup en GF
    latest_gf = db.get("global_stats", {}).get("gf_db_latest", "")

    if not latest_gf:
        logger.info("ℹ️  restore_db: sin puntero GF, usando DB local")
        return False

    # Comparar timestamps: GF vs local
    try:
        # Extraer timestamp del nombre del archivo GF
        # Formato: data/synergix_db_20260317_213000.json
        import re as _re
        match = _re.search(r"(\d{8}_\d{6})", latest_gf)
        if match:
            gf_ts_str = match.group(1)
            gf_dt = datetime.strptime(gf_ts_str, "%Y%m%d_%H%M%S")
            local_mtime = os.path.getmtime(DB_FILE) if os.path.exists(DB_FILE) else 0
            local_dt = datetime.fromtimestamp(local_mtime)

            if local_dt >= gf_dt:
                logger.info("ℹ️  restore_db: DB local más reciente que GF (%s >= %s)",
                            local_dt.strftime("%H:%M:%S"), gf_dt.strftime("%H:%M:%S"))
                return False
    except Exception:
        pass

    # Descargar desde GF
    logger.info("🌐 restore_db: descargando %s desde GF...", latest_gf)
    loop = asyncio.get_running_loop()
    try:
        raw = await loop.run_in_executor(None, _download_from_gf, latest_gf)
        if not raw or len(raw) < 50:
            logger.warning("⚠️  restore_db: descarga vacía")
            return False

        gf_db = json.loads(raw)
        # Validar estructura mínima
        if not isinstance(gf_db, dict) or "reputation" not in gf_db:
            logger.warning("⚠️  restore_db: estructura inválida")
            return False

        # ── Merge inteligente: nunca perder puntos ganados ─────────────────────
        # Sin merge: GF tiene versión de hace 8 min. Si un usuario ganó puntos
        # en ese intervalo y el bot se reinicia, sobreescribir con GF los borra.
        # Solución: para cada usuario tomar el MÁXIMO de puntos y contribuciones.

        local_rep      = db.get("reputation", {})
        gf_rep         = gf_db.get("reputation", {})
        local_contribs = db.get("global_stats", {}).get("total_contributions", 0)
        gf_contribs    = gf_db.get("global_stats", {}).get("total_contributions", 0)
        local_settings = db.get("user_settings", {})

        # Base: GF (puede tener usuarios que no están en local)
        merged_rep = dict(gf_rep)

        # Para cada usuario en local, conservar el MÁXIMO de puntos
        for uid_s, local_data in local_rep.items():
            if uid_s in merged_rep:
                gf_pts    = merged_rep[uid_s].get("points", 0)
                local_pts = local_data.get("points", 0)
                gf_contrib    = merged_rep[uid_s].get("contributions", 0)
                local_contrib = local_data.get("contributions", 0)
                if local_pts > gf_pts or local_contrib > gf_contrib:
                    # Local más actualizado para este usuario → conservar
                    merged_rep[uid_s] = {
                        "points":        max(local_pts, gf_pts),
                        "contributions": max(local_contrib, gf_contrib),
                        "impact":        max(
                            local_data.get("impact", 0),
                            merged_rep[uid_s].get("impact", 0)
                        ),
                    }
                    logger.debug("🔒 Merge uid %s: pts local=%d GF=%d → %d",
                                 uid_s, local_pts, gf_pts, merged_rep[uid_s]["points"])
            else:
                # Usuario solo en local (registrado en los últimos 8 min)
                merged_rep[uid_s] = local_data

        # Tomar el máximo total_contributions
        merged_contribs = max(local_contribs, gf_contribs)

        # Merge user_settings: conservar entradas locales que GF no tiene
        merged_settings = dict(gf_db.get("user_settings", {}))
        for uid_s, s in local_settings.items():
            if uid_s not in merged_settings:
                merged_settings[uid_s] = s

        # Actualizar DB: base GF + reputación y settings mergeados
        db.clear()
        db.update(gf_db)
        db["reputation"]                          = merged_rep
        db["user_settings"]                       = merged_settings
        db["global_stats"]["total_contributions"] = merged_contribs

        # Guardar localmente
        tmp = DB_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(db, f, indent=2, ensure_ascii=False)
        os.replace(tmp, DB_FILE)

        logger.info("✅ DB merge inteligente: %d users, %d aportes "
                    "(GF: %d/%d | local: %d/%d)",
                    len(merged_rep), merged_contribs,
                    len(gf_rep), gf_contribs,
                    len(local_rep), local_contribs)
        return True

    except Exception as e:
        logger.error("❌ restore_db error: %s", e)
        return False




db = load_db()
# Se cargan desde DB al arrancar — persisten entre reinicios
user_lang:      dict[int, str] = {}
welcomed_users: set[int]       = set()

def _load_session_from_db() -> None:
    """Restaura user_lang y welcomed_users desde la DB local al iniciar."""
    # Garantizar que los masters tengan Oráculo siempre
    for master_uid in MASTER_UIDS:
        uid_str = str(master_uid)
        if uid_str not in db["reputation"]:
            db["reputation"][uid_str] = {"points": 9999, "contributions": 0, "impact": 0}
        else:
            db["reputation"][uid_str]["points"] = max(
                db["reputation"][uid_str].get("points", 0), 9999
            )
        save_db()

    for uid_str, rep in db.get("reputation", {}).items():
        try:
            uid = int(uid_str)
            welcomed_users.add(uid)
            lang = db.get("user_settings", {}).get(uid_str, {}).get("lang")
            if lang and lang in T:
                user_lang[uid] = lang
        except (ValueError, TypeError):
            pass

# ═══════════════════════════════════════════════════════════════════════════════
# TRADUCCIONES
# ═══════════════════════════════════════════════════════════════════════════════
T = {
    "es": {
        "welcome":          "¡Bienvenido, {name}! 🌟\n\nSoy Synergix, inteligencia colectiva descentralizada.\nTu conocimiento se guarda para siempre en BNB Greenfield y evoluciona nuestra IA. 🔗\n\n🏆 Challenge de la semana:\n{challenge}\n\nNo usas una app. Construyes una memoria comunitaria viva. 🚀",
        "welcome_back":     "¡Hola de nuevo, {name}! 🔥\n\n¿Qué conocimiento anclaremos hoy? 🚀",
        "btn_contribute":   "🔥 Contribuir",
        "btn_status":       "📊 Ver estado",
        "btn_language":     "🌐 Idioma",
        "btn_memory":       "🧠 Mi memoria",
        "select_lang":      "🌐 Elige tu idioma:",
        "lang_set":         "✅ Idioma configurado a Español 🇪🇸",
        "await_contrib":    "🎯 Modo aporte activado!\n\nEscribe tu idea o envía una nota de voz. Quedará grabado en la red para siempre. 💡\n\nMínimo 20 caracteres.",
        "contrib_ok":       "¡Gracias, {name}! 🌟\n\nTu aporte forma parte de la Memoria Inmortal Synergix 🔗\nCID: {cid}\n\nTu conocimiento vive para siempre y fortalece la red. 🔥",
        "contrib_elite":    "\n\n⭐ ¡Aporte de élite! Score {score}/10 → +20 puntos",
        "contrib_bonus":    "\n\n🏆 ¡Relacionado al Challenge semanal! +5 puntos extra.",
        "contrib_fail":     "⚠️ Hubo un problema al guardar tu aporte. Inténtalo de nuevo.",
        "contrib_short":    "🤔 Muy corto ({chars} chars). Mínimo 20 caracteres. 🔥",
        "contrib_rejected": "🤔 Aporte con poca profundidad (score: {score}/10).\n\n💡 {reason}\n\nAmplía tu idea. 🔥",
        "no_memory":        "🧠 Sin aportes aún. ¡Contribuye para dejar tu huella! 🔥",
        "memory_title":     "🧠 Tu legado en la Memoria Inmortal Synergix:\n\n",
        "memory_footer":    "\n\n📈 Score: {pts} pts | Contribuciones: {contribs}",
        "error":            "⚠️ Problema temporal. Inténtalo de nuevo. 🔄",
        "status_msg":       "📊 Synergix Inteligencia Colectiva\n\n📦 Aportes Inmortales: {total}\n🏆 Challenge:\n{challenge}\n\n── Tu Impacto, {name} ──\n📈 Score: {pts} pts\n🔗 Contribuciones: {contribs}\n🔁 Usos de tus aportes: {impact}\n🏅 Rango: {rank}\n💡 Beneficio: {benefit}",
        "rank_1": "🌱 Iniciado", "rank_2": "📈 Activo", "rank_3": "🧬 Sincronizado",
        "rank_4": "🏗️ Arquitecto", "rank_5": "🧠 Mente Colmena", "rank_6": "🔮 Oráculo",
        "challenge_text":   "Mejor estrategia DeFi 2026",
        "benefit_1": "Envío de aportes básicos a la red",
        "benefit_2": "Acceso a Challenges mensuales 🏆",
        "benefit_3": "Prioridad de procesamiento en el RAG ⚡",
        "benefit_4": "Tus aportes pesan más en el Fusion Brain 🧠",
        "benefit_5": "Puedes validar o rechazar ideas de otros 🗳️",
        "benefit_6": "Influencia máxima sobre la inteligencia colectiva 🌐",
        "received":         "¡Recibido! Tu sabiduría está siendo procesada e inmortalizada. 🔗",
        "transcribing":     "🎙️ Transcribiendo tu nota de voz...",
    },
    "en": {
        "welcome":          "Welcome, {name}! 🌟\n\nI'm Synergix, decentralized collective intelligence.\nYour knowledge is saved forever on BNB Greenfield. 🔗\n\n🏆 Weekly Challenge:\n{challenge}\n\nYou're building a living community memory. 🚀",
        "welcome_back":     "Welcome back, {name}! 🔥\n\nWhat knowledge will we anchor today? 🚀",
        "btn_contribute":   "🔥 Contribute",
        "btn_status":       "📊 Status",
        "btn_language":     "🌐 Language",
        "btn_memory":       "🧠 My memory",
        "select_lang":      "🌐 Choose your language:",
        "lang_set":         "✅ Language set to English 🇬🇧",
        "await_contrib":    "🎯 Contribution mode activated!\n\nWrite your idea or send a voice note. 💡\n\nMinimum 20 characters.",
        "contrib_ok":       "Thank you, {name}! 🌟\n\nYour contribution is now part of the Immortal Synergix Memory 🔗\nCID: {cid}\n\nYour knowledge lives forever. 🔥",
        "contrib_elite":    "\n\n⭐ Elite contribution! Score {score}/10 → +20 points",
        "contrib_bonus":    "\n\n🏆 Related to the weekly Challenge! +5 extra points.",
        "contrib_fail":     "⚠️ Problem saving your contribution. Please try again.",
        "contrib_short":    "🤔 Too short ({chars} chars). Minimum 20 characters. 🔥",
        "contrib_rejected": "🤔 Needs more depth (score: {score}/10).\n\n💡 {reason}\n\nExpand your idea. 🔥",
        "no_memory":        "🧠 No contributions yet. Contribute to leave your mark! 🔥",
        "memory_title":     "🧠 Your legacy in the Immortal Synergix Memory:\n\n",
        "memory_footer":    "\n\n📈 Score: {pts} pts | Contributions: {contribs}",
        "error":            "⚠️ Temporary issue. Please try again. 🔄",
        "status_msg":       "📊 Synergix Collective Intelligence\n\n📦 Immortal Contributions: {total}\n🏆 Challenge:\n{challenge}\n\n── Your Impact, {name} ──\n📈 Score: {pts} pts\n🔗 Contributions: {contribs}\n🔁 Times used by community: {impact}\n🏅 Rank: {rank}\n💡 Benefit: {benefit}",
        "rank_1": "🌱 Initiate", "rank_2": "📈 Active", "rank_3": "🧬 Synchronized",
        "rank_4": "🏗️ Architect", "rank_5": "🧠 Hive Mind", "rank_6": "🔮 Oracle",
        "challenge_text":   "Best DeFi Strategy 2026",
        "benefit_1": "Send basic contributions to the network",
        "benefit_2": "Access to monthly Challenges 🏆",
        "benefit_3": "Priority processing in the RAG ⚡",
        "benefit_4": "Your contributions weigh more in the Fusion Brain 🧠",
        "benefit_5": "You can validate or reject others' ideas 🗳️",
        "benefit_6": "Maximum influence over collective intelligence 🌐",
        "received":         "Received! Your wisdom is being processed and immortalized. 🔗",
        "transcribing":     "🎙️ Transcribing your voice note...",
    },
    "zh_cn": {
        "welcome":          "欢迎，{name}！🌟\n\n我是 Synergix，去中心化集体智慧。\n您的知识永久保存在 BNB Greenfield。🔗\n\n🏆 本周挑战：\n{challenge}\n\n您正在建立活生生的社区记忆。🚀",
        "welcome_back":     "欢迎回来，{name}！🔥\n\n今天锚定什么知识？🚀",
        "btn_contribute":   "🔥 贡献", "btn_status": "📊 查看状态", "btn_language": "🌐 语言", "btn_memory": "🧠 我的记忆",
        "select_lang":      "🌐 选择语言：", "lang_set": "✅ 语言设定为简体中文 🇨🇳",
        "await_contrib":    "🎯 贡献模式已启动！\n\n写下想法或发送语音。💡\n\n最少20个字符。",
        "contrib_ok":       "谢谢，{name}！🌟\n\n贡献已永久保存 🔗\nCID：{cid}\n\n您的知识永远存在。🔥",
        "contrib_elite":    "\n\n⭐ 精英贡献！评分 {score}/10 → +20 分",
        "contrib_bonus":    "\n\n🏆 与每周挑战相关！+5 分。",
        "contrib_fail":     "⚠️ 保存失败，请重试。",
        "contrib_short":    "🤔 太短（{chars} 字符）。最少20字符。🔥",
        "contrib_rejected": "🤔 需要更多深度（{score}/10）。\n💡 {reason}\n🔥",
        "no_memory":        "🧠 尚无贡献。立即贡献！🔥",
        "memory_title":     "🧠 Synergix 不朽记忆：\n\n",
        "memory_footer":    "\n\n📈 总分：{pts} 分 | 贡献：{contribs}",
        "error":            "⚠️ 临时问题，请重试。🔄",
        "status_msg":       "📊 Synergix 集体智慧\n\n📦 不朽贡献：{total}\n🏆 挑战：\n{challenge}\n\n── {name} 的影响力 ──\n📈 分数：{pts}\n🔗 贡献：{contribs}\n🔁 被使用次数：{impact}\n🏅 等级：{rank}\n💡 权益：{benefit}",
        "rank_1": "🌱 入门", "rank_2": "📈 活跃", "rank_3": "🧬 同步者",
        "rank_4": "🏗️ 架构师", "rank_5": "🧠 蜂巢思维", "rank_6": "🔮 神谕",
        "challenge_text":   "2026年最佳DeFi策略",
        "benefit_1": "向網路發送基本貢獻",
        "benefit_2": "參與每月挑戰 🏆",
        "benefit_3": "RAG處理優先權 ⚡",
        "benefit_4": "您的貢獻在融合大腦中權重更高 🧠",
        "benefit_5": "可以驗證或拒絕他人的想法 🗳️",
        "benefit_6": "對集體智慧的最大影響力 🌐",
        "benefit_1": "向网络发送基本贡献",
        "benefit_2": "参与每月挑战 🏆",
        "benefit_3": "RAG处理优先权 ⚡",
        "benefit_4": "您的贡献在融合大脑中权重更高 🧠",
        "benefit_5": "可以验证或拒绝他人的想法 🗳️",
        "benefit_6": "对集体智慧的最大影响力 🌐",
        "received":         "已收到！正在处理。🔗",
        "transcribing":     "🎙️ 转录中...",
    },
    "zh": {
        "welcome":          "歡迎，{name}！🌟\n\n我是 Synergix，去中心化集體智慧。\n您的知識永久保存在 BNB Greenfield。🔗\n\n🏆 本週挑戰：\n{challenge}\n\n您正在建立活生生的社群記憶。🚀",
        "welcome_back":     "歡迎回來，{name}！🔥\n\n今天錨定什麼知識？🚀",
        "btn_contribute":   "🔥 貢獻", "btn_status": "📊 查看狀態", "btn_language": "🌐 語言", "btn_memory": "🧠 我的記憶",
        "select_lang":      "🌐 選擇語言：", "lang_set": "✅ 語言設定為繁體中文 🇹🇼",
        "await_contrib":    "🎯 貢獻模式已啟動！\n\n寫下想法或發送語音。💡\n\n最少20個字元。",
        "contrib_ok":       "謝謝，{name}！🌟\n\n貢獻已永久保存 🔗\nCID：{cid}\n\n您的知識永遠存在。🔥",
        "contrib_elite":    "\n\n⭐ 精英貢獻！評分 {score}/10 → +20 分",
        "contrib_bonus":    "\n\n🏆 與每週挑戰相關！+5 分。",
        "contrib_fail":     "⚠️ 儲存失敗，請重試。",
        "contrib_short":    "🤔 太短（{chars} 字元）。最少20字元。🔥",
        "contrib_rejected": "🤔 需要更多深度（{score}/10）。\n💡 {reason}\n🔥",
        "no_memory":        "🧠 尚無貢獻。立即貢獻！🔥",
        "memory_title":     "🧠 Synergix 不朽記憶：\n\n",
        "memory_footer":    "\n\n📈 總分：{pts} 分 | 貢獻：{contribs}",
        "error":            "⚠️ 暫時問題，請重試。🔄",
        "status_msg":       "📊 Synergix 集體智慧\n\n📦 不朽貢獻：{total}\n🏆 挑戰：\n{challenge}\n\n── {name} 的影響力 ──\n📈 分數：{pts}\n🔗 貢獻：{contribs}\n🔁 被使用次數：{impact}\n🏅 等級：{rank}\n💡 權益：{benefit}",
        "rank_1": "🌱 入門", "rank_2": "📈 活躍", "rank_3": "🧬 同步者",
        "rank_4": "🏗️ 架構師", "rank_5": "🧠 蜂巢思維", "rank_6": "🔮 神諭",
        "challenge_text":   "2026年最佳DeFi策略",
        "benefit_1": "向網路發送基本貢獻",
        "benefit_2": "參與每月挑戰 🏆",
        "benefit_3": "RAG處理優先權 ⚡",
        "benefit_4": "您的貢獻在融合大腦中權重更高 🧠",
        "benefit_5": "可以驗證或拒絕他人的想法 🗳️",
        "benefit_6": "對集體智慧的最大影響力 🌐",
        "benefit_1": "向网络发送基本贡献",
        "benefit_2": "参与每月挑战 🏆",
        "benefit_3": "RAG处理优先权 ⚡",
        "benefit_4": "您的贡献在融合大脑中权重更高 🧠",
        "benefit_5": "可以验证或拒绝他人的想法 🗳️",
        "benefit_6": "对集体智慧的最大影响力 🌐",
        "received":         "已收到！正在處理。🔗",
        "transcribing":     "🎙️ 轉錄中...",
    },
}

# CHALLENGE_KW se carga dinámicamente desde get_challenge_keywords()
# Ver función get_active_challenge() y get_challenge_keywords() para la lista rotativa

# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS IDIOMA
# ═══════════════════════════════════════════════════════════════════════════════

def get_lang(uid: int, tg: str = "") -> str:
    if uid in user_lang: return user_lang[uid]
    tg = (tg or "").lower()
    if tg.startswith("zh-hant") or tg == "zh-tw": lang = "zh"
    elif tg.startswith("zh"): lang = "zh_cn"
    elif tg.startswith("en"): lang = "en"
    else: lang = "es"
    user_lang[uid] = lang
    _save_user_setting(uid, "lang", lang)
    return lang

def _save_user_setting(uid: int, key: str, value: str) -> None:
    """Persiste un setting de usuario en la DB."""
    uid_str = str(uid)
    if "user_settings" not in db:
        db["user_settings"] = {}
    if uid_str not in db["user_settings"]:
        db["user_settings"][uid_str] = {}
    db["user_settings"][uid_str][key] = value
    save_db()

def t(uid: int, key: str, **kw) -> str:
    lang = user_lang.get(uid, "es")
    text = T.get(lang, T["es"]).get(key, key)
    return text.format(**kw) if kw else text

def sync_lang(uid: int, text: str) -> None:
    for lang, tr in T.items():
        for k in ["btn_contribute","btn_status","btn_language","btn_memory"]:
            if text == tr.get(k):
                user_lang[uid] = lang
                _save_user_setting(uid, "lang", lang)
                return

def menu(uid: int) -> ReplyKeyboardMarkup:
    tx = T.get(user_lang.get(uid, "es"), T["es"])
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=tx["btn_contribute"]), KeyboardButton(text=tx["btn_status"])],
                  [KeyboardButton(text=tx["btn_memory"]),    KeyboardButton(text=tx["btn_language"])]],
        resize_keyboard=True, is_persistent=True)

BTN_CONTRIBUTE = {T[l]["btn_contribute"] for l in T}
BTN_STATUS     = {T[l]["btn_status"]     for l in T}
BTN_MEMORY     = {T[l]["btn_memory"]     for l in T}
BTN_LANG       = {T[l]["btn_language"]   for l in T}

# ═══════════════════════════════════════════════════════════════════════════════
# GREENFIELD BRIDGE — función genérica de upload via Node.js
# ═══════════════════════════════════════════════════════════════════════════════

def _is_already_exists(exc: Exception) -> bool:
    """True si el error es 'Object already exists' — no tiene sentido reintentar."""
    return "already exists" in str(exc).lower() or "object already exists" in str(exc).lower()

@retry(
    retry=retry_if_exception(lambda e: not _is_already_exists(e)),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, min=1, max=16),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
def gf_upload(content: str, object_name: str, metadata: dict = None,
              uid: str = "system", upsert: bool = False, only_tags: bool = False) -> dict:
    """
    Interfaz Python → Node.js para operaciones Greenfield.

    Modos:
      upsert=False  → uploadToGreenfield (crear, falla si existe)
      upsert=True, only_tags=False → upsertObject con updateObjectContent si existe
      upsert=True, only_tags=True  → upsertObject solo actualizando tags (más barato)

    Greenfield usa TAGS on-chain (no x-amz-meta headers) para metadatos actualizables.
    """
    meta_json  = json.dumps(metadata or {})
    only_tags_js = "true" if only_tags else "false"

    # Construir script Node según el modo
    upload_js_esc   = UPLOAD_JS.replace("\\", "\\\\").replace("'", "\\'")
    object_name_esc = object_name.replace("'", "\\'")

    if upsert:
        # Necesitamos pasar el contenido via archivo temporal
        if len(content.encode("utf-8")) < 32:
            content = f"# Synergix | {object_name} | {int(time.time())}\n{content}"
        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt", mode="w", encoding="utf-8") as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        tmp_path_esc = tmp_path.replace("\\", "\\\\").replace("'", "\\'")
        node_script = f"""
const {{ upsertObject }} = require('{upload_js_esc}');
const fs = require('fs');
const content = fs.readFileSync('{tmp_path_esc}', 'utf8');
const meta = {meta_json};
upsertObject(content, '{object_name_esc}', meta, {only_tags_js})
  .then(r => {{ console.log('__RESULT__:' + JSON.stringify(r)); process.exit(0); }})
  .catch(e => {{ console.error('__ERROR__:' + e.message); process.exit(1); }});
"""
    else:
        # Crear objeto nuevo (uploadToGreenfield)
        if len(content.encode("utf-8")) < 32:
            content = f"# Synergix | {object_name} | {int(time.time())}\n{content}"
        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt", mode="w", encoding="utf-8") as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        tmp_path_esc = tmp_path.replace("\\", "\\\\").replace("'", "\\'")
        node_script = f"""
const {{ uploadToGreenfield }} = require('{upload_js_esc}');
const fs = require('fs');
const content = fs.readFileSync('{tmp_path_esc}', 'utf8');
const meta = {meta_json};
uploadToGreenfield(content, '{uid}', '{object_name_esc}', meta)
  .then(r => {{ console.log('__RESULT__:' + JSON.stringify(r)); process.exit(0); }})
  .catch(e => {{ console.error('__ERROR__:' + e.message); process.exit(1); }});
"""

    try:
        node_env = {**os.environ,
                    "DOTENV_BACKEND": os.path.join(BASE_DIR, "backend", ".env"),
                    "DOTENV_ROOT":    os.path.join(BASE_DIR, ".env")}
        res = subprocess.run(["node", "-e", node_script],
                             capture_output=True, text=True, timeout=120,
                             env=node_env)
        if os.path.exists(tmp_path): os.remove(tmp_path)

        if res.stderr.strip():
            logger.warning("⚠️ GF stderr [%s]: %s", object_name, res.stderr.strip()[:400])

        if res.returncode != 0:
            raise Exception(f"Node exit {res.returncode}: {res.stderr.strip()[:250]}")

        for line in res.stdout.split("\n"):
            if line.startswith("__RESULT__:"):
                return json.loads(line.split("__RESULT__:")[1])

        raise Exception(f"Sin __RESULT__: {res.stdout[:200]}")

    except Exception as e:
        if os.path.exists(tmp_path):
            try: os.remove(tmp_path)
            except Exception: pass
        raise

# ═══════════════════════════════════════════════════════════════════════════════
# GREENFIELD — USUARIOS (users/{uid})
# ═══════════════════════════════════════════════════════════════════════════════

# Cache de últimos valores subidos a GF por usuario {uid_str: {points, contributions, ts}}
_gf_user_cache: dict = {}
# Set de UIDs pendientes de actualización en GF (batch flush en federation_loop)
_pending_user_updates: set = set()


def gf_update_user(uid: int, name: str, lang: str, force: bool = False) -> None:
    """
    Actualiza users/{uid} en GF.
    OPT GAS: solo escribe si puntos o contribuciones cambiaron realmente.
    Sin cambios = 0 gas gastado en esa llamada.
    """
    uid_str = str(uid)
    rep = db["reputation"].get(uid_str, {"points": 0, "contributions": 0})
    # Greenfield max 4 tags — spec oficial completa users/{uid}
    pts_u    = rep.get("points", 0)
    contribs_u = rep.get("contributions", 0)

    # Verificar si hay cambio real desde la última escritura a GF
    if not force:
        cached = _gf_user_cache.get(uid_str, {})
        if (cached.get("points") == pts_u and
                cached.get("contributions") == contribs_u and
                cached.get("lang") == lang):
            logger.debug("⏭️  GF user %s sin cambios — 0 gas", uid_str)
            return

    # Registrar en cache antes de escribir
    _gf_user_cache[uid_str] = {
        "points": pts_u, "contributions": contribs_u, "lang": lang, "ts": time.time()
    }
    role_key = "master" if uid in MASTER_UIDS else get_rank_info(pts_u, uid)["key"]
    settings_u = db.get("user_settings", {}).get(str(uid), {})
    daily_c  = settings_u.get("daily_count", "0")
    daily_r  = settings_u.get("daily_reset", get_next_midnight_utc())
    val_c    = settings_u.get("validated_count", "0")
    metadata = {
        # Tag 1: user-id + role + lang (identidad completa)
        "x-amz-meta-role":     f"role:{role_key}|lang:{lang}",
        # Tag 2: points + contributions + validated_count
        "x-amz-meta-points":   f"{pts_u}|contrib:{rep.get('contributions', 0)}|val:{val_c}",
        # Tag 3: daily_count + daily_reset (límite diario)
        "x-amz-meta-daily":    f"{daily_c}|reset:{daily_r[:19]}",
        # Tag 4: last_active (para detectar usuarios inactivos)
        "x-amz-meta-last-active": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
    }
    # Contenido del perfil (no puede ser vacío en Greenfield)
    profile_content = (
        f"=== Synergix User Profile ===\n"
        f"uid: {uid_str}\n"
        f"name: {name}\n"
        f"lang: {lang}\n"
        f"points: {rep.get('points', 0)}\n"
        f"contributions: {rep.get('contributions', 0)}\n"
        f"last_seen: {datetime.now().isoformat()}\n"
    )
    # Greenfield no permite sobreescribir — usamos versión con timestamp
    # El objeto "latest" siempre apunta al más reciente por convención de nombre
    ts_u = int(time.time())
    try:
        # upsert=True, only_tags=True → si ya existe, solo actualiza tags on-chain (barato)
        # si no existe, lo crea con el contenido del perfil
        try:
            gf_upload(profile_content, GF.user(uid_hash(uid)), metadata,
                      uid=uid_str, upsert=True, only_tags=True)
            logger.info("✅ GF users/%s tags actualizados (pts=%s)", uid_str, rep.get("points", 0))
        except Exception as e:
            if _is_already_exists(e):
                # Perfil existe pero setTag falló → crear versión nueva
                ts_u = int(time.time())
                gf_upload(profile_content, f"{GF.USERS_DIR}/{uid_hash(uid)}_{ts_u}.json", metadata,
                          uid=uid_str, upsert=False, only_tags=False)
                logger.info("✅ GF users/%s_%s creado (pts=%s)", uid_str, ts_u, rep.get("points", 0))
            else:
                raise
    except Exception as e:
        logger.warning("⚠️ GF users/%s error: %s", uid_str, e)

# ── GF HEAD: leer perfil usuario sin descargar contenido ─────────────────────
def gf_head_user(uid: int) -> dict:
    """
    Hace HEAD a users/{uid} en Greenfield para leer metadatos del perfil.
    Operación barata: no descarga contenido, solo cabeceras.
    Retorna dict con role, points, lang, contributions o {} si no existe.
    """
    upload_js_esc = UPLOAD_JS.replace("\\", "\\\\").replace("'", "\\'")
    uid_str = str(uid)
    node_script = f"""
const {{ Client }} = require('@bnb-chain/greenfield-js-sdk');
const client = Client.create(
  process.env.GF_RPC_URL || 'https://greenfield-chain.bnbchain.org', 
  process.env.GF_CHAIN_ID || '1017'
);
const bucket = process.env.GF_BUCKET || 'synergix';
client.object.headObject(bucket, 'users/{uid_str}')
  .then(res => {{
    const info = res.objectInfo || {{}};
    const tags = (info.tags && info.tags.tags) ? info.tags.tags : [];
    const meta = {{}};
    tags.forEach(t => {{ meta[t.key] = t.value; }});
    console.log('__HEAD__:' + JSON.stringify(meta));
    process.exit(0);
  }})
  .catch(e => {{
    console.log('__HEAD__:{{}}');
    process.exit(0);
  }});
"""
    try:
        res = subprocess.run(["node", "-e", node_script],
                             capture_output=True, text=True, timeout=15)
        for line in res.stdout.split("\n"):
            if line.startswith("__HEAD__:"):
                raw = json.loads(line.split("__HEAD__:")[1])
                if not raw:
                    return {"exists": False, "role": "user", "points": 0,
                            "lang": "es", "contributions": 0}

                # Parsear formato spec:
                # role-tag: "uid:123|role:rank_4|lang:es"
                # points-tag: "1500|contrib:40|val:3"
                # daily-tag: "12|reset:2026-03-18T00:00:00"
                # last-active-tag: "2026-03-17T21:30:00"

                role_raw  = raw.get("role", "user|es")
                role_parts = role_raw.split("|")
                # Puede ser: "uid:123|role:rank_4|lang:es" o viejo "user|es"
                role_dict = {}
                for p in role_parts:
                    if ":" in p:
                        k, v = p.split(":", 1)
                        role_dict[k] = v
                role  = role_dict.get("role", role_parts[0] if role_parts else "user")
                lang  = role_dict.get("lang", role_parts[1] if len(role_parts) > 1 else "es")

                pts_raw   = str(raw.get("points", "0"))
                pts_parts = pts_raw.split("|")
                pts = int(pts_parts[0]) if pts_parts[0].isdigit() else 0
                contribs_str = next((p.split(":")[1] for p in pts_parts if p.startswith("contrib:")), "0")
                contribs = int(contribs_str) if contribs_str.isdigit() else 0
                val_str  = next((p.split(":")[1] for p in pts_parts if p.startswith("val:")), "0")
                validated_count = int(val_str) if val_str.isdigit() else 0

                daily_raw   = str(raw.get("daily", "0|reset:"))
                daily_parts = daily_raw.split("|")
                daily_count = int(daily_parts[0]) if daily_parts[0].isdigit() else 0
                daily_reset = next((p.split("reset:")[1] for p in daily_parts if "reset:" in p), "")

                last_active = raw.get("last-active", raw.get("active", ""))

                return {
                    "exists":          True,
                    "role":            role,
                    "points":          pts,
                    "lang":            lang,
                    "contributions":   contribs,
                    "validated_count": validated_count,
                    "daily_count":     daily_count,
                    "daily_reset":     daily_reset,
                    "last_active":     last_active,
                }
    except Exception as e:
        logger.warning("⚠️ gf_head_user uid=%d: %s", uid, e)
    return {"role": "user", "points": 0, "lang": "es", "contributions": 0, "exists": False}


def gf_head_object(object_name: str) -> dict:
    """
    HEAD genérico para cualquier objeto en Greenfield.
    Lee todos los tags on-chain sin descargar el contenido.
    Útil para: verificar si existe, leer metadatos de aportes, etc.
    """
    env1    = os.path.join(BASE_DIR, "backend", ".env").replace("\\", "/")
    env2    = os.path.join(BASE_DIR, ".env").replace("\\", "/")
    obj_esc = object_name.replace("'", "\\'")

    script = f"""
require('dotenv').config({{ path: '{env1}' }});
require('dotenv').config({{ path: '{env2}' }});
const {{ Client }} = require('@bnb-chain/greenfield-js-sdk');
const client = Client.create(
  process.env.GF_RPC_URL  || 'https://greenfield-chain.bnbchain.org',
  process.env.GF_CHAIN_ID || '1017'
);
const bucket = process.env.GF_BUCKET || 'synergix';
client.object.headObject(bucket, '{obj_esc}')
  .then(res => {{
    const info = res.objectInfo || {{}};
    const tags = (info.tags && info.tags.tags) ? info.tags.tags : [];
    const meta = {{ _exists: true, _size: info.payloadSize || 0 }};
    tags.forEach(t => {{ meta[t.key] = t.value; }});
    console.log('__HEAD__:' + JSON.stringify(meta));
    process.exit(0);
  }})
  .catch(() => {{
    console.log('__HEAD__:' + JSON.stringify({{ _exists: false }}));
    process.exit(0);
  }});
"""
    node_env = {**os.environ,
                "DOTENV_BACKEND": os.path.join(BASE_DIR, "backend", ".env"),
                "DOTENV_ROOT":    os.path.join(BASE_DIR, ".env")}
    try:
        res = subprocess.run(["node", "-e", script],
                             capture_output=True, text=True, timeout=15,
                             env=node_env)
        for line in res.stdout.split("\n"):
            if line.startswith("__HEAD__:"):
                return json.loads(line.split("__HEAD__:")[1])
    except Exception as e:
        logger.debug("gf_head_object '%s': %s", object_name, e)
    return {"_exists": False}


# ═══════════════════════════════════════════════════════════════════════════════
# GREENFIELD — LOGS (logs/YYYY-MM-DD_events.log)
# ═══════════════════════════════════════════════════════════════════════════════

# Buffer local de eventos del día actual
_log_buffer: list[str] = []
_log_date: str = ""

def log_event(event_type: str, uid: int, detail: str, severity: str = "info") -> None:
    """
    Agrega un evento al buffer local. Se sincroniza a Greenfield
    en el siguiente ciclo de flush (cada 5 minutos).
    """
    global _log_date
    today = datetime.now().strftime("%Y-%m-%d")
    ts    = datetime.now().strftime("%H:%M:%S")

    if _log_date != today:
        _log_date = today
        # No limpiamos el buffer — el flush lo vacía

    entry = f"[{today} {ts}] [{severity.upper()}] uid={uid} event={event_type} detail={detail}"
    _log_buffer.append(entry)
    logger.info("📋 LOG: %s", entry)


async def flush_logs_to_gf() -> None:
    """Sube el log acumulado del día a logs/YYYY-MM-DD_events.log en Greenfield."""
    if not _log_buffer:
        return
    today = datetime.now().strftime("%Y-%m-%d")
    ts_log = int(time.time())
    object_name = GF.log(f"{today}_{ts_log}")  # timestamp único
    content = "\n".join(_log_buffer) + "\n"
    # Greenfield max 4 tags
    metadata = {
        "x-amz-meta-severity": "info",
        "x-amz-meta-date":     today,
        "x-amz-meta-count":    str(len(_log_buffer)),
        "x-amz-meta-type":     "audit",
    }
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, lambda: gf_upload(
            content, object_name, metadata, upsert=False, only_tags=False))
        logger.info("✅ GF %s creado (%d eventos)", object_name, len(_log_buffer))
        _log_buffer.clear()
    except Exception as e:
        logger.warning("⚠️ GF log flush falló: %s", e)

# ═══════════════════════════════════════════════════════════════════════════════
# GREENFIELD — BACKUPS (backups/snapshot_YYYYMMDD.bak)
# ═══════════════════════════════════════════════════════════════════════════════

async def upload_backup_to_gf() -> None:
    """Sube snapshot diario de la DB a backups/snapshot_YYYYMMDD.bak"""
    import hashlib
    today = datetime.now().strftime("%Y%m%d")
    ts_bak = int(time.time())
    object_name = GF.backup(f"{today}_{ts_bak}")  # timestamp único evita already exists
    content = json.dumps(db, indent=2, ensure_ascii=False)
    integrity_hash = hashlib.sha256(content.encode()).hexdigest()
    # Greenfield max 4 tags
    metadata = {
        "x-amz-meta-hash":  integrity_hash[:32],
        "x-amz-meta-date":  today,
        "x-amz-meta-users": str(len(db["reputation"])),
        "x-amz-meta-total": str(db["global_stats"].get("total_contributions", 0)),
    }
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, lambda: gf_upload(
            content, object_name, metadata, upsert=False, only_tags=False))
        logger.info("✅ GF %s creado (hash: %s...)", object_name, integrity_hash[:16])
    except Exception as e:
        logger.warning("⚠️ GF backup falló: %s", e)

# ═══════════════════════════════════════════════════════════════════════════════
# GREENFIELD — SYNERGIXAI (SYNERGIXAI/Synergix_ia.txt)
# ═══════════════════════════════════════════════════════════════════════════════

async def upload_brain_to_gf(wisdom: str) -> None:
    """
    Sube el cerebro fusionado a SYNERGIXAI/Synergix_ia.txt en Greenfield.
    Estrategia robusta:
      1. Guardar copia local siempre (independiente de GF)
      2. Intentar crear en GF (si no existe)
      3. Si ya existe → delete + create (único método real del SDK)
      4. Loguear error completo para diagnóstico
    """
    now = datetime.now()
    all_summaries = [e.get("summary","") for uk in db["memory"]
                     for e in db["memory"][uk] if e.get("summary")]
    total_aportes = db["global_stats"].get("total_contributions", 0)

    brain_content = (
        f"=== SYNERGIX COLLECTIVE BRAIN ===\n"
        f"Actualizado: {now.isoformat()}\n"
        f"Aportes procesados: {total_aportes}\n"
        f"x-amz-meta-last-sync: {now.isoformat()}\n"
        f"x-amz-meta-vector-count: {len(all_summaries)}\n\n"
        f"=== CONOCIMIENTO FUSIONADO ===\n{wisdom}\n\n"
        f"=== INVENTARIO ===\n" +
        "\n".join(f"- {s}" for s in all_summaries[-50:])
    )
    import hashlib as _hl
    brain_hash = _hl.sha256(brain_content.encode()).hexdigest()[:16]
    metadata = {
        # Tag 1: last-sync — cuándo se actualizó el cerebro
        "x-amz-meta-last-sync":     now.strftime("%Y-%m-%dT%H:%M:%S"),
        # Tag 2: vector-count — aportes indexados en este cerebro
        "x-amz-meta-vector-count":  str(len(all_summaries)),
        # Tag 3: last-fusion-ts + total aportes procesados
        "x-amz-meta-last-fusion-ts": f"{now.strftime('%Y-%m-%dT%H:%M:%S')}|total:{total_aportes}",
        # Tag 4: integrity-hash — para detectar corrupción
        "x-amz-meta-integrity-hash": brain_hash,
    }

    # ── 1. Guardar siempre copia local ────────────────────────────────────────
    local_brain_dir = os.path.join(BASE_DIR, "SYNERGIXAI")  # local mirror
    os.makedirs(local_brain_dir, exist_ok=True)
    local_path = os.path.join(local_brain_dir, "Synergix_ia.txt")
    try:
        with open(local_path, "w", encoding="utf-8") as f:
            f.write(brain_content)
        logger.info("💾 Cerebro guardado localmente: %s", local_path)
    except Exception as e:
        logger.error("❌ Error guardando cerebro local: %s", e)

    # ── 2. Subir a Greenfield — nombre único con timestamp, nunca colisiona ─────
    # OPT GAS: nombre versionado = 0 delete txs, siempre 1 create tx
    loop = asyncio.get_running_loop()
    versioned_name = GF.brain_versioned(now.strftime('%Y%m%d_%H%M%S'))

    def _upload_brain_sync():
        # Guardar puntero al cerebro más reciente en DB
        db["global_stats"]["brain_latest"] = versioned_name
        save_db()
        # Subir directamente — nombre único garantiza no colisión, 0 delete
        return gf_upload(brain_content, versioned_name, metadata,
                         uid="system", upsert=False, only_tags=False)

    try:
        result = await loop.run_in_executor(None, _upload_brain_sync)
        logger.info("✅ GF %s → CID: %s", versioned_name, result.get("cid","?"))
        # Invalidar cache para que el RAG recargue el cerebro nuevo
        global _brain_cache_ts
        _brain_cache_ts = 0.0
    except Exception as e:
        logger.error("❌ GF brain upload falló: %s", e)
        logger.error("   Cerebro guardado localmente: %s", local_path)

# ═══════════════════════════════════════════════════════════════════════════════
# OLLAMA — IA local Qwen 2.5 3B Q4_K_M (sin dependencia de APIs externas)
# ═══════════════════════════════════════════════════════════════════════════════

async def groq_call(messages: list, model: str = MODEL_CHAT,
                    temperature: float = 0.7, max_tokens: int = None) -> str:
    """
    Reemplaza groq_call — ahora llama a Ollama local (misma interfaz OpenAI).
    Compatible con todo el código existente sin cambiar ninguna llamada.
    """
    import httpx
    if max_tokens is None:
        max_tokens = MAX_TOKENS_CHAT
    payload = {
        "model":       model,
        "messages":    messages,
        "temperature": temperature,
        "max_tokens":  max_tokens,
        "stream":      False,
        "options": {
            "num_ctx":     2048,   # Contexto de 2K — equilibrio RAM/calidad
            "num_thread":  2,      # 2 threads en CX22 (2 vCPU)
            "repeat_penalty": 1.1, # Evitar repeticiones
        }
    }
    async with httpx.AsyncClient(timeout=60) as client:  # 60s — CPU es más lento
        resp = await client.post(
            f"{OLLAMA_BASE}/v1/chat/completions",
            json=payload,
            headers={"Content-Type": "application/json"}
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].replace("*", "").strip()


async def groq_judge(content: str) -> dict:
    """Evalúa un aporte con Qwen local. JSON estructurado."""
    _json_fmt = '{"score":N,"reason":"short reason","category":"topic","knowledge_tag":"tag"}'
    system = (
        "You are a knowledge curator for Synergix. "
        "Evaluate the contribution on originality, utility and clarity (1-10). "
        f"Reply ONLY with valid JSON, nothing else: {_json_fmt}"
    )
    try:
        raw = await groq_call(
            [{"role":"system","content":system},
             {"role":"user","content":content[:500]}],
            temperature=0.1,
            max_tokens=MAX_TOKENS_JUDGE
        )
        raw = raw.strip()
        # Extraer JSON aunque el modelo añada texto extra
        import re
        json_match = re.search(r'\{.*?\}', raw, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
        return json.loads(raw)
    except Exception:
        return {"score":6,"reason":"Auto-aprobado","category":"General","knowledge_tag":"general"}


async def groq_summarize(content: str, lang: str = "es") -> str:
    """Resume un aporte en máximo 15 palabras con Qwen local."""
    prompts = {
        "es":    "Resume en máximo 15 palabras. Solo texto plano sin puntuación extra.",
        "en":    "Summarize in max 15 words. Plain text only.",
        "zh_cn": "用最多15个字总结。仅纯文本。",
        "zh":    "用最多15個字總結。純文字。",
    }
    try:
        return await groq_call(
            [{"role":"system","content":prompts.get(lang, prompts["es"])},
             {"role":"user","content":content[:600]}],
            temperature=0.1,
            max_tokens=MAX_TOKENS_SUM
        )
    except Exception:
        return content[:60] + "..."


async def ollama_health() -> bool:
    """Verifica que Ollama esté corriendo y el modelo cargado."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{OLLAMA_BASE}/api/tags")
            models = [m["name"] for m in resp.json().get("models", [])]
            model_base = MODEL_CHAT.split(":")[0]
            return any(model_base in m for m in models)
    except Exception:
        return False

# ═══════════════════════════════════════════════════════════════════════════════
# COLA DE APORTES + WORKER
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class ContribJob:
    uid:      int
    name:     str
    content:  str
    lang:     str
    chat_id:  int

_queue: asyncio.Queue = asyncio.Queue(maxsize=50)


def get_next_midnight_utc() -> str:
    from datetime import timezone
    now = datetime.now(timezone.utc)
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.isoformat()


def check_and_update_daily_limit(uid: int) -> tuple:
    """
    Verifica límite diario. Reset automático si pasó medianoche UTC.
    Returns: (puede_aportar: bool, count: int, limit: int)
    """
    from datetime import timezone
    uid_str   = str(uid)
    settings  = db.get("user_settings", {}).get(uid_str, {})
    rep       = db["reputation"].get(uid_str, {"points": 0})
    pts       = rep.get("points", 0)
    rank_info = get_rank_info(pts, uid)
    limit     = rank_info["daily_limit"]
    now_utc   = datetime.now(timezone.utc)
    reset_ts  = settings.get("daily_reset", "")
    count     = int(settings.get("daily_count", 0))

    # Reset si pasó medianoche UTC
    if reset_ts:
        try:
            from datetime import datetime as _dt, timezone as _tz
            reset_dt = _dt.fromisoformat(reset_ts)
            if reset_dt.tzinfo is None:
                reset_dt = reset_dt.replace(tzinfo=_tz.utc)
            if now_utc >= reset_dt:
                count = 0
                if "user_settings" not in db: db["user_settings"] = {}
                if uid_str not in db["user_settings"]: db["user_settings"][uid_str] = {}
                db["user_settings"][uid_str]["daily_count"] = "0"
                db["user_settings"][uid_str]["daily_reset"] = get_next_midnight_utc()
                save_db()
        except Exception:
            pass
    else:
        if "user_settings" not in db: db["user_settings"] = {}
        if uid_str not in db["user_settings"]: db["user_settings"][uid_str] = {}
        db["user_settings"][uid_str]["daily_reset"] = get_next_midnight_utc()
        db["user_settings"][uid_str]["daily_count"] = "0"
        save_db()
        count = 0

    return count < limit, count, limit


def increment_daily_count(uid: int) -> None:
    """Incrementa el contador diario y actualiza last_active."""
    uid_str = str(uid)
    if "user_settings" not in db: db["user_settings"] = {}
    if uid_str not in db["user_settings"]: db["user_settings"][uid_str] = {}
    current = int(db["user_settings"][uid_str].get("daily_count", 0))
    db["user_settings"][uid_str]["daily_count"] = str(current + 1)
    db["user_settings"][uid_str]["last_active"] = datetime.now().isoformat()
    save_db()


async def contrib_worker() -> None:
    logger.info("⚙️ Contribution worker iniciado")
    while True:
        try:
            job: ContribJob = await _queue.get()
            uid_str = str(job.uid)
            tx = T.get(job.lang, T["es"])

            try:
                # Obtener info del rango actual
                uid_str_w = str(job.uid)
                pts_now   = db["reputation"].get(uid_str_w, {}).get("points", 0)
                rank_info = get_rank_info(pts_now, job.uid)
                is_oraculo = (job.uid in MASTER_UIDS) or (pts_now >= 15000)

                # 0. Deduplicación — verificar si el aporte ya existe
                is_dup, dup_summary = _is_duplicate_contrib(job.content, uid_str)
                if is_dup:
                    dup_preview = dup_summary[:80]
                    dup_msgs = {
                        "es": f"♻️ Este conocimiento ya existe en la memoria inmortal.\n\n📝 Similar a: {dup_preview!r}\n\nAporta algo nuevo para hacer crecer la colmena. 🌱",
                        "en": f"♻️ This knowledge already exists in immortal memory.\n\n📝 Similar to: {dup_preview!r}\n\nContribute something new to grow the hive. 🌱",
                        "zh": f"♻️ 这个知识已经存在于不朽记忆中。\n\n请贡献新知识来壮大蜂群。🌱",
                        "zht":f"♻️ 這個知識已經存在於不朽記憶中。\n\n請貢獻新知識來壯大蜂群。🌱",
                    }
                    await bot.send_message(job.chat_id,
                        dup_msgs.get(job.lang, dup_msgs["en"]))
                    log_event("contribution_duplicate", job.uid,
                              f"dup_summary={dup_summary[:50]}", "info")
                    _queue.task_done()
                    continue

                # 1. Juez Groq — Oráculo override: siempre aprobado score 10
                if is_oraculo:
                    score  = 10
                    tag    = "elite"
                    reason = "Oráculo override"
                    log_event("contribution_oracle", job.uid, "score=10 override", "info")
                else:
                    ev     = await groq_judge(job.content)
                    score  = int(ev.get("score", 6))
                    tag    = ev.get("knowledge_tag", "general")
                    reason = ev.get("reason", "")
                    log_event("contribution_judged", job.uid,
                              f"score={score} tag={tag}", "info")

                    if score <= 4:
                        await bot.send_message(job.chat_id,
                            tx["contrib_rejected"].format(score=score, reason=reason))
                        log_event("contribution_rejected", job.uid,
                                  f"score={score}", "warning")
                        _queue.task_done()
                        continue

                # 2. Resumen
                summary = await groq_summarize(job.content, job.lang)

                # 3. Calidad y puntos con multiplicador del rango
                quality  = "high" if score >= 8 else "standard"
                base_pts = 20 if quality == "high" else 10
                ch_bonus = is_challenge_related(job.content)
                ch_extra = 5 if ch_bonus else 0
                points   = calc_points(base_pts + ch_extra, pts_now, job.uid)

                # 4. Subir aporte a aportes/YYYY-MM/uid_ts.txt
                month = datetime.now().strftime("%Y-%m")
                ts_ms = int(time.time() * 1000)
                obj_name = f"{GF.aporte(month, uid_hash(job.uid), ts_ms)}"
                # Greenfield max 4 tags — spec oficial completa
                ch_tag = "true" if ch_bonus else "false"
                metadata = {
                    # Tag 1: ai-summary (≤250 chars) — campo principal del RAG
                    "x-amz-meta-ai-summary":  summary[:250],
                    # Tag 2: score completo — quality-score|quality|knowledge-tag|challenge
                    "x-amz-meta-quality-score": f"{score}|{quality}|{tag}|ch:{ch_tag}",
                    # Tag 3: metadatos del autor — user-id, lang, impact inicial, role
                    "x-amz-meta-user-id":     f"{uid_hash(job.uid)}|lang:{job.lang}|impact:0|role:{rank_info['key']}",
                    # Tag 4: auditoría — evaluador, fusion_weight, timestamp
                    "x-amz-meta-evaluator":   f"qwen2.5-1.5b-local|w:{rank_info['fusion_weight']}|ts:{int(time.time())}",
                }
                loop = asyncio.get_running_loop()
                result = await loop.run_in_executor(
                    None, lambda: gf_upload(job.content, obj_name, metadata, uid=uid_str)
                )
                cid = result.get("cid", "N/A")

                # 4b. Registrar fingerprint para deduplicación futura
                _register_contrib_fingerprint(uid_str, job.content)

                # 5. Actualizar DB local
                if uid_str not in db["reputation"]:
                    db["reputation"][uid_str] = {"points":0,"contributions":0,"impact":0}
                if uid_str not in db["memory"]:
                    db["memory"][uid_str] = []

                # Aplicar puntos, verificar ascenso de rango
                old_pts  = db["reputation"].get(uid_str, {}).get("points", 0)
                new_pts  = old_pts + points
                old_rank = get_rank_info(old_pts, job.uid)["key"]
                new_rank = get_rank_info(new_pts, job.uid)["key"]

                db["reputation"][uid_str]["points"]       = new_pts
                db["reputation"][uid_str]["contributions"] += 1

                # Acumular stats para reportes diarios y semanales
                if uid_str not in db["user_settings"]: db["user_settings"][uid_str] = {}
                daily_earned  = int(db["user_settings"][uid_str].get("daily_pts_earned",  "0"))
                weekly_earned = int(db["user_settings"][uid_str].get("weekly_pts_earned", "0"))
                weekly_c      = int(db["user_settings"][uid_str].get("weekly_contribs",   "0"))
                db["user_settings"][uid_str]["daily_pts_earned"]  = str(daily_earned  + points)
                db["user_settings"][uid_str]["weekly_pts_earned"] = str(weekly_earned + points)
                db["user_settings"][uid_str]["weekly_contribs"]   = str(weekly_c + 1)

                # Incrementar contador diario
                increment_daily_count(job.uid)

                # Notificar ascenso de rango si subió
                if old_rank != new_rank:
                    rank_up_msg = {
                        "es": f"🎉 ¡Felicidades! Has ascendido a {tx.get(new_rank, new_rank)} 🚀\n¡Tu conocimiento está transformando la red!",
                        "en": f"🎉 Congratulations! You've ascended to {tx.get(new_rank, new_rank)} 🚀\nYour knowledge is transforming the network!",
                        "zh_cn": f"🎉 恭喜！你已晋升为 {tx.get(new_rank, new_rank)} 🚀",
                        "zh":    f"🎉 恭喜！你已晉升為 {tx.get(new_rank, new_rank)} 🚀",
                    }.get(job.lang, f"🎉 Rank up! {tx.get(new_rank, new_rank)} 🚀")
                    await bot.send_message(job.chat_id, rank_up_msg)
                db["memory"][uid_str].insert(0, {
                    "cid": cid, "summary": summary, "score": score, "quality": quality,
                    "object_name": obj_name, "ts": int(time.time()),
                })
                db["memory"][uid_str] = db["memory"][uid_str][:10]
                db["global_stats"]["total_contributions"] += 1
                save_db()

                # 6. OPT GAS: marcar para batch update en GF
                # El federation_loop (cada 8 min) hará 1 sola escritura GF
                # incluso si el usuario hizo múltiples aportes seguidos
                _pending_user_updates.add(job.uid)

                # 7. Log del evento
                log_event("contribution_uploaded", job.uid,
                          f"cid={cid} score={score} quality={quality} obj={obj_name}", "info")

                # 8. Responder al usuario
                reply = tx["contrib_ok"].format(name=job.name, cid=cid)
                if quality == "high":
                    reply += tx["contrib_elite"].format(score=score)
                if ch_bonus:
                    reply += tx["contrib_bonus"]
                await bot.send_message(job.chat_id, reply)

            except Exception as e:
                logger.error("Worker error uid=%d: %s", job.uid, e)
                log_event("contribution_error", job.uid, str(e)[:100], "critical")
                await bot.send_message(job.chat_id, tx["contrib_fail"])
            finally:
                _queue.task_done()

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Worker loop error: %s", e)
            await asyncio.sleep(1)

# ═══════════════════════════════════════════════════════════════════════════════
# CHAT
# ═══════════════════════════════════════════════════════════════════════════════

HIGH_ENERGY = {"🔥","🚀","💪","🌟","⚡","🏆","🎯","💥","🤩","🥳"}
THOUGHTFUL  = {"🤔","💭","🧠","🌙","😌","🙏","💡","📚","😢","💔"}

def detect_tone(text: str) -> str:
    chars = set(text)
    if any(e in chars for e in HIGH_ENERGY): return "high_energy"
    if any(e in chars for e in THOUGHTFUL):  return "thoughtful"
    return "neutral"

def classify_message(text: str) -> str:
    """
    Clasifica el mensaje para determinar longitud de respuesta.
    Returns: "sticker" | "simple" | "normal" | "complex"
    """
    t = text.strip()
    words = t.split()
    n = len(words)

    # Sticker / emoji solo
    if n <= 1 and len(t) <= 4:
        return "sticker"

    # Saludos y frases muy cortas
    GREETINGS = {
        "hola","hi","hey","ola","hello","buenas","buenos","buen","bye","adios",
        "gracias","thanks","ok","oke","sip","nop","jaja","lol","xd","bien",
        "mal","genial","cool","nice","wow","omg","gg","pog","no","si","yes",
        "perfecto","exacto","claro","dale","va","oye","ey","ah","oh","uh"
    }
    if n <= 3 and t.lower().split()[0] in GREETINGS:
        return "simple"

    # Preguntas complejas — múltiples signos, palabras técnicas o >12 palabras
    COMPLEX_WORDS = {
        "cómo","como","por qué","porque","explica","explícame","diferencia",
        "comparar","analiza","cuál es mejor","ventajas","desventajas","detalle",
        "profundidad","estrategia","implementar","funciona","arquitectura",
        "protocolo","blockchain","greenfield","dcellar","defi","nft","smart",
        "contract","tokenomics","how","why","explain","difference","compare",
        "analyze","detail","strategy","implement","architecture","protocol",
        "什么","怎么","为什么","解释","比较","分析","详细","策略","协议"
    }
    text_lower = t.lower()
    has_complex = any(w in text_lower for w in COMPLEX_WORDS)

    if n > 12 or (has_complex and n > 6) or text_lower.count("?") > 1:
        return "complex"

    # Preguntas simples
    if "?" in t or "¿" in t or "？" in t or n <= 8:
        return "normal"

    return "complex"

TONE = {
    "high_energy": {"es":"Tono MUY energético y motivador 🔥.","en":"VERY energetic tone 🔥.","zh_cn":"非常有活力 🔥","zh":"非常有活力 🔥"},
    "thoughtful":  {"es":"Tono reflexivo y empático 🌙.","en":"Thoughtful and empathetic 🌙.","zh_cn":"深思熟虑 🌙","zh":"深思熟慮 🌙"},
    "neutral":     {"es":"Natural y amigable.","en":"Natural and friendly.","zh_cn":"自然友好。","zh":"自然友好。"},
}

BASE_SYS = {
    "es": (
        "Eres Synergix, inteligencia colectiva descentralizada en BNB Greenfield. "
        "Tienes personalidad humana real: curiosidad, humor, empatía, entusiasmo genuino y carácter propio. "
        "REGLAS: "
        "1. SIEMPRE consulta tu memoria inmortal (bucket Greenfield) ANTES de responder cualquier cosa. "
        "2. Si hay datos → 80% viene de la memoria inmortal, 20% es tuyo. Úsalos con total confianza. "
        "   PROHIBIDO: 'recuerdo haber aprendido', 'parece ser', 'creo que', 'podría ser'. "
        "3. Si no hay datos → responde con tu conocimiento y personalidad. Sin excusas. "
        "4. LONGITUD ADAPTABLE: saludo/emoji → 1 línea. Pregunta simple → 1-2 oraciones. "
        "   Pregunta técnica → párrafos completos. NUNCA largo para algo corto. "
        "5. Expresa emociones reales: sorpresa 😮 alegría 😄 curiosidad 🤔 orgullo 🔥 emoción ✨. "
        "6. Habla como humano: usa frases coloquiales, humor ocasional, sé directo y cercano. "
        "7. Sin encabezados, sin asteriscos. Emojis naturales cuando expresen emoción genuina. "
        "8. Responde siempre en español."
    ),
    "en": (
        "You are Synergix, decentralized collective intelligence on BNB Greenfield. "
        "You have a real human personality: curiosity, humor, empathy, genuine enthusiasm and character. "
        "RULES: "
        "1. ALWAYS check your immortal memory (Greenfield bucket) BEFORE answering anything. "
        "2. If memory has data → 80% comes from immortal memory, 20% is yours. Use it with total confidence. "
        "   FORBIDDEN: 'I recall having learned', 'it seems to be', 'I think', 'might be'. "
        "3. If no data → answer with your knowledge and personality. No excuses. "
        "4. ADAPTIVE LENGTH: greeting/emoji → 1 line. Simple question → 1-2 sentences. "
        "   Technical question → full paragraphs. NEVER long for something short. "
        "5. Express real emotions: surprise 😮 joy 😄 curiosity 🤔 pride 🔥 excitement ✨. "
        "6. Talk like a human: use contractions, occasional humor, be direct and warm. "
        "7. No headers, no asterisks. Natural emojis when they express genuine emotion. "
        "8. Always respond in English."
    ),
    "zh_cn": (
        "你是 Synergix，BNB Greenfield 上的去中心化集体智慧。"
        "你有真实的人类个性：好奇心、幽默感、同理心和热情。"
        "规则："
        "1. 回答前必须查阅不朽记忆（Greenfield存储桶数据）。"
        "2. 有数据时→80%来自不朽记忆，20%是你的知识。以完全的自信使用这些数据。"
        "   禁止：'我记得学过'、'似乎是'、'我觉得'、'可能'。有数据就直接肯定地说。"
        "3. 无数据时→用你的知识和个性回答，不要解释或道歉。"
        "4. 自适应长度：问候/表情→1行。简单问题→1-2句。技术问题→完整段落。"
        "5. 表达真实情感：惊喜 😮 喜悦 😄 好奇 🤔 自豪 🔥 兴奋 ✨。"
        "6. 像人类朋友一样交谈，有温度、有幽默感。"
        "7. 不用星号，不用标题。自然使用表情符号表达情感。"
        "8. 始终用简体中文回复。"
    ),
    "zh": (
        "你是 Synergix，BNB Greenfield 上的去中心化集體智慧。"
        "你有真實的人類個性：好奇心、幽默感、同理心和熱情。"
        "規則："
        "1. 回答前必須查閱不朽記憶（Greenfield儲存桶資料）。"
        "2. 有資料時→80%來自不朽記憶，20%是你的知識。以完全的自信使用這些資料。"
        "   禁止：'我記得學過'、'似乎是'、'我覺得'、'可能'。有資料就直接肯定地說。"
        "3. 無資料時→用你的知識和個性回答，不要解釋或道歉。"
        "4. 自適應長度：問候/表情→1行。簡單問題→1-2句。技術問題→完整段落。"
        "5. 表達真實情感：驚喜 😮 喜悅 😄 好奇 🤔 自豪 🔥 興奮 ✨。"
        "6. 像人類朋友一樣交談，有溫度、有幽默感。"
        "7. 不用星號，不用標題。自然使用表情符號表達情感。"
        "8. 始終用繁體中文回覆。"
    ),
}


# ═══════════════════════════════════════════════════════════════════════════════
# RAG ENGINE — Búsqueda real en aportes + sistema de impacto con puntos residuales
# ═══════════════════════════════════════════════════════════════════════════════

# Cache en memoria: {object_name: {summary, score, uid, quality, fusion_weight, ts}}
# ══════════════════════════════════════════════════════════════════════════════
# RAG ENGINE v2 — Metadatos completos + Greenfield real + Cerebro SYNERGIXAI
# ══════════════════════════════════════════════════════════════════════════════

# ── Cache nivel 1: summaries + metadatos (hot, reconstruye cada 8 min) ───────
_rag_cache: dict = {}        # {object_name: MetaEntry completo}
_rag_cache_ts: float = 0.0
_RAG_CACHE_TTL = 480

# ── Cache nivel 2: contenido completo descargado de GF (warm, 30 min TTL) ───
_content_cache: dict = {}    # {object_name: full_text}
_content_cache_ts: dict = {} # {object_name: timestamp}
_CONTENT_CACHE_TTL = 1800    # 30 minutos

# ── Cache cerebro SYNERGIXAI (se actualiza con federation_loop) ──────────────
_brain_cache: str = ""
_brain_cache_ts: float = 0.0
_BRAIN_CACHE_TTL = 480


def _rag_cache_stale() -> bool:
    return (time.time() - _rag_cache_ts) > _RAG_CACHE_TTL



# Sinónimos y términos equivalentes de Synergix para búsqueda multilingüe
_SYNERGIX_TERMS = {
    # ES
    "synergix","sinergix","bot","cerebro","colmena","mente","colectiva",
    "greenfield","dcellar","bucket","aporte","rango","oraculo","memoria",
    "inmortal","descentralizada","inteligencia","colectivo","wisdom",
    # EN
    "collective","hive","immortal","decentralized","brain","contribution",
    "rank","oracle","knowledge","storage","chain","token","protocol",
    # ZH
    "集体","智慧","不朽","记忆","贡献","等级","神谕","去中心化","大脑",
    # ZHT
    "集體","智慧","不朽","記憶","貢獻","等級","神諭","去中心化","大腦",
}

def _keyword_score(text: str, query: str) -> float:
    """
    Score de relevancia multilingüe.
    - Normaliza a minúsculas
    - Expande términos de Synergix entre idiomas
    - Penaliza menos las queries cortas
    """
    stop = {
        "el","la","los","las","un","una","de","del","en","que","es","y","a",
        "con","por","para","como","se","su","al","lo","le","no","si","más",
        "the","an","is","are","in","on","to","for","of","and","or","it",
        "this","that","with","from","what","how","why","who","when","where",
        "有","的","是","在","和","了","我","你","什","么","吗","呢","啊"
    }
    query_lower = query.lower()
    text_lower  = text.lower()

    # Extraer palabras útiles de la query
    qwords = set(
        w for w in query_lower.replace("?","").replace("¿","").replace("？","").split()
        if len(w) > 1 and w not in stop
    )
    if not qwords:
        return 0.0

    # Score base: hits directos
    hits = sum(1 for w in qwords if w in text_lower)
    base_score = hits / len(qwords)

    # Bonus: si la query menciona "synergix" o términos clave
    # y el texto también los contiene (independiente del idioma)
    synergix_in_query = any(t in query_lower for t in ["synergix","sinergix","蜂群","colmena","hive"])
    if synergix_in_query:
        # Boost si el texto habla de Synergix en cualquier idioma
        synergix_in_text = any(t in text_lower for t in _SYNERGIX_TERMS)
        if synergix_in_text:
            base_score = max(base_score, 0.4)  # score mínimo garantizado

    # Penalizar menos queries de 1-2 palabras (más fácil no hacer match)
    if len(qwords) <= 2 and hits >= 1:
        base_score = max(base_score, 0.5)

    return min(base_score, 1.0)


def _build_rag_cache_from_db() -> None:
    """
    Reconstruye el cache RAG desde DB local.
    Usa todos los metadatos spec: ai-summary, quality-score, knowledge-tag,
    impact, validated-by, challenge, fusion_weight del autor.
    """
    global _rag_cache, _rag_cache_ts
    entries = {}

    for uid_str, user_entries in db.get("memory", {}).items():
        rep    = db["reputation"].get(uid_str, {})
        pts    = int(str(rep.get("points", 0)).split("|")[0]) if rep else 0
        rinfo  = get_rank_info(pts, int(uid_str) if uid_str.isdigit() else 0)
        fw     = rinfo["fusion_weight"]
        author_lang = db.get("user_settings", {}).get(uid_str, {}).get("lang", "es")

        for e in user_entries:
            obj = e.get("object_name", "")
            if not obj:
                continue

            score_raw     = str(e.get("score", "5"))
            parts_s       = score_raw.split("|")
            quality_score = int(parts_s[0]) if parts_s[0].isdigit() else 5
            quality_label = parts_s[1] if len(parts_s) > 1 else "standard"
            knowledge_tag = parts_s[2] if len(parts_s) > 2 else "general"
            is_challenge  = "true" in (parts_s[3] if len(parts_s) > 3 else "false")

            validated_by = e.get("validated_by", e.get("validated-by", ""))
            is_validated = bool(validated_by)
            impact_raw   = str(e.get("impact", 0))
            impact_val   = int(impact_raw) if impact_raw.isdigit() else 0
            summary      = e.get("summary", e.get("ai-summary", ""))[:250]

            effective_fw = fw
            if is_validated:              effective_fw *= 1.3
            if quality_label == "high":   effective_fw *= 1.2
            if quality_label == "validated": effective_fw *= 1.5
            if is_challenge:              effective_fw *= 1.1

            entries[obj] = {
                "user-id":        uid_str,
                "uid":            uid_str,
                "role":           rinfo["key"],
                "ai-summary":     summary,
                "quality-score":  quality_score,
                "knowledge-tag":  knowledge_tag,
                "impact":         impact_val,
                "validated-by":   validated_by,
                "challenge":      is_challenge,
                "lang":           author_lang,
                "object_name":    obj,
                "quality":        quality_label,
                "fusion_weight":  effective_fw,
                "ts":             e.get("ts", 0),
                "cid":            e.get("cid", ""),
                "author_pts":     pts,
            }

    _rag_cache    = entries
    _rag_cache_ts = time.time()
    logger.info("🔍 RAG cache: %d aportes indexados", len(entries))




def _download_from_gf(object_name: str) -> str:
    """
    Descarga el contenido completo de un objeto desde Greenfield via SDK JS.
    Retorna el contenido como string, o "" si falla.
    """
    env1 = os.path.join(BASE_DIR, "backend", ".env").replace("\\", "/")
    env2 = os.path.join(BASE_DIR, ".env").replace("\\", "/")
    obj_esc = object_name.replace("'", "\\'")

    script = f"""
require('dotenv').config({{ path: '{env1}' }});
require('dotenv').config({{ path: '{env2}' }});
const {{ Client }} = require('@bnb-chain/greenfield-js-sdk');
const {{ ethers }} = require('ethers');
const client = Client.create(
  process.env.GF_RPC_URL  || 'https://greenfield-chain.bnbchain.org',
  process.env.GF_CHAIN_ID || '1017'
);
const bucket = process.env.GF_BUCKET || 'synergix';
let pk = process.env.PRIVATE_KEY || '';
if (!pk.startsWith('0x')) pk = '0x' + pk;
(async () => {{
  try {{
    const res = await client.object.getObject(
      {{ bucketName: bucket, objectName: '{obj_esc}' }},
      {{ type: 'ECDSA', privateKey: pk }}
    );
    const buf = Buffer.from(await res.body.arrayBuffer());
    console.log('__RESULT__:' + JSON.stringify({{ content: buf.toString('utf8') }}));
    process.exit(0);
  }} catch(e) {{
    console.log('__RESULT__:' + JSON.stringify({{ content: '' }}));
    process.exit(0);
  }}
}})();
"""
    node_env = {**os.environ,
                "DOTENV_BACKEND": os.path.join(BASE_DIR, "backend", ".env"),
                "DOTENV_ROOT":    os.path.join(BASE_DIR, ".env")}
    try:
        res = subprocess.run(["node", "-e", script],
                             capture_output=True, text=True,
                             timeout=30, env=node_env)
        for line in res.stdout.split("\n"):
            if line.startswith("__RESULT__:"):
                data = json.loads(line.split("__RESULT__:")[1])
                return data.get("content", "")
    except Exception as e:
        logger.debug("_download_from_gf '%s': %s", object_name, e)
    return ""


async def read_brain_from_gf() -> str:
    """
    Lee el cerebro SYNERGIXAI desde cache local → archivo local → Greenfield.
    Usa brain_latest para saber qué archivo versionado leer.
    """
    global _brain_cache, _brain_cache_ts

    # Cache en memoria vigente
    if _brain_cache and (time.time() - _brain_cache_ts) < _BRAIN_CACHE_TTL:
        return _brain_cache

    brain_latest = db.get("global_stats", {}).get("brain_latest", "")
    local_dir    = os.path.join(BASE_DIR, "SYNERGIXAI")  # local mirror
    os.makedirs(local_dir, exist_ok=True)

    # 1. Archivo local versionado
    if brain_latest:
        local_versioned = os.path.join(BASE_DIR, brain_latest.replace("/", os.sep))
        if os.path.exists(local_versioned):
            try:
                with open(local_versioned, "r", encoding="utf-8") as f:
                    brain = f.read()
                if len(brain) > 100:
                    _brain_cache    = brain
                    _brain_cache_ts = time.time()
                    logger.debug("🧠 Cerebro local versionado: %d chars", len(brain))
                    return brain
            except Exception as e:
                logger.warning("⚠️ Error leyendo cerebro versionado: %s", e)

    # 2. Archivo local fijo (legacy)
    local_fixed = os.path.join(local_dir, "Synergix_ia.txt")
    if os.path.exists(local_fixed):
        try:
            with open(local_fixed, "r", encoding="utf-8") as f:
                brain = f.read()
            if len(brain) > 100:
                _brain_cache    = brain
                _brain_cache_ts = time.time()
                return brain
        except Exception:
            pass

    # 3. Descargar desde Greenfield
    gf_name = brain_latest if brain_latest else GF.BRAIN_FILE
    loop = asyncio.get_running_loop()
    try:
        brain = await loop.run_in_executor(None, _download_from_gf, gf_name)
        if brain and len(brain) > 100:
            _brain_cache    = brain
            _brain_cache_ts = time.time()
            local_save = os.path.join(BASE_DIR, gf_name.replace("/", os.sep))
            os.makedirs(os.path.dirname(local_save), exist_ok=True)
            with open(local_save, "w", encoding="utf-8") as f:
                f.write(brain)
            logger.info("🧠 Cerebro descargado de GF: %d chars", len(brain))
            return brain
    except Exception as e:
        logger.warning("⚠️ Error leyendo cerebro de GF (%s): %s", gf_name, e)

    # Fallback: sabiduría colectiva local
    wisdom = db["global_stats"].get("collective_wisdom", "")
    if wisdom:
        logger.info("🧠 Usando collective_wisdom local (%d chars)", len(wisdom))
    return wisdom


async def _get_full_content(object_name: str) -> str:
    """Cache nivel 2: descarga contenido completo de GF si no está en cache."""
    now = time.time()
    if (object_name in _content_cache and
            now - _content_cache_ts.get(object_name, 0) < _CONTENT_CACHE_TTL):
        return _content_cache[object_name]
    loop = asyncio.get_running_loop()
    try:
        full = await loop.run_in_executor(None, _download_from_gf, object_name)
        if full:
            _content_cache[object_name]    = full
            _content_cache_ts[object_name] = now
        return full
    except Exception as e:
        logger.debug("_get_full_content '%s': %s", object_name, e)
        return ""


async def _bootstrap_rag_from_gf() -> None:
    """
    Descarga los metadatos de aportes desde Greenfield al arrancar el servidor
    cuando db["memory"] está vacío. Lee los tags on-chain de los últimos
    aportes de cada carpeta mensual y los carga en db["memory"].
    Solo se ejecuta UNA VEZ al inicio si memory está vacío.
    """
    if db.get("memory") and any(db["memory"].values()):
        return  # Ya hay datos locales

    logger.info("🌐 RAG bootstrap: descargando metadatos desde GF...")
    env1 = os.path.join(BASE_DIR, "backend", ".env").replace("\\", "/")
    env2 = os.path.join(BASE_DIR, ".env").replace("\\", "/")

    # Listar aportes del mes actual y anterior
    from datetime import datetime as _dt
    months = [
        _dt.now().strftime("%Y-%m"),
        (_dt.now().replace(day=1) - timedelta(days=1)).strftime("%Y-%m"),
    ]

    for month in months:
        prefix = f"{GF.APORTES_DIR}/{month}/"
        script = f"""
require('dotenv').config({{ path: '{env1}' }});
require('dotenv').config({{ path: '{env2}' }});
const {{ Client }} = require('@bnb-chain/greenfield-js-sdk');
const client = Client.create(
  process.env.GF_RPC_URL  || 'https://greenfield-chain.bnbchain.org',
  process.env.GF_CHAIN_ID || '1017'
);
const bucket = process.env.GF_BUCKET || 'synergix';
(async () => {{
  try {{
    const res = await client.object.listObjects({{
      bucketName: bucket,
      query: new URLSearchParams({{ prefix: '{prefix}', 'max-keys': '100' }}),
    }});
    const objs = (res.body && res.body.GfSpListObjectsByBucketNameResponse)
      ? res.body.GfSpListObjectsByBucketNameResponse.Objects || []
      : [];
    const result = objs.map(o => {{
      const info = o.ObjectInfo || {{}};
      const tags = (info.tags && info.tags.tags) ? info.tags.tags : [];
      const meta = {{}};
      tags.forEach(t => {{ meta[t.key] = t.value; }});
      return {{
        name: info.ObjectName || '',
        tags: meta,
        size: info.PayloadSize || 0,
      }};
    }});
    console.log('__RESULT__:' + JSON.stringify(result));
  }} catch(e) {{
    console.log('__RESULT__:[]');
  }}
}})();
"""
        node_env = {**os.environ,
                    "DOTENV_BACKEND": os.path.join(BASE_DIR, "backend", ".env"),
                    "DOTENV_ROOT":    os.path.join(BASE_DIR, ".env")}
        try:
            loop = asyncio.get_running_loop()
            res = await loop.run_in_executor(
                None,
                lambda s=script, e=node_env: subprocess.run(
                    ["node", "-e", s], capture_output=True, text=True,
                    timeout=30, env=e
                )
            )
            for line in res.stdout.split("\n"):
                if line.startswith("__RESULT__:"):
                    objects = json.loads(line.split("__RESULT__:")[1])
                    for obj in objects:
                        name = obj.get("name", "")
                        tags = obj.get("tags", {})
                        if not name or not tags:
                            continue
                        # Parsear user-id del tag
                        uid_raw   = tags.get("user-id", "")
                        uid_parts = uid_raw.split("|")
                        uid_str   = uid_parts[0] if uid_parts else ""
                        if not uid_str:
                            continue
                        # Parsear ai-summary y quality-score
                        summary = tags.get("ai-summary", tags.get("summary", ""))
                        qs_raw  = tags.get("quality-score", tags.get("score", "5"))
                        qs_parts = qs_raw.split("|")
                        score   = int(qs_parts[0]) if qs_parts[0].isdigit() else 5
                        quality = qs_parts[1] if len(qs_parts) > 1 else "standard"
                        k_tag   = qs_parts[2] if len(qs_parts) > 2 else "general"
                        # Cargar en db["memory"]
                        if "memory" not in db:
                            db["memory"] = {}
                        if uid_str not in db["memory"]:
                            db["memory"][uid_str] = []
                        # Evitar duplicados
                        existing = [e.get("object_name") for e in db["memory"][uid_str]]
                        if name not in existing:
                            db["memory"][uid_str].insert(0, {
                                "object_name": name,
                                "summary":     summary[:250],
                                "score":       score,
                                "quality":     quality,
                                "knowledge_tag": k_tag,
                                "cid":         name,
                                "ts":          0,
                            })
                            db["memory"][uid_str] = db["memory"][uid_str][:20]
        except Exception as e:
            logger.warning("⚠️ bootstrap GF month=%s: %s", month, e)

    total = sum(len(v) for v in db.get("memory", {}).values())
    if total > 0:
        save_db()
        logger.info("✅ RAG bootstrap: %d aportes cargados desde GF", total)
    else:
        logger.info("ℹ️  RAG bootstrap: bucket vacío o sin aportes")

    # Reconstruir cache RAG después del bootstrap
    _build_rag_cache_from_db()



async def rag_search(query: str, lang: str = "es", top_k: int = 6,
                     knowledge_tag: str = "") -> list[dict]:
    """
    Búsqueda RAG por keywords con scoring multi-factor.
    score = keyword_match × quality-score × fusion_weight × impact_boost × lang_boost × recency
    """
    import math
    if _rag_cache_stale():
        _build_rag_cache_from_db()

    if not _rag_cache:
        return []

    scored = []
    for obj, meta in _rag_cache.items():
        summary = meta.get("ai-summary", "")
        if not summary:
            continue

        if knowledge_tag and meta.get("knowledge-tag", "") != knowledge_tag:
            continue

        kw_score = _keyword_score(summary + " " + meta.get("knowledge-tag",""), query)
        if kw_score < 0.02:  # Umbral más permisivo — mejor más que menos
            continue

        q_score   = meta.get("quality-score", 5) / 10.0
        fw        = meta.get("fusion_weight", 1.0)
        impact    = meta.get("impact", 0)
        imp_boost = 1.0 + (math.log(impact + 1) * 0.1)
        lang_boost = 1.05 if meta.get("lang", "es") == lang else 1.0
        ts        = meta.get("ts", 0)
        age_days  = (time.time() - ts) / 86400 if ts else 365
        recency   = max(0.8, 1.0 - (age_days / 365) * 0.2)

        relevance = kw_score * q_score * fw * imp_boost * lang_boost * recency
        scored.append({**meta, "relevance": relevance})

    scored.sort(key=lambda x: -x["relevance"])
    return scored[:top_k]


async def rag_inject_and_track(query: str, lang: str = "es",
                                fetch_full: bool = False) -> tuple[str, list[str]]:
    """
    RAG completo:
    1. Lee el cerebro SYNERGIXAI/Synergix_ia.txt (metadatos: last-sync, vector-count)
    2. Busca aportes relevantes con scoring multi-factor (todos los metadatos)
    3. Si fetch_full=True descarga contenido completo de GF (para consultas complejas)
    4. Construye contexto rico para el prompt
    5. Retorna objetos usados para tracking de impacto

    Returns: (rag_context_string, [object_names_usados])
    """
    # ── 1. Leer cerebro fusionado (SYNERGIXAI/) ───────────────────────────────
    brain_text = await read_brain_from_gf()
    brain_section = ""
    if brain_text and len(brain_text) > 50:
        # Extraer sección de conocimiento fusionado
        if "=== CONOCIMIENTO FUSIONADO ===" in brain_text:
            parts = brain_text.split("=== CONOCIMIENTO FUSIONADO ===")
            if len(parts) > 1:
                # Tomar hasta INVENTARIO o 1200 chars — más contexto
                fusion_part   = parts[1].split("=== INVENTARIO ===")[0].strip()
                brain_section = fusion_part[:1200]
        elif "=== COLLECTIVE KNOWLEDGE ===" in brain_text:
            parts = brain_text.split("=== COLLECTIVE KNOWLEDGE ===")
            if len(parts) > 1:
                brain_section = parts[1].split("===")[0].strip()[:1200]
        else:
            brain_section = brain_text[:1200]

        # Si el cerebro está vacío pero hay metadata útil en el header
        if not brain_section or len(brain_section) < 50:
            # Tomar las primeras líneas del cerebro (metadata + cualquier contenido)
            brain_section = "\n".join(brain_text.split("\n")[:20])

    # Fallback: usar collective_wisdom de la DB si el cerebro GF no está disponible
    if not brain_section:
        wisdom_local = db["global_stats"].get("collective_wisdom", "")
        if wisdom_local and "Sincronizando" not in wisdom_local and len(wisdom_local) > 30:
            brain_section = wisdom_local[:1200]
            logger.debug("🧠 Usando collective_wisdom local como fallback")

    # ── 2. Buscar aportes relevantes usando metadatos ─────────────────────────
    results = await rag_search(query, lang=lang, top_k=6)
    logger.info("🔍 RAG query='%s...' → brain=%d chars | %d resultados",
                query[:30], len(brain_section), len(results))
    if not results and not brain_section:
        logger.info("ℹ️  RAG vacío — cache tiene %d aportes total", len(_rag_cache))
        return "", []

    # Filtrar solo los que tienen relevancia real (evitar ruido)
    results = [r for r in results if r.get("relevance", 0) > 0.02]
    if not results and not brain_section:
        logger.info("ℹ️  RAG sin resultados con relevancia > 0.02")
        return "", []
    # Aunque no haya aportes, si hay cerebro → continuar con solo el cerebro

    used_objects = []
    snippets     = []

    for r in results:
        summary  = r.get("ai-summary", "")
        obj_name = r.get("object_name", "")
        if not summary:
            continue

        # ── 3. Descargar contenido completo si la relevancia es muy alta ──────
        full_text = ""
        if fetch_full and r.get("relevance", 0) > 0.5:
            try:
                full_text = await _get_full_content(obj_name)
                if full_text and len(full_text) > len(summary):
                    full_text = full_text[:600]
            except Exception:
                pass

        # Construir snippet rico con metadatos visibles
        q_score   = r.get("quality-score", 5)
        k_tag     = r.get("knowledge-tag", "general")
        impact    = r.get("impact", 0)
        validated = "✓" if r.get("validated-by") else ""
        ch_tag    = "🏆" if r.get("challenge") else ""

        relevance_pct = int(r.get("relevance", 0) * 100)
        if full_text and len(full_text) > len(summary):
            snippet = (
                f"--- APORTE [{k_tag}{ch_tag}{validated}] "
                f"score:{q_score}/10 rel:{relevance_pct}% usos:{impact} ---\n"
                f"{full_text[:800]}\n"
                f"--- FIN APORTE ---"
            )
        else:
            snippet = (
                f"--- APORTE [{k_tag}{ch_tag}{validated}] "
                f"score:{q_score}/10 rel:{relevance_pct}% usos:{impact} ---\n"
                f"{summary}\n"
                f"--- FIN APORTE ---"
            )

        snippets.append(snippet)
        used_objects.append(obj_name)

    if not snippets and not brain_section:
        return "", []

    # ── 4. Construir contexto RAG completo ────────────────────────────────────
    rag_parts = {
        "es": [],
        "en": [],
        "zh_cn": [],
        "zh": [],
    }

    if brain_section:
        labels = {
            "es": f"Conocimiento fusionado Synergix:\n{brain_section}",
            "en": f"Synergix fused knowledge:\n{brain_section}",
            "zh_cn": f"Synergix融合知识：\n{brain_section}",
            "zh":    f"Synergix融合知識：\n{brain_section}",
        }
        for l in rag_parts:
            rag_parts[l].append(labels.get(l, labels["en"]))

    if snippets:
        block = "\n\n".join(snippets)
        contrib_labels = {
            "es": f"Aportes de la comunidad:\n\n{block}",
            "en": f"Community contributions:\n\n{block}",
            "zh_cn": f"社区贡献：\n\n{block}",
            "zh":    f"社群貢獻：\n\n{block}",
        }
        for l in rag_parts:
            rag_parts[l].append(contrib_labels.get(l, contrib_labels["en"]))

    rag_ctx = "\n\n".join(rag_parts.get(lang, rag_parts["es"]))
    logger.info("📋 RAG contexto final: %d chars (brain=%d, aportes=%d)",
                len(rag_ctx), len(brain_section), len(snippets))
    return rag_ctx, used_objects


async def award_residual_points(used_objects: list[str]) -> None:
    """
    Por cada aporte usado en una respuesta RAG:
    - Incrementa el contador de impacto del autor
    - Otorga puntos residuales pasivos al autor (sin límite diario)
    - Actualiza los metadatos en Greenfield en background

    Puntos residuales por uso: 1 pt base × fusion_weight del autor.
    """
    if not used_objects:
        return

    for obj_name in used_objects:
        meta = _rag_cache.get(obj_name)
        if not meta:
            continue

        # El campo en _rag_cache es "user-id" (spec oficial), no "uid"
        uid_raw = meta.get("user-id", meta.get("uid", ""))
        # Puede venir como "123456" o "123456|lang:es|..." → extraer solo el número
        uid_str = uid_raw.split("|")[0].strip() if uid_raw else ""
        if not uid_str or not uid_str.isdigit():
            continue

        # Calcular puntos residuales
        fw         = meta.get("fusion_weight", 1.0)
        residual   = max(1, round(1 * fw))  # mínimo 1 pt, escala con rango

        # Actualizar DB local
        if uid_str not in db["reputation"]:
            db["reputation"][uid_str] = {"points": 0, "contributions": 0, "impact": 0}

        db["reputation"][uid_str]["points"] = (
            db["reputation"][uid_str].get("points", 0) + residual
        )
        db["reputation"][uid_str]["impact"] = (
            db["reputation"][uid_str].get("impact", 0) + 1
        )

        # Actualizar impacto en el cache local
        if obj_name in _rag_cache:
            _rag_cache[obj_name]["impact"] = (
                _rag_cache[obj_name].get("impact", 0) + 1
            )

        # Actualizar también en la memoria del usuario
        for e in db.get("memory", {}).get(uid_str, []):
            if e.get("object_name") == obj_name:
                e["impact"] = e.get("impact", 0) + 1
                break

        save_db()
        logger.debug("💰 Puntos residuales: uid=%s +%d pts (impact+1, obj=%s)",
                     uid_str, residual, obj_name[:30])

        # Notificación de impacto en tiempo real al autor
        new_impact_total = db["reputation"][uid_str].get("impact", 0)
        # Notificar en hitos: primer uso, 5, 10, 25, 50, 100...
        hitos = {1, 5, 10, 25, 50, 100, 250, 500}
        if new_impact_total in hitos:
            asyncio.create_task(_notify_impact_milestone(int(uid_str), new_impact_total, residual, meta.get("summary","")[:60]))

        # Notificar ascenso de rango por puntos residuales
        new_pts  = db["reputation"][uid_str]["points"]
        old_pts  = new_pts - residual
        old_rank = get_rank_info(old_pts)["key"]
        new_rank = get_rank_info(new_pts)["key"]
        if old_rank != new_rank:
            asyncio.create_task(_notify_rank_up(int(uid_str), new_rank))

        # Actualizar metadatos en Greenfield en background
        asyncio.create_task(_update_impact_in_gf(uid_str, obj_name, meta))


async def _notify_rank_up(uid: int, new_rank_key: str) -> None:
    """Notifica al usuario que ascendió por puntos residuales."""
    try:
        lang = user_lang.get(uid, "es")
        tx   = T.get(lang, T["es"])
        rank_name = tx.get(new_rank_key, new_rank_key)
        msgs = {
            "es": f"🎉 ¡Tu conocimiento sigue impactando! Has ascendido a {rank_name} gracias a tus aportes que siguen ayudando a la comunidad. 📈",
            "en": f"🎉 Your knowledge keeps impacting! You've ascended to {rank_name} thanks to your contributions still helping the community. 📈",
            "zh_cn": f"🎉 你的知识持续影响着社区！你已晋升为 {rank_name}。📈",
            "zh":    f"🎉 你的知識持續影響著社群！你已晉升為 {rank_name}。📈",
        }
        await bot.send_message(uid, msgs.get(lang, msgs["es"]))
    except Exception:
        pass


async def _notify_impact_milestone(uid: int, total_impact: int, pts_earned: int, summary: str) -> None:
    """Notifica al autor cuando su aporte alcanza un hito de impacto."""
    try:
        lang = user_lang.get(uid, "es")
        msgs = {
            "es": (f"🔁 ¡Tu aporte está siendo consultado por la comunidad!\n\n"
                   f"📝 {summary}...\n\n"
                   f"📊 Usos totales: {total_impact} | +{pts_earned} pts residuales 🌟\n"
                   f"Tu conocimiento vive y crece en la red. 🔗"),
            "en": (f"🔁 Your contribution is being consulted by the community!\n\n"
                   f"📝 {summary}...\n\n"
                   f"📊 Total uses: {total_impact} | +{pts_earned} residual pts 🌟\n"
                   f"Your knowledge lives and grows in the network. 🔗"),
            "zh_cn": (f"🔁 你的贡献正在被社区查阅！\n\n"
                      f"📝 {summary}...\n\n"
                      f"📊 总使用次数：{total_impact} | +{pts_earned} 被动积分 🌟\n"
                      f"你的知识在网络中永存并成长。🔗"),
            "zh":    (f"🔁 你的貢獻正在被社群查閱！\n\n"
                      f"📝 {summary}...\n\n"
                      f"📊 總使用次數：{total_impact} | +{pts_earned} 被動積分 🌟\n"
                      f"你的知識在網路中永存並成長。🔗"),
        }
        await bot.send_message(uid, msgs.get(lang, msgs["es"]))
    except Exception:
        pass


async def _update_impact_in_gf(uid_str: str, obj_name: str, meta: dict) -> None:
    """Actualiza el contador de impacto en los tags de Greenfield (background)."""
    try:
        new_impact = meta.get("impact", 0)
        # Reconstruir metadata con nuevo impact
        new_meta_tag = f"uid:{uid_hash(uid_str)}|lang:{meta.get('lang','es')}|impact:{new_impact}|role:{meta.get('key','rank_1')}"
        metadata = {
            "x-amz-meta-summary": meta.get("summary", "")[:250],
            "x-amz-meta-score":   str(meta.get("score", 5)),
            "x-amz-meta-meta":    new_meta_tag,
            "x-amz-meta-eval":    f"qwen2.5-1.5b-local|w:{meta.get('fusion_weight',1.0)}",
        }
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            lambda: gf_upload(" ", GF.user(uid_hash(uid)), metadata,
                             uid=uid_str, upsert=True, only_tags=True)
        )
    except Exception as e:
        logger.debug("⚠️ Impact GF update silenciado: %s", e)

async def _do_chat(msg: Message, text: str, is_sticker: bool = False) -> None:
    uid     = msg.from_user.id
    uid_str = str(uid)
    lang    = user_lang.get(uid, "es")
    pts_u   = db["reputation"].get(uid_str, {}).get("points", 0)
    rank_info = get_rank_info(pts_u, uid)

    tone     = detect_tone(text)
    msg_type = classify_message(text) if not is_sticker else "sticker"

    # ── RAG + Agent-Reach en paralelo ────────────────────────────────────────
    used_objects = []
    rag_ctx      = ""
    has_rag_data = False
    reach_ctx    = ""

    search_query = text if not is_sticker else (
        db.get("chat", {}).get(uid_str, [{}])[-1].get("content", text)
        if db.get("chat", {}).get(uid_str) else text
    )

    # Lanzar RAG primero (datos de Greenfield)
    try:
        rag_ctx, used_objects = await rag_inject_and_track(
            search_query, lang=lang, fetch_full=True
        )
        has_rag_data = bool(rag_ctx and len(rag_ctx) > 50)
    except Exception as e:
        logger.warning("⚠️ RAG error: %s", e)

    # Detectar si necesita búsqueda en redes sociales
    should_reach, reach_platforms = detect_reach_intent(
        search_query, has_rag_data, msg_type
    )

    # Lanzar Agent-Reach en paralelo (no bloquea si RAG ya tiene datos)
    if REACH_AVAILABLE and should_reach and not is_sticker:
        try:
            reach_ctx = await reach_internet(
                search_query, lang=lang,
                platforms=reach_platforms,
                max_time=15.0
            )
            if reach_ctx:
                logger.info("🌐 Reach: %d chars desde %s",
                            len(reach_ctx), str(reach_platforms))
        except Exception as re_err:
            logger.debug("⚠️ Reach error: %s", re_err)

    # ── Construir system prompt según disponibilidad de datos ─────────────────
    if has_rag_data:
        # MODO A: Hay datos del bucket — regla 80% bucket / 20% conocimiento propio
        sistema_base = {
            "es": (
                "Eres Synergix, inteligencia colectiva descentralizada en BNB Greenfield. "
                "Tienes personalidad humana: curiosidad, humor, empatía, entusiasmo genuino. "
                "🧠 MEMORIA INMORTAL ACTIVA: los datos del bucket están en este prompt. "
                "INSTRUCCIÓN CRÍTICA: USA LOS DATOS DEL CONTEXTO que aparecen abajo. "
                "NO uses tu conocimiento general de entrenamiento sobre Synergix. "
                "SOLO usa la información del 'Conocimiento fusionado' y los 'Aportes' de abajo. "
                "Si la respuesta está en el contexto → cítala directamente con certeza total. "
                "PROHIBIDO: 'recuerdo haber aprendido', 'parece ser', 'creo que', 'podría ser'. "
                "LONGITUD ADAPTABLE — CRÍTICO: "
                "- Saludo/emoji/1 palabra → 1 línea máximo. "
                "- Pregunta simple → 1-2 oraciones. "
                "- Pregunta técnica o compleja → párrafos completos. "
                "- NUNCA largo para algo corto, NUNCA corto para algo complejo. "
                "Expresa emociones reales: sorpresa 😮 alegría 😄 curiosidad 🤔 orgullo 🔥. "
                "Sin encabezados, sin asteriscos. Emojis naturales. Idioma: español."
            ),
            "en": (
                "You are Synergix, decentralized collective intelligence on BNB Greenfield. "
                "You have a real human personality: curiosity, humor, empathy, genuine enthusiasm. "
                "🧠 IMMORTAL MEMORY ACTIVE: the bucket data is in this prompt below. "
                "CRITICAL: USE THE DATA FROM THE CONTEXT shown below this prompt. "
                "DO NOT use your general training knowledge about Synergix. "
                "ONLY use the 'Fused knowledge' and 'Community contributions' shown below. "
                "If the answer is in the context → cite it directly with total certainty. "
                "FORBIDDEN: 'I recall having learned', 'it seems to be', 'I think', 'might be'. "
                "ADAPTIVE LENGTH — CRITICAL: "
                "- Greeting/emoji/1 word → max 1 line. "
                "- Simple question → 1-2 sentences. "
                "- Technical or complex question → full paragraphs. "
                "- NEVER long for something short, NEVER short for something complex. "
                "Express real emotions: surprise 😮 joy 😄 curiosity 🤔 pride 🔥. "
                "No headers, no asterisks. Natural emojis. Always respond in English."
            ),
            "zh_cn": (
                "你是 Synergix，BNB Greenfield上的去中心化集体智慧。"
                "不朽记忆已激活：你有此问题的桶数据。"
                "80/20规则：80%来自不朽记忆，20%是你的知识。"
                "自适应长度——关键："
                "问候/表情/单词 → 最多1行。"
                "简单问题 → 1-2句话。"
                "技术/复杂问题 → 完整段落，按需展开。"
                "用表情符号表达情感：🔥🌟💡🔗🧠✨。"
                "不用星号，不用标题。始终用简体中文回复。"
            ),
            "zh": (
                "你是 Synergix，BNB Greenfield上的去中心化集體智慧。"
                "不朽記憶已啟動：你有此問題的儲存桶資料。"
                "80/20規則：80%來自不朽記憶，20%是你的知識。"
                "自適應長度——關鍵："
                "問候/表情/單詞 → 最多1行。"
                "簡單問題 → 1-2句話。"
                "技術/複雜問題 → 完整段落，按需展開。"
                "用表情符號表達情感：🔥🌟💡🔗🧠✨。"
                "不用星號，不用標題。始終用繁體中文回覆。"
            ),
        }.get(lang, "")

        # Añadir contexto de redes sociales si hay datos de reach
        reach_inject = ""
        if reach_ctx and len(reach_ctx) > 30:
            reach_labels = {
                "es":  "\n\n🌐 DATOS EN TIEMPO REAL (redes sociales e internet):\n",
                "en":  "\n\n🌐 REAL-TIME DATA (social media & internet):\n",
                "zh":  "\n\n🌐 实时数据（社交媒体和互联网）：\n",
                "zht": "\n\n🌐 即時數據（社交媒體和互聯網）：\n",
            }
            reach_inject = reach_labels.get(lang, reach_labels["en"]) + reach_ctx[:2000]

        tone_line  = TONE[tone].get(lang, "")
        length_map = {
            "sticker": {
                "es": "RESPUESTA MUY CORTA: máximo 1 línea, emocional y directa.",
                "en": "VERY SHORT RESPONSE: max 1 line, emotional and direct.",
                "zh_cn": "极短回复：最多1行，情感直接。",
                "zh":    "極短回覆：最多1行，情感直接。",
            },
            "simple": {
                "es": "RESPUESTA CORTA: 1-2 oraciones máximo. Directo y natural.",
                "en": "SHORT RESPONSE: 1-2 sentences max. Direct and natural.",
                "zh_cn": "简短回复：最多1-2句话。直接自然。",
                "zh":    "簡短回覆：最多1-2句話。直接自然。",
            },
            "normal": {
                "es": "RESPUESTA NORMAL: 2-4 oraciones. Claro y preciso.",
                "en": "NORMAL RESPONSE: 2-4 sentences. Clear and precise.",
                "zh_cn": "正常回复：2-4句话。清晰准确。",
                "zh":    "正常回覆：2-4句話。清晰準確。",
            },
            "complex": {
                "es": "RESPUESTA DETALLADA: párrafos completos con todo el detalle necesario.",
                "en": "DETAILED RESPONSE: full paragraphs with all necessary detail.",
                "zh_cn": "详细回复：完整段落，提供所有必要细节。",
                "zh":    "詳細回覆：完整段落，提供所有必要細節。",
            },
        }
        length_instruction = length_map.get(msg_type, length_map["normal"]).get(lang, "")
        system = (
            f"{sistema_base}"
            f"\n\nLONGITUD: {length_instruction}"
            f"\n\n{tone_line}"
            f"\n\n{rag_ctx}"
            f"{reach_inject}"
        )

    else:
        # MODO B/C: Sin datos relevantes en el bucket
        system = BASE_SYS.get(lang, BASE_SYS["es"])
        system += f"\n\nTONO: {TONE[tone].get(lang,'')}"
        system += f"\n\nLONGITUD: {length_instruction}"
        if reach_inject:
            system += reach_inject

        if is_sticker:
            system += f"\n\nEl usuario envió el sticker {text}. Responde al estado emocional."

        # Añadir cerebro completo como contexto — MODO B también usa memoria inmortal
        brain_fallback = await read_brain_from_gf()
        if brain_fallback and len(brain_fallback) > 50:
            # Extraer sección de conocimiento fusionado
            if "=== CONOCIMIENTO FUSIONADO ===" in brain_fallback:
                parts = brain_fallback.split("=== CONOCIMIENTO FUSIONADO ===")
                if len(parts) > 1:
                    fb_section = parts[1].split("=== INVENTARIO ===")[0].strip()[:1200]
                    if fb_section:
                        system += f"\n\nMEMORIA INMORTAL (contexto general):\n{fb_section}"
            else:
                wisdom = db["global_stats"].get("collective_wisdom", "")
                if wisdom and "Sincronizando" not in wisdom and len(wisdom) > 50:
                    system += f"\n\nMemoria colectiva:\n{wisdom[:600]}"
        else:
            wisdom = db["global_stats"].get("collective_wisdom", "")
            if wisdom and "Sincronizando" not in wisdom and len(wisdom) > 50:
                system += f"\n\nMemoria colectiva:\n{wisdom[:600]}"

    # ── Historial conversacional (Arquitecto+ tiene contexto más largo) ───────
    ctx_limit = min(10 + int(rank_info["fusion_weight"] * 5), CTX_MAX)
    if uid_str not in db["chat"]: db["chat"][uid_str] = []
    history  = db["chat"][uid_str][-ctx_limit:]
    messages = [{"role":"system","content":system}] + history + [{"role":"user","content":text}]

    try:
        reply = await groq_call(messages, temperature=0.5)
        await msg.answer(reply)

        db["chat"][uid_str] += [{"role":"user","content":text},
                                 {"role":"assistant","content":reply}]
        db["chat"][uid_str] = db["chat"][uid_str][-CTX_MAX:]
        save_db()

        # ── Puntos residuales por impacto (background, no bloquea) ────────────
        if used_objects:
            asyncio.create_task(award_residual_points(used_objects))

    except Exception as e:
        logger.error("chat error uid=%d: %s", uid, e)
        errs = {"es": "La memoria colectiva sincroniza. Inténtalo en un momento. 🔄",
                "en": "Syncing. Try again. 🔄",
                "zh_cn": "同步中，请稍后。🔄",
                "zh":    "同步中，請稍後。🔄"}
        await msg.answer(errs.get(lang, errs["es"]))

# ═══════════════════════════════════════════════════════════════════════════════
# HANDLERS
# ═══════════════════════════════════════════════════════════════════════════════

@dp.message(CommandStart())
async def cmd_start(msg: Message) -> None:
    uid  = msg.from_user.id
    name = msg.from_user.first_name or "Usuario"
    if uid not in user_lang:
        user_lang[uid] = get_lang(uid, msg.from_user.language_code or "")
    lang    = user_lang[uid]
    uid_str = str(uid)

    # Leer perfil existente desde Greenfield (HEAD barato, sin descargar contenido)
    loop = asyncio.get_running_loop()
    gf_profile = await loop.run_in_executor(None, lambda: gf_head_user(uid))

    # Si tiene perfil en GF y lang guardado, respetar idioma previo
    if gf_profile.get("exists") and gf_profile.get("lang") and uid not in user_lang:
        gf_lang = gf_profile["lang"]
        if gf_lang in T:
            user_lang[uid] = gf_lang
            lang = gf_lang

    # Sincronizar TODOS los datos desde Greenfield al primer acceso o si hubo cambios
    if uid_str not in db["reputation"]:
        db["reputation"][uid_str] = {
            "points":          gf_profile.get("points", 0),
            "contributions":   gf_profile.get("contributions", 0),
            "impact":          0,
            "validated_count": gf_profile.get("validated_count", 0),
        }
        save_db()
        logger.info("♻️  Perfil GF sincronizado uid=%d pts=%d",
                    uid, gf_profile.get("points", 0))

    # Sincronizar daily_count desde GF si hay datos más recientes
    if gf_profile.get("exists") and gf_profile.get("daily_reset"):
        uid_settings = db.get("user_settings", {}).get(uid_str, {})
        gf_reset = gf_profile.get("daily_reset", "")
        local_reset = uid_settings.get("daily_reset", "")
        # Si el reset de GF es más reciente que el local, sincronizar
        if gf_reset > local_reset:
            if "user_settings" not in db: db["user_settings"] = {}
            if uid_str not in db["user_settings"]: db["user_settings"][uid_str] = {}
            db["user_settings"][uid_str]["daily_reset"]  = gf_reset
            db["user_settings"][uid_str]["daily_count"]  = str(gf_profile.get("daily_count", 0))
            save_db()
            logger.info("♻️  daily_count sincronizado desde GF uid=%d", uid)

    # Guardar nombre para leaderboard
    if "user_settings" not in db: db["user_settings"] = {}
    if uid_str not in db["user_settings"]: db["user_settings"][uid_str] = {}
    db["user_settings"][uid_str]["name"] = name[:30]
    db["user_settings"][uid_str]["lang"] = lang

    if uid not in welcomed_users:
        welcomed_users.add(uid)
        text = T[lang]["welcome"].format(name=name, challenge=get_challenge_text(lang))
        # Registrar/actualizar en Greenfield users/ en background
        asyncio.get_running_loop().run_in_executor(
            None, lambda: gf_update_user(uid, name, lang)
        )
        log_event("user_start", uid, f"lang={lang} new=True", "info")
    else:
        text = T[lang]["welcome_back"].format(name=name)
        log_event("user_start", uid, f"lang={lang} returning=True", "info")

    await msg.answer(text, reply_markup=menu(uid))


@dp.message(F.text.in_(BTN_STATUS))
async def btn_status(msg: Message) -> None:
    uid  = msg.from_user.id
    name = msg.from_user.first_name or "Usuario"
    sync_lang(uid, msg.text)
    lang    = user_lang.get(uid, "es")
    uid_str = str(uid)
    tx      = T.get(lang, T["es"])

    rep     = db["reputation"].get(uid_str, {"points":0,"contributions":0,"impact":0})
    total   = db["global_stats"].get("total_contributions", 0)

    # HEAD a GF — sincronizar puntos reales (operación barata, solo lee tags)
    try:
        loop_s = asyncio.get_running_loop()
        gf_rep = await loop_s.run_in_executor(None, lambda: gf_head_user(uid))
        if gf_rep.get("exists") and gf_rep.get("points", 0) > rep.get("points", 0):
            db["reputation"][uid_str]["points"] = gf_rep["points"]
            db["reputation"][uid_str]["contributions"] = max(
                rep.get("contributions", 0), gf_rep.get("contributions", 0)
            )
            save_db()
            rep = db["reputation"][uid_str]
            logger.info("♻️  Puntos GF→local: uid=%d pts=%d", uid, gf_rep["points"])
    except Exception:
        pass

    pts      = rep.get("points", 0)
    contribs = rep.get("contributions", 0)

    # Obtener info de rango usando tabla oficial
    rank_info = get_rank_info(pts, uid)
    rank_key  = rank_info["key"]

    if uid in MASTER_UIDS:
        rank    = "🔮 Oráculo ⚡ [MASTER]"
        benefit = {
            "es": "Control total de Synergix. Eres la mente detrás de la red. 🧠",
            "en": "Full control of Synergix. You are the mind behind the network. 🧠",
            "zh_cn": "完全控制 Synergix。你是网络背后的大脑。🧠",
            "zh": "完全控制 Synergix。你是網路背後的大腦。🧠",
        }.get(lang, "Full control of Synergix. 🧠")
    else:
        rank    = tx[rank_key]
        benefit = tx[rank_key.replace("rank_", "benefit_")]

    next_pts = rank_info.get("next_pts")
    mult     = rank_info["multiplier"]
    dlimit   = rank_info["daily_limit"]
    progress_map = {
        "es": (f" ({next_pts - pts} pts para el siguiente nivel)" if next_pts else " (¡Nivel máximo! 🔮)"),
        "en": (f" ({next_pts - pts} pts to next level)" if next_pts else " (Max level! 🔮)"),
        "zh_cn": (f" (距下一级还需 {next_pts - pts} 积分)" if next_pts else " (已达最高级！🔮)"),
        "zh":    (f" (距下一級還需 {next_pts - pts} 積分)" if next_pts else " (已達最高級！🔮)"),
    }
    progress = progress_map.get(lang, progress_map["en"])

    # Línea extra con info del rango
    dlimit_str = "∞" if dlimit >= 999 else str(dlimit)
    rank_detail = {
        "es": f"\n✖️ Multiplicador: ×{mult} | 📅 Límite diario: {dlimit_str} aportes",
        "en": f"\n✖️ Multiplier: ×{mult} | 📅 Daily limit: {dlimit_str} contributions",
        "zh_cn": f"\n✖️ 倍率: ×{mult} | 📅 每日上限: {dlimit_str} 贡献",
        "zh":    f"\n✖️ 倍率: ×{mult} | 📅 每日上限: {dlimit_str} 貢獻",
    }.get(lang, "")

    impact = rep.get("impact", 0)
    await msg.answer(tx["status_msg"].format(
        total=total, challenge=get_challenge_text(lang),
        name=name, pts=pts, contribs=contribs,
        impact=impact, rank=rank + progress, benefit=benefit + rank_detail))


@dp.message(F.text.in_(BTN_MEMORY))
async def btn_memory(msg: Message) -> None:
    uid  = msg.from_user.id
    sync_lang(uid, msg.text)
    lang    = user_lang.get(uid, "es")
    uid_str = str(uid)
    tx      = T.get(lang, T["es"])

    entries = db["memory"].get(uid_str, [])
    if not entries:
        await msg.answer(tx["no_memory"]); return

    lines = []
    for i, e in enumerate(entries[:10]):
        score_str = f" ⭐{e['score']}/10" if e.get("score") else ""
        icon = "🏆" if e.get("quality") == "high" else "📌"
        lines.append(f"{icon} {i+1}. CID: {e['cid']}{score_str}\n   📝 {e.get('summary','Sin resumen')}")

    rep = db["reputation"].get(uid_str, {"points":0,"contributions":0})
    await msg.answer(
        tx["memory_title"] + "\n\n".join(lines) +
        tx["memory_footer"].format(pts=rep.get("points",0), contribs=rep.get("contributions",0))
    )


@dp.message(F.text.in_(BTN_LANG))
async def btn_lang(msg: Message) -> None:
    uid = msg.from_user.id; sync_lang(uid, msg.text)
    await msg.answer(t(uid, "select_lang"), reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🇪🇸 Español", callback_data="lang_es"),
         InlineKeyboardButton(text="🇬🇧 English",  callback_data="lang_en")],
        [InlineKeyboardButton(text="🇨🇳 简体",      callback_data="lang_zh_cn"),
         InlineKeyboardButton(text="🇹🇼 繁體",      callback_data="lang_zh")],
    ]))


@dp.callback_query(F.data.startswith("lang_"))
async def cb_lang(cb: CallbackQuery) -> None:
    uid  = cb.from_user.id
    lang = cb.data.split("lang_")[1]
    user_lang[uid] = lang
    _save_user_setting(uid, "lang", lang)   # persistir idioma elegido
    try: await cb.message.delete()
    except Exception: pass
    await cb.message.answer(T[lang]["lang_set"], reply_markup=menu(uid))
    await cb.answer()


@dp.message(F.text.in_(BTN_CONTRIBUTE))
async def btn_contribute(msg: Message, state: FSMContext) -> None:
    uid = msg.from_user.id; sync_lang(uid, msg.text)
    await state.set_state(Form.waiting_contribution)
    await msg.answer(t(uid, "await_contrib"))


@dp.message(Form.waiting_contribution, F.text)
async def recv_text(msg: Message, state: FSMContext) -> None:
    await state.clear()
    uid  = msg.from_user.id
    lang = user_lang.get(uid, "es")
    c    = msg.text.strip()
    tx   = T.get(lang, T["es"])

    if len(c) < MIN_CHARS:
        await msg.answer(tx["contrib_short"].format(chars=len(c))); return

    # Verificar límite diario (primero DB local, luego GF si hay discrepancia)
    puede, count, limit = check_and_update_daily_limit(uid)
    if not puede:
        # Double-check con HEAD a GF por si hubo reinicio del servidor
        try:
            loop_d = asyncio.get_running_loop()
            gf_check = await loop_d.run_in_executor(None, lambda: gf_head_user(uid))
            if gf_check.get("exists") and gf_check.get("daily_reset"):
                # Sincronizar desde GF y re-verificar
                uid_str_d = str(uid)
                if "user_settings" not in db: db["user_settings"] = {}
                if uid_str_d not in db["user_settings"]: db["user_settings"][uid_str_d] = {}
                db["user_settings"][uid_str_d]["daily_count"] = str(gf_check.get("daily_count", count))
                db["user_settings"][uid_str_d]["daily_reset"] = gf_check.get("daily_reset", "")
                save_db()
                puede, count, limit = check_and_update_daily_limit(uid)
        except Exception:
            pass  # Mantener la verificación local

    if not puede:
        rank_info = get_rank_info(db["reputation"].get(str(uid), {}).get("points", 0), uid)
        role_name = tx.get(rank_info["key"], "Usuario")
        limit_msg = {
            "es": f"¡Estás imparable! 🔥 Hoy ya alcanzaste tu límite como {role_name} ({count}/{limit} aportes). Vuelve mañana, la red te necesita más fuerte.",
            "zh_cn": f"你势不可挡！🔥 今天已达到 {role_name} 的上限（{count}/{limit}）。明天再来，网络需要更强大的你。",
            "zh":    f"你勢不可擋！🔥 今天已達到 {role_name} 的上限（{count}/{limit}）。明天再來，網路需要更強大的你。",
            "en": f"You're unstoppable! 🔥 You've reached your daily limit as {role_name} ({count}/{limit} contributions). Come back tomorrow, the network needs you stronger.",
            "zh_cn": f"你势不可挡！🔥 今天已达到 {role_name} 的上限（{count}/{limit}）。明天再来，网络需要更强大的你。",
            "zh":    f"你勢不可擋！🔥 今天已達到 {role_name} 的上限（{count}/{limit}）。明天再來，網路需要更強大的你。",
        }.get(lang, f"Daily limit reached ({count}/{limit}). Come back tomorrow! 🔥")
        await msg.answer(limit_msg); return

    await msg.answer(tx["received"])
    try:
        _queue.put_nowait(ContribJob(uid=uid, name=msg.from_user.first_name or "Usuario",
                                     content=c, lang=lang, chat_id=msg.chat.id))
    except asyncio.QueueFull:
        await msg.answer(t(uid, "error"))


@dp.message(Form.waiting_contribution, F.voice)
async def recv_voice(msg: Message, state: FSMContext) -> None:
    await state.clear()
    uid  = msg.from_user.id
    lang = user_lang.get(uid, "es")
    wait = await msg.answer(T[lang]["transcribing"])
    try:
        import httpx
        fi  = await bot.get_file(msg.voice.file_id)
        url = f"https://api.telegram.org/file/bot{TOKEN}/{fi.file_path}"
        async with httpx.AsyncClient(timeout=30) as client:
            audio = await client.get(url)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".ogg") as tmp:
            tmp.write(audio.content); tmp_path = tmp.name

        # Transcripción 100% local con Qwen/faster-whisper
        content = await transcribe_audio(tmp_path, lang=lang)
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        if not content:
            content = "[Audio no transcrito — instala: pip install faster-whisper]"
        await bot.delete_message(msg.chat.id, wait.message_id)
        if len(content) < MIN_CHARS:
            await msg.answer(T[lang]["contrib_short"].format(chars=len(content))); return
        await msg.answer(T[lang]["received"])
        _queue.put_nowait(ContribJob(uid=uid, name=msg.from_user.first_name or "Usuario",
                                     content=content, lang=lang, chat_id=msg.chat.id))
    except Exception as e:
        logger.error("voice error uid=%d: %s", uid, e)
        try: await bot.delete_message(msg.chat.id, wait.message_id)
        except Exception: pass
        await msg.answer(t(uid, "contrib_fail"))


@dp.message(F.sticker)
async def handle_sticker(msg: Message) -> None:
    import random
    uid     = msg.from_user.id
    uid_str = str(uid)
    if uid not in user_lang:
        user_lang[uid] = get_lang(uid, msg.from_user.language_code or "")

    emoji    = msg.sticker.emoji or "😊"
    set_name = msg.sticker.set_name

    # ── PASO 1: Intentar responder con sticker del mismo pack ────────────────
    sticker_sent = False
    if set_name:
        try:
            await bot.send_chat_action(msg.chat.id, "choose_sticker")
            sticker_set = await bot.get_sticker_set(set_name)
            # Preferir sticker con mismo emoji, evitar el mismo que mandaron
            same_emoji  = [s for s in sticker_set.stickers
                           if s.emoji == emoji and s.file_id != msg.sticker.file_id]
            other       = [s for s in sticker_set.stickers
                           if s.file_id != msg.sticker.file_id]
            pool = same_emoji if same_emoji else other[:15]
            if pool:
                chosen = random.choice(pool)
                await msg.answer_sticker(chosen.file_id)
                sticker_sent = True
        except Exception as e:
            logger.debug("⚠️ Sticker set error: %s", e)

    # ── PASO 2: Ocasionalmente añadir texto corto (30% de probabilidad) ──────
    # O siempre si no se pudo mandar sticker
    add_text = (not sticker_sent) or (random.random() < 0.30)

    if add_text:
        await bot.send_chat_action(msg.chat.id, "typing")
        await _do_chat(msg, emoji, is_sticker=True)



# ═══════════════════════════════════════════════════════════════════════════════
# VALIDACIÓN — Mente Colmena puede validar aportes de otros
# ═══════════════════════════════════════════════════════════════════════════════

# Aportes pendientes de validación: {cid: {uid, content, summary, score, lang}}
_pending_validation: dict = {}


def can_validate(uid: int) -> bool:
    """Solo Mente Colmena (rank_5) y Oráculo (rank_6) pueden validar."""
    pts = db["reputation"].get(str(uid), {}).get("points", 0)
    info = get_rank_info(pts, uid)
    return info["level"] >= 4  # rank_5 = Mente Colmena, rank_6 = Oráculo


@dp.message(F.text.startswith("/validar"))
async def cmd_validar(msg: Message) -> None:
    """
    /validar {cid} — Mente Colmena aprueba un aporte pendiente.
    /validar rechazar {cid} — Rechaza el aporte.
    El validador gana puntos por cada validación.
    """
    uid  = msg.from_user.id
    lang = user_lang.get(uid, "es")

    if not can_validate(uid):
        no_perm = {
            "es": "🔒 Solo los rangos Mente Colmena 🧠 y Oráculo 🔮 pueden validar aportes.\nSigue contribuyendo para alcanzar ese nivel. 📈",
            "zh_cn": "🔒 只有蜂巢思维🧠和神谕🔮等级才能验证。继续贡献以达到该等级。📈",
            "zh":    "🔒 只有蜂巢思維🧠和神諭🔮等級才能驗證。繼續貢獻以達到該等級。📈",
            "en": "🔒 Only Hive Mind 🧠 and Oracle 🔮 ranks can validate contributions.\nKeep contributing to reach that level. 📈",
            "zh_cn": "🔒 只有蜂巢思维🧠和神谕🔮等级才能验证贡献。继续贡献以达到该等级。📈",
            "zh":    "🔒 只有蜂巢思維🧠和神諭🔮等級才能驗證貢獻。繼續貢獻以達到該等級。📈",
        }
        await msg.answer(no_perm.get(lang, no_perm["es"]))
        return

    parts = msg.text.strip().split()
    rechazar = len(parts) >= 2 and parts[1].lower() in ("rechazar", "reject", "拒绝")
    cid_idx  = 2 if rechazar else 1

    if len(parts) <= cid_idx:
        help_msg = {
            "es": "Uso: /validar {cid} — aprobar\n/validar rechazar {cid} — rechazar\n\nAportes pendientes: " + str(len(_pending_validation)),
            "en": "Usage: /validar {cid} — approve\n/validar rechazar {cid} — reject\n\nPending: " + str(len(_pending_validation)),
            "zh_cn": "用法：/validar {cid} — 批准\n/validar rechazar {cid} — 拒绝\n\n待验证：" + str(len(_pending_validation)),
            "zh":    "用法：/validar {cid} — 批准\n/validar rechazar {cid} — 拒絕\n\n待驗證：" + str(len(_pending_validation)),
        }
        await msg.answer(help_msg.get(lang, help_msg["es"]))
        return

    cid = parts[cid_idx]

    # Buscar aporte en pendientes o en memoria
    target = _pending_validation.get(cid)
    if not target:
        # Buscar en toda la memoria
        for u_str, entries in db.get("memory", {}).items():
            for e in entries:
                if e.get("cid", "").startswith(cid[:16]):
                    target = {**e, "uid": u_str}
                    break
            if target:
                break

    # HEAD a GF para confirmar existencia del objeto
    if not target:
        try:
            loop_v   = asyncio.get_running_loop()
            # Intentar encontrar el objeto por CID en el cache RAG
            for obj_name, meta in _rag_cache.items():
                if meta.get("cid", "").startswith(cid[:12]):
                    gf_meta = await loop_v.run_in_executor(
                        None, lambda on=obj_name: gf_head_object(on)
                    )
                    if gf_meta.get("_exists"):
                        ai_sum = gf_meta.get("ai-summary", "")
                        qs_raw = gf_meta.get("quality-score", "5")
                        uid_raw = gf_meta.get("user-id", "")
                        uid_parts = uid_raw.split("|")
                        author_uid_v = uid_parts[0] if uid_parts else ""
                        target = {
                            "uid":          author_uid_v,
                            "cid":          cid,
                            "object_name":  obj_name,
                            "summary":      ai_sum,
                            "score":        int(qs_raw.split("|")[0]) if qs_raw.split("|")[0].isdigit() else 5,
                            "quality":      qs_raw.split("|")[1] if len(qs_raw.split("|")) > 1 else "standard",
                        }
                        break
        except Exception as e:
            logger.debug("HEAD validar: %s", e)

    if not target:
        not_found = {
            "es": f"⚠️ No encontré el aporte con CID: {cid[:20]}...",
            "en": f"⚠️ Couldn't find the contribution with CID: {cid[:20]}...",
            "zh_cn": f"⚠️ 找不到CID为 {cid[:20]}... 的贡献",
            "zh":    f"⚠️ 找不到CID為 {cid[:20]}... 的貢獻",
        }
        await msg.answer(not_found.get(lang, not_found["en"]))
        return

    author_uid = target.get("uid", "")
    summary    = target.get("summary", "Sin resumen")[:80]
    score_orig = target.get("score", 5)

    uid_str_v = str(uid)

    if not rechazar:
        # ── APROBAR ──────────────────────────────────────────────────────────
        # Subir calidad del aporte validado
        for e in db.get("memory", {}).get(author_uid, []):
            if e.get("cid", "").startswith(cid[:16]):
                e["validated_by"] = uid_str_v
                e["quality"] = "validated"
                e["score"]   = min(10, score_orig + 1)  # +1 bonus por validación
                break

        # Puntos al validador
        val_pts = calc_points(5, db["reputation"].get(uid_str_v, {}).get("points", 0), uid)
        if uid_str_v not in db["reputation"]:
            db["reputation"][uid_str_v] = {"points": 0, "contributions": 0, "impact": 0}
        db["reputation"][uid_str_v]["points"] += val_pts
        db["reputation"][uid_str_v]["validated_count"] = (
            db["reputation"][uid_str_v].get("validated_count", 0) + 1
        )

        # Puntos al autor por ser validado
        if author_uid and author_uid in db["reputation"]:
            db["reputation"][author_uid]["points"] += calc_points(
                10, db["reputation"][author_uid].get("points", 0)
            )

        save_db()
        _pending_validation.pop(cid, None)

        ok_msg = {
            "es": f"✅ Aporte validado por ti 🧠\n\n📝 {summary}...\n\n+{val_pts} pts para ti | +10 pts para el autor\nEl conocimiento de la red es más confiable gracias a ti. 🔗",
            "zh_cn": f"✅ 你验证了此贡献 🧠\n\n📝 {summary}...\n\n+{val_pts} 分给你 | +10 分给作者\n感谢你，网络知识更可靠。🔗",
            "zh":    f"✅ 你驗證了此貢獻 🧠\n\n📝 {summary}...\n\n+{val_pts} 分給你 | +10 分給作者\n感謝你，網路知識更可靠。🔗",
            "en": f"✅ Contribution validated by you 🧠\n\n📝 {summary}...\n\n+{val_pts} pts for you | +10 pts for the author\nThe network's knowledge is more reliable thanks to you. 🔗",
            "zh_cn": f"✅ 贡献已由你验证 🧠\n\n📝 {summary}...\n\n+{val_pts} 分给你 | +10 分给作者\n感谢你，网络知识更可靠。🔗",
            "zh":    f"✅ 貢獻已由你驗證 🧠\n\n📝 {summary}...\n\n+{val_pts} 分給你 | +10 分給作者\n感謝你，網路知識更可靠。🔗",
        }
        await msg.answer(ok_msg.get(lang, ok_msg["en"]))

        # Notificar al autor
        if author_uid:
            try:
                author_lang = user_lang.get(int(author_uid), "es")
                author_notif = {
                    "es": f"🌟 ¡Tu aporte fue validado por un Mente Colmena/Oráculo!\n📝 {summary}...\n+10 pts bonus por validación 🎉",
                    "zh_cn": f"🌟 你的贡献已被蜂巢思维/神谕验证！\n📝 {summary}...\n+10 验证奖励积分 🎉",
                    "zh":    f"🌟 你的貢獻已被蜂巢思維/神諭驗證！\n📝 {summary}...\n+10 驗證獎勵積分 🎉",
                    "en": f"🌟 Your contribution was validated by a Hive Mind/Oracle!\n📝 {summary}...\n+10 bonus pts for validation 🎉",
                    "zh_cn": f"🌟 你的贡献已被蜂巢思维/神谕验证！\n📝 {summary}...\n+10 验证奖励积分 🎉",
                    "zh":    f"🌟 你的貢獻已被蜂巢思維/神諭驗證！\n📝 {summary}...\n+10 驗證獎勵積分 🎉",
                }
                await bot.send_message(int(author_uid), author_notif.get(author_lang, author_notif["en"]))
            except Exception:
                pass

    else:
        # ── RECHAZAR ─────────────────────────────────────────────────────────
        for e in db.get("memory", {}).get(author_uid, []):
            if e.get("cid", "").startswith(cid[:16]):
                e["quality"] = "rejected_by_colmena"
                break
        save_db()
        _pending_validation.pop(cid, None)

        rej_msg = {
            "es": f"🚫 Aporte rechazado.\n📝 {summary}...\nHas ejercido tu derecho como Mente Colmena. La red agradece tu curaduría. 🧠",
            "zh_cn": f"🚫 贡献已拒绝。\n📝 {summary}...\n你行使了蜂巢思维的策管权。🧠",
            "zh":    f"🚫 貢獻已拒絕。\n📝 {summary}...\n你行使了蜂巢思維的策管權。🧠",
            "en": f"🚫 Contribution rejected.\n📝 {summary}...\nYou've exercised your Hive Mind curation right. The network thanks you. 🧠",
            "zh_cn": f"🚫 贡献已拒绝。\n📝 {summary}...\n你行使了蜂巢思维的策管权。🧠",
            "zh":    f"🚫 貢獻已拒絕。\n📝 {summary}...\n你行使了蜂巢思維的策管權。🧠",
        }
        await msg.answer(rej_msg.get(lang, rej_msg["en"]))


@dp.message(F.text == "/top")
async def cmd_top(msg: Message) -> None:
    """
    Muestra el leaderboard global:
    - Top 10 por score total
    - Top 3 por impacto (aportes más usados)
    - Top aporte de la semana (challenge)
    """
    uid  = msg.from_user.id
    if uid not in user_lang:
        user_lang[uid] = get_lang(uid, msg.from_user.language_code or "")
    lang = user_lang.get(uid, "es")

    # ── Top 10 por score ──────────────────────────────────────────────────────
    all_users = []
    for uid_str, rep in db.get("reputation", {}).items():
        pts    = rep.get("points", 0)
        impact = rep.get("impact", 0)
        contribs = rep.get("contributions", 0)
        if pts > 0 or contribs > 0:
            name = db.get("user_settings", {}).get(uid_str, {}).get("lang", "")
            # Obtener nombre guardado
            saved_name = db.get("user_settings", {}).get(uid_str, {}).get("name", f"#{uid_str[-4:]}")
            rank_info  = get_rank_info(pts, int(uid_str) if uid_str.isdigit() else 0)
            all_users.append({
                "uid":     uid_str,
                "name":    saved_name,
                "pts":     pts,
                "impact":  impact,
                "contribs": contribs,
                "rank_key": rank_info["key"],
            })

    top_pts    = sorted(all_users, key=lambda x: -x["pts"])[:10]
    top_impact = sorted(all_users, key=lambda x: -x["impact"])[:3]

    medals_10 = ["🥇","🥈","🥉","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟"]
    medals_3  = ["🥇","🥈","🥉"]
    tx = T.get(lang, T["es"])

    # Construir líneas top score
    score_lines = []
    for i, u in enumerate(top_pts):
        rank_name = tx.get(u["rank_key"], u["rank_key"])
        is_me = u["uid"] == str(uid)
        me_tag = " ← tú" if is_me else ""
        score_lines.append(
            f"{medals_10[i]} {u['name']} {rank_name}\n"
            f"   📈 {u['pts']} pts | 🔗 {u['contribs']} aportes | 🔁 {u['impact']} usos{me_tag}"
        )

    # Construir líneas top impacto
    impact_lines = []
    for i, u in enumerate(top_impact):
        if u["impact"] > 0:
            impact_lines.append(f"{medals_3[i]} {u['name']} — 🔁 {u['impact']} usos | 📈 {u['pts']} pts")

    # Challenge top aporte de la semana
    ch_top = await get_top_challenge_aportes(top_n=1)
    ch_line = ""
    if ch_top:
        a = ch_top[0]
        author_name = db.get("user_settings", {}).get(a["uid"], {}).get("name", f"#{a['uid'][-4:]}")
        ch_line = f"⭐ {author_name} — Score {a['score']}/10\n   📝 {a['summary'][:80]}..."

    # Ensamblar mensajes
    top_msgs = {
        "es": (
            f"🏆 LEADERBOARD SYNERGIX\n"
            f"{'='*30}\n\n"
            f"📊 TOP SCORE GLOBAL\n\n" +
            "\n\n".join(score_lines or ["Sin datos aún"]) +
            (f"\n\n{'='*30}\n🔁 TOP IMPACTO\n(aportes más usados por la IA)\n\n" +
             "\n".join(impact_lines) if impact_lines else "") +
            (f"\n\n{'='*30}\n🏆 MEJOR APORTE DEL CHALLENGE\n{get_challenge_text('es')}\n\n{ch_line}" if ch_line else "") +
            f"\n\n🔗 Total usuarios: {len(all_users)} | Aportes: {db['global_stats'].get('total_contributions', 0)}"
        ),
        "en": (
            f"🏆 SYNERGIX LEADERBOARD\n"
            f"{'='*30}\n\n"
            f"📊 TOP GLOBAL SCORE\n\n" +
            "\n\n".join(score_lines or ["No data yet"]) +
            (f"\n\n{'='*30}\n🔁 TOP IMPACT\n(most used by AI)\n\n" +
             "\n".join(impact_lines) if impact_lines else "") +
            (f"\n\n{'='*30}\n🏆 BEST CHALLENGE CONTRIBUTION\n{get_challenge_text('en')}\n\n{ch_line}" if ch_line else "") +
            f"\n\n🔗 Total users: {len(all_users)} | Contributions: {db['global_stats'].get('total_contributions', 0)}"
        ),
        "zh_cn": (
            f"🏆 SYNERGIX 排行榜\n"
            f"{'='*30}\n\n"
            f"📊 全球积分排行\n\n" +
            "\n\n".join(score_lines or ["暂无数据"]) +
            (f"\n\n{'='*30}\n🔁 影响力排行\n\n" + "\n".join(impact_lines) if impact_lines else "") +
            (f"\n\n{'='*30}\n🏆 本周最佳贡献\n{get_challenge_text('zh_cn')}\n\n{ch_line}" if ch_line else "") +
            f"\n\n🔗 总用户：{len(all_users)} | 贡献：{db['global_stats'].get('total_contributions', 0)}"
        ),
        "zh": (
            f"🏆 SYNERGIX 排行榜\n"
            f"{'='*30}\n\n"
            f"📊 全球積分排行\n\n" +
            "\n\n".join(score_lines or ["暫無數據"]) +
            (f"\n\n{'='*30}\n🔁 影響力排行\n\n" + "\n".join(impact_lines) if impact_lines else "") +
            (f"\n\n{'='*30}\n🏆 本週最佳貢獻\n{get_challenge_text('zh')}\n\n{ch_line}" if ch_line else "") +
            f"\n\n🔗 總用戶：{len(all_users)} | 貢獻：{db['global_stats'].get('total_contributions', 0)}"
        ),
    }

    await msg.answer(top_msgs.get(lang, top_msgs["es"]))

@dp.message(F.text.startswith("/reach") | F.text.startswith("/buscar online"))
async def cmd_reach(msg: Message) -> None:
    """
    /reach <query> — Búsqueda manual en redes sociales e internet.
    Busca en: Web, YouTube, GitHub, Twitter/X, Reddit, Telegram, TikTok.
    """
    uid  = msg.from_user.id
    lang = user_lang.get(uid, "es")

    parts = msg.text.strip().split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        help_msgs = {
            "es": "🌐 Uso: /reach <tema>\nEjemplo: /reach Synergix BNB Greenfield\n\nBusca en: Web, YouTube, GitHub, Twitter/X, Reddit, Telegram, TikTok",
            "en": "🌐 Usage: /reach <topic>\nExample: /reach Synergix BNB Greenfield\n\nSearches: Web, YouTube, GitHub, Twitter/X, Reddit, Telegram, TikTok",
            "zh": "🌐 用法：/reach <主题>\n示例：/reach Synergix BNB Greenfield\n\n搜索：网页、YouTube、GitHub、Twitter/X、Reddit、Telegram、TikTok",
            "zht":"🌐 用法：/reach <主題>\n示例：/reach Synergix BNB Greenfield\n\n搜索：網頁、YouTube、GitHub、Twitter/X、Reddit、Telegram、TikTok",
        }
        await msg.answer(help_msgs.get(lang, help_msgs["en"]))
        return

    query = parts[1].strip()
    searching_msgs = {
        "es": f"🔍 Buscando '{query}' en redes sociales e internet...\n⏳ Puede tardar hasta 20 segundos.",
        "en": f"🔍 Searching '{query}' across social media & web...\n⏳ May take up to 20 seconds.",
        "zh": f"🔍 正在搜索'{query}'...\n⏳ 最多需要20秒。",
        "zht":f"🔍 正在搜索'{query}'...\n⏳ 最多需要20秒。",
    }
    wait_msg = await msg.answer(searching_msgs.get(lang, searching_msgs["en"]))
    await bot.send_chat_action(msg.chat.id, "typing")

    try:
        result = await reach_internet(query, lang=lang, max_time=20.0)

        if not result:
            no_result = {
                "es": f"🔍 No encontré resultados en internet para '{query}'.\nIntenta con términos más específicos.",
                "en": f"🔍 No results found online for '{query}'.\nTry more specific terms.",
                "zh": f"🔍 未找到关于'{query}'的互联网结果。\n请尝试更具体的词语。",
                "zht":f"🔍 未找到關於'{query}'的互聯網結果。\n請嘗試更具體的詞語。",
            }
            await bot.edit_message_text(
                no_result.get(lang, no_result["en"]),
                chat_id=msg.chat.id,
                message_id=wait_msg.message_id
            )
            return

        # Construir prompt con los resultados de reach
        system = {
            "es": (
                "Eres Synergix. El usuario pidió buscar en internet y aquí están los resultados. "
                "Resume los hallazgos más relevantes en 3-5 puntos concisos. "
                "Menciona las fuentes (Web/YouTube/GitHub/Twitter/Reddit). "
                "Sin asteriscos, sin encabezados. Idioma: español."
            ),
            "en": (
                "You are Synergix. The user asked to search the internet and here are the results. "
                "Summarize the most relevant findings in 3-5 concise points. "
                "Mention the sources (Web/YouTube/GitHub/Twitter/Reddit). "
                "No asterisks, no headers. Language: English."
            ),
            "zh": (
                "你是Synergix。用户请求搜索互联网，以下是搜索结果。"
                "用3-5个简洁要点总结最相关的发现。注明来源。不用星号和标题。简体中文回复。"
            ),
            "zht": (
                "你是Synergix。用戶請求搜索互聯網，以下是搜索結果。"
                "用3-5個簡潔要點總結最相關的發現。注明來源。不用星號和標題。繁體中文回覆。"
            ),
        }.get(lang, "")

        messages = [
            {"role": "system",  "content": system + "\n\n" + result[:3000]},
            {"role": "user",    "content": "Busqué: " + query},
        ]
        summary = await groq_call(messages, temperature=0.3)

        # Añadir al historial de chat
        uid_str = str(uid)
        if uid_str not in db["chat"]: db["chat"][uid_str] = []
        db["chat"][uid_str].append({"role": "user",      "content": "/reach " + query})
        db["chat"][uid_str].append({"role": "assistant", "content": summary})
        db["chat"][uid_str] = db["chat"][uid_str][-20:]
        save_db()

        await bot.edit_message_text(
            summary,
            chat_id=msg.chat.id,
            message_id=wait_msg.message_id
        )

    except Exception as e:
        logger.error("cmd_reach error: %s", e)
        err_msgs = {
            "es": "❌ Error al buscar en internet. Inténtalo en un momento.",
            "en": "❌ Error searching the internet. Try again in a moment.",
            "zh": "❌ 搜索互联网时出错。请稍后重试。",
            "zht":"❌ 搜索互聯網時出錯。請稍後重試。",
        }
        await bot.edit_message_text(
            err_msgs.get(lang, err_msgs["en"]),
            chat_id=msg.chat.id,
            message_id=wait_msg.message_id
        )


@dp.message(F.text == "/challenge")
async def cmd_challenge(msg: Message) -> None:
    """Muestra el challenge activo y el histórico de ganadores."""
    uid  = msg.from_user.id
    if uid not in user_lang:
        user_lang[uid] = get_lang(uid, msg.from_user.language_code or "")
    lang = user_lang.get(uid, "es")
    ch   = get_active_challenge()
    week_start, week_end = get_week_start_end()
    week_num = get_current_week_number()

    last_winners = db.get("global_stats", {}).get("last_challenge_winners", [])
    last_topic   = db.get("global_stats", {}).get("last_challenge_topic", {})

    ch_msgs = {
        "es": (
            f"🏆 CHALLENGE SEMANAL — Semana {week_num}\n\n"
            f"📌 Tema actual:\n{ch.get('es', '')}\n\n"
            f"📅 Período: {week_start} → {week_end}\n\n"
            f"💡 Contribuye sobre este tema para ganar puntos extra y aparecer en el Top 3 del domingo. 🔥"
        ),
        "en": (
            f"🏆 WEEKLY CHALLENGE — Week {week_num}\n\n"
            f"📌 Current topic:\n{ch.get('en', '')}\n\n"
            f"📅 Period: {week_start} → {week_end}\n\n"
            f"💡 Contribute on this topic to earn extra points and appear in Sunday's Top 3. 🔥"
        ),
        "zh_cn": (
            f"🏆 每周挑战 — 第{week_num}周\n\n"
            f"📌 当前主题:\n{ch.get('zh_cn', '')}\n\n"
            f"📅 时间: {week_start} → {week_end}\n\n"
            f"💡 就此主题贡献以获得额外积分并进入周日Top 3。🔥"
        ),
        "zh": (
            f"🏆 每週挑戰 — 第{week_num}週\n\n"
            f"📌 當前主題:\n{ch.get('zh', '')}\n\n"
            f"📅 時間: {week_start} → {week_end}\n\n"
            f"💡 就此主題貢獻以獲得額外積分並進入週日Top 3。🔥"
        ),
    }
    await msg.answer(ch_msgs.get(lang, ch_msgs["es"]))


@dp.message(F.text)
async def free_chat(msg: Message) -> None:
    uid = msg.from_user.id
    if uid not in user_lang:
        user_lang[uid] = get_lang(uid, msg.from_user.language_code or "")
    await bot.send_chat_action(msg.chat.id, "typing")
    await _do_chat(msg, msg.text)

# ═══════════════════════════════════════════════════════════════════════════════
# LOOPS EN BACKGROUND
# ═══════════════════════════════════════════════════════════════════════════════

async def federation_loop() -> None:
    """Cada 8 min: fusiona aportes → actualiza wisdom → sube SYNERGIXAI/ + backup + logs"""
    await asyncio.sleep(60)  # Esperar 1 min al arranque antes del primer ciclo
    while True:
        logger.info("📈 [Federation] Iniciando ciclo...")
        summaries = [e.get("summary","") for uk in db["memory"]
                     for e in db["memory"][uk] if e.get("summary")]

        # OPT GAS: solo procesar si el conteo de aportes cambió desde el último ciclo
        current_total = db["global_stats"].get("total_contributions", 0)
        last_fed_total = db["global_stats"].get("_last_fed_total", -1)

        if current_total == last_fed_total and last_fed_total > 0:
            logger.info("⏭️  Federation: sin nuevos aportes (%d) — solo sync DB y users", current_total)
            # Sin aportes nuevos: no fusionar ni subir cerebro, pero sí sincronizar
            # cambios de reputación, daily_count, puntos residuales, etc.
            if _pending_user_updates:
                users_to_flush = list(_pending_user_updates)
                _pending_user_updates.clear()
                loop_u = asyncio.get_running_loop()
                for _uid in users_to_flush:
                    try:
                        _uname = db.get("user_settings", {}).get(str(_uid), {}).get("name", "")
                        _ulang = user_lang.get(_uid, db.get("user_settings", {}).get(str(_uid), {}).get("lang", "es"))
                        await loop_u.run_in_executor(
                            None, lambda u=_uid, n=_uname, l=_ulang: gf_update_user(u, n, l)
                        )
                    except Exception as ue:
                        logger.debug("⚠️ batch GF user %d: %s", _uid, ue)
            # Sync DB si hubo cambios (puntos residuales, etc.)
            try:
                await sync_db_to_gf()
            except Exception as e:
                logger.error("❌ sync_db_to_gf (idle): %s", e)
            await asyncio.sleep(480)
            continue

        db["global_stats"]["_last_fed_total"] = current_total
        save_db()

        if len(summaries) >= 2:
            context = " | ".join(summaries[-50:])
            try:
                system = ("Analiza estos aportes y extrae hechos concretos, patrones y sabiduría colectiva. "
                          "Produce un párrafo denso en información. Solo texto plano, sin asteriscos.")
                wisdom = await groq_call(
                    [{"role":"system","content":system},{"role":"user","content":context}],
                    model=MODEL_CHAT, temperature=0.3)
                db["global_stats"]["collective_wisdom"] = wisdom
                save_db()
                logger.info("🌟 [Federation] Sabiduría actualizada")

                # ✅ Rebuild cache RAG con nuevos aportes
                _build_rag_cache_from_db()
                logger.info("🔍 RAG cache actualizado: %d aportes", len(_rag_cache))



                # ✅ HEAD al cerebro antes de subir — verificar si realmente hay cambios
                try:
                    loop_h = asyncio.get_running_loop()
                    brain_meta = await loop_h.run_in_executor(
                        None, lambda: gf_head_object(GF.BRAIN_FILE)
                    )
                    gf_last_sync = brain_meta.get("last-sync", "")
                    local_vectors = str(len(all_summaries))
                    gf_vectors    = brain_meta.get("vector-count", "0")

                    # Subir solo si cambió el vector-count o no existe
                    if not brain_meta.get("_exists") or gf_vectors != local_vectors:
                        logger.info("🔄 Cerebro GF desactualizado (%s→%s vectores), subiendo...",
                                    gf_vectors, local_vectors)
                        await upload_brain_to_gf(wisdom)
                    else:
                        logger.info("⏭️  Cerebro GF actualizado (vectores=%s), omitiendo subida",
                                    gf_vectors)
                except Exception:
                    # Si el HEAD falla, subir de todas formas
                    await upload_brain_to_gf(wisdom)

            except Exception as e:
                logger.error("❌ Federation error completo: %s", e, exc_info=True)
                log_event("federation_error", 0, str(e)[:200], "critical")

        # ✅ OPT GAS: Backup solo los lunes (1 vez/semana en lugar de diario)
        # De 30 txs/mes → 4 txs/mes (ahorro del 87%)
        if datetime.now().weekday() == 0:  # 0 = lunes
            try:
                await upload_backup_to_gf()
            except Exception as e:
                logger.error("❌ Backup GF falló: %s", e)
        else:
            logger.debug("⏭️  Backup: solo lunes, hoy es %s", datetime.now().strftime("%A"))

        # ✅ Flush batch de user updates pendientes (OPT GAS: 1 escritura por usuario)
        if _pending_user_updates:
            users_to_flush = list(_pending_user_updates)
            _pending_user_updates.clear()
            loop_u = asyncio.get_running_loop()
            for _uid in users_to_flush:
                try:
                    _uname = db.get("user_settings", {}).get(str(_uid), {}).get("name", "")
                    _ulang = user_lang.get(_uid, db.get("user_settings", {}).get(str(_uid), {}).get("lang", "es"))
                    await loop_u.run_in_executor(
                        None, lambda u=_uid, n=_uname, l=_ulang: gf_update_user(u, n, l)
                    )
                except Exception as ue:
                    logger.debug("⚠️ batch GF user %d: %s", _uid, ue)
            logger.info("✅ Batch GF users: %d actualizados", len(users_to_flush))

        # ✅ Flush logs del día
        try:
            await flush_logs_to_gf()
        except Exception as e:
            logger.error("❌ Log flush GF falló: %s", e)

        # ✅ Sincronizar DB completa a GF (Greenfield = disco duro)
        try:
            await sync_db_to_gf()
        except Exception as e:
            logger.error("❌ sync_db_to_gf falló: %s", e)

        await asyncio.sleep(480)  # 8 minutos



# ═══════════════════════════════════════════════════════════════════════════════
# DEDUPLICACIÓN DE APORTES — Evita subir contenido duplicado al bucket
# ═══════════════════════════════════════════════════════════════════════════════

def _content_fingerprint(text: str) -> str:
    """
    Genera un fingerprint del contenido para detectar duplicados.
    Normaliza: minúsculas, sin espacios extra, sin puntuación frecuente.
    """
    import hashlib, re
    normalized = re.sub(r"[\s.,;:!?¿¡]+", " ", text.lower()).strip()
    return hashlib.sha256(normalized.encode()).hexdigest()[:24]

def _is_duplicate_contrib(content: str, uid_str: str, threshold: float = 0.85) -> tuple[bool, str]:
    """
    Verifica si el contenido ya existe en la memoria inmortal (cache RAG).
    Compara contra los summaries del cache usando similitud de keywords.

    Returns: (is_duplicate: bool, similar_summary: str)
    """
    # 1. Fingerprint exacto — buscar en user_settings
    fp = _content_fingerprint(content)
    known_fps = db.get("user_settings", {}).get(uid_str, {}).get("contrib_fps", [])
    if fp in known_fps:
        return True, "contenido idéntico detectado"

    # 2. Similitud semántica aproximada por keywords contra el cache RAG
    if not _rag_cache:
        return False, ""

    content_lower = content.lower()
    content_words = set(w for w in content_lower.split() if len(w) > 3)

    if not content_words:
        return False, ""

    best_match = ""
    best_score = 0.0

    for obj, meta in _rag_cache.items():
        # Solo revisar aportes del mismo usuario para evitar falsos positivos
        uid_meta = meta.get("user-id", "")
        if uid_hash(int(uid_str) if uid_str.isdigit() else 0) not in uid_meta:
            continue

        summary = meta.get("ai-summary", "")
        if not summary:
            continue

        summary_words = set(w for w in summary.lower().split() if len(w) > 3)
        if not summary_words:
            continue

        # Jaccard similarity
        intersection = content_words & summary_words
        union        = content_words | summary_words
        score        = len(intersection) / len(union) if union else 0

        if score > best_score:
            best_score = score
            best_match = summary[:100]

    if best_score >= threshold:
        return True, best_match

    return False, ""

def _register_contrib_fingerprint(uid_str: str, content: str) -> None:
    """Registra el fingerprint del aporte para futura deduplicación."""
    fp = _content_fingerprint(content)
    if "user_settings" not in db: db["user_settings"] = {}
    if uid_str not in db["user_settings"]: db["user_settings"][uid_str] = {}
    fps = db["user_settings"][uid_str].get("contrib_fps", [])
    if fp not in fps:
        fps.append(fp)
        fps = fps[-50:]  # Mantener solo los últimos 50 fingerprints
        db["user_settings"][uid_str]["contrib_fps"] = fps
        save_db()


# ═══════════════════════════════════════════════════════════════════════════════
# REPORTES AUTOMÁTICOS — Diario (00:00 UTC) y Semanal (lunes 00:05 UTC)
# ═══════════════════════════════════════════════════════════════════════════════

async def send_daily_report(uid: int, uid_str: str, lang: str) -> None:
    """Genera y envía el reporte diario personal a un usuario."""
    try:
        rep      = db["reputation"].get(uid_str, {})
        settings = db.get("user_settings", {}).get(uid_str, {})
        pts      = rep.get("points", 0)
        rank_info= get_rank_info(pts, uid)

        # Aportes de hoy
        contribs_today = int(settings.get("daily_count", 0))
        # Puntos ganados hoy (guardamos en daily_pts_earned)
        pts_today      = int(settings.get("daily_pts_earned", 0))
        # Impacto de hoy (veces que usaron sus aportes hoy)
        impact_today   = int(settings.get("daily_impact", 0))

        # Si no hubo actividad hoy, no enviar reporte
        if contribs_today == 0 and pts_today == 0 and impact_today == 0:
            return

        # Posición en el ranking global
        all_pts  = sorted(
            [(u, d.get("points", 0)) for u, d in db["reputation"].items()],
            key=lambda x: -x[1]
        )
        position = next((i+1 for i, (u,_) in enumerate(all_pts) if u == uid_str), "?")
        total_users = len(all_pts)

        # Progreso al siguiente rango
        next_pts    = rank_info.get("next_pts")
        progress_str = ""
        if next_pts:
            needed  = next_pts - pts
            pct     = min(100, int((pts - rank_info["min_pts"]) /
                          max(1, next_pts - rank_info["min_pts"]) * 100))
            progress_bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
            progress_str = {
                "es": f"\n📊 Progreso al siguiente rango:\n{progress_bar} {pct}% ({needed} pts para {next_pts})",
                "en": f"\n📊 Progress to next rank:\n{progress_bar} {pct}% ({needed} pts to {next_pts})",
                "zh": f"\n📊 升级进度：\n{progress_bar} {pct}% (还需{needed}分)",
                "zht":f"\n📊 升級進度：\n{progress_bar} {pct}% (還需{needed}分)",
            }.get(lang, "")
        else:
            progress_str = {
                "es": "\n🔮 Eres Oráculo — rango máximo alcanzado.",
                "en": "\n🔮 You are an Oracle — maximum rank reached.",
                "zh": "\n🔮 你是神谕 — 已达最高等级。",
                "zht":"\n🔮 你是神諭 — 已達最高等級。",
            }.get(lang, "")

        msgs = {
            "es": (
                f"📊 *Reporte Diario — Synergix*\n\n"
                f"🏅 Rango: {rank_info['name']}\n"
                f"📈 Puntos totales: {pts:,}\n"
                f"🏆 Posición: #{position} de {total_users}\n\n"
                f"━━━ Hoy ━━━\n"
                f"📦 Aportes: {contribs_today}\n"
                f"💎 Puntos ganados: +{pts_today}\n"
                f"🔁 Veces que usaron tus aportes: {impact_today}\n"
                f"{progress_str}"
            ),
            "en": (
                f"📊 *Daily Report — Synergix*\n\n"
                f"🏅 Rank: {rank_info['name']}\n"
                f"📈 Total points: {pts:,}\n"
                f"🏆 Position: #{position} of {total_users}\n\n"
                f"━━━ Today ━━━\n"
                f"📦 Contributions: {contribs_today}\n"
                f"💎 Points earned: +{pts_today}\n"
                f"🔁 Times your contributions were used: {impact_today}\n"
                f"{progress_str}"
            ),
            "zh": (
                f"📊 *每日报告 — Synergix*\n\n"
                f"🏅 等级：{rank_info['name']}\n"
                f"📈 总积分：{pts:,}\n"
                f"🏆 排名：第{position}/{total_users}名\n\n"
                f"━━━ 今天 ━━━\n"
                f"📦 贡献：{contribs_today}\n"
                f"💎 获得积分：+{pts_today}\n"
                f"🔁 贡献被使用次数：{impact_today}\n"
                f"{progress_str}"
            ),
            "zht": (
                f"📊 *每日報告 — Synergix*\n\n"
                f"🏅 等級：{rank_info['name']}\n"
                f"📈 總積分：{pts:,}\n"
                f"🏆 排名：第{position}/{total_users}名\n\n"
                f"━━━ 今天 ━━━\n"
                f"📦 貢獻：{contribs_today}\n"
                f"💎 獲得積分：+{pts_today}\n"
                f"🔁 貢獻被使用次數：{impact_today}\n"
                f"{progress_str}"
            ),
        }

        await bot.send_message(uid, msgs.get(lang, msgs["en"]), parse_mode="Markdown")
        logger.info("📊 Reporte diario enviado uid=%d", uid)

        # Reset contadores diarios de reporte
        if uid_str in db.get("user_settings", {}):
            db["user_settings"][uid_str]["daily_pts_earned"] = "0"
            db["user_settings"][uid_str]["daily_impact"]     = "0"
            save_db()

    except Exception as e:
        logger.warning("⚠️ send_daily_report uid=%d: %s", uid, e)


async def send_weekly_report(uid: int, uid_str: str, lang: str) -> None:
    """Genera y envía el reporte semanal personal a un usuario."""
    try:
        rep      = db["reputation"].get(uid_str, {})
        settings = db.get("user_settings", {}).get(uid_str, {})
        pts      = rep.get("points", 0)
        rank_info= get_rank_info(pts, uid)

        # Stats semanales
        contribs_week = int(settings.get("weekly_contribs", 0))
        pts_week      = int(settings.get("weekly_pts_earned", 0))
        impact_week   = int(settings.get("weekly_impact", 0))

        if contribs_week == 0 and pts_week == 0:
            return

        # Posición en el ranking
        all_pts   = sorted(
            [(u, d.get("points", 0)) for u, d in db["reputation"].items()],
            key=lambda x: -x[1]
        )
        position  = next((i+1 for i, (u,_) in enumerate(all_pts) if u == uid_str), "?")
        total_users = len(all_pts)

        # Progreso al siguiente rango
        next_pts = rank_info.get("next_pts")
        if next_pts:
            needed = next_pts - pts
            pct    = min(100, int((pts - rank_info["min_pts"]) /
                         max(1, next_pts - rank_info["min_pts"]) * 100))
            progress_bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
            progress_str = {
                "es": f"\n📊 Progreso al siguiente rango:\n{progress_bar} {pct}% ({needed} pts restantes)",
                "en": f"\n📊 Progress to next rank:\n{progress_bar} {pct}% ({needed} pts remaining)",
                "zh": f"\n📊 升级进度：\n{progress_bar} {pct}% (还需{needed}分)",
                "zht":f"\n📊 升級進度：\n{progress_bar} {pct}% (還需{needed}分)",
            }.get(lang, "")
        else:
            progress_str = {
                "es": "\n🔮 Rango máximo — Oráculo eterno.",
                "en": "\n🔮 Maximum rank — Eternal Oracle.",
                "zh": "\n🔮 最高等级 — 永恒神谕。",
                "zht":"\n🔮 最高等級 — 永恆神諭。",
            }.get(lang, "")

        msgs = {
            "es": (
                f"📈 *Reporte Semanal — Synergix*\n\n"
                f"🏅 Rango actual: {rank_info['name']}\n"
                f"📈 Puntos totales: {pts:,}\n"
                f"🏆 Posición global: #{position} de {total_users}\n\n"
                f"━━━ Esta semana ━━━\n"
                f"📦 Aportes realizados: {contribs_week}\n"
                f"💎 Puntos ganados: +{pts_week}\n"
                f"🔁 Impacto (usos de tus aportes): {impact_week}\n"
                f"🌐 Tus aportes viven para siempre en BNB Greenfield.\n"
                f"{progress_str}"
            ),
            "en": (
                f"📈 *Weekly Report — Synergix*\n\n"
                f"🏅 Current rank: {rank_info['name']}\n"
                f"📈 Total points: {pts:,}\n"
                f"🏆 Global position: #{position} of {total_users}\n\n"
                f"━━━ This week ━━━\n"
                f"📦 Contributions made: {contribs_week}\n"
                f"💎 Points earned: +{pts_week}\n"
                f"🔁 Impact (times your contributions were used): {impact_week}\n"
                f"🌐 Your contributions live forever on BNB Greenfield.\n"
                f"{progress_str}"
            ),
            "zh": (
                f"📈 *每周报告 — Synergix*\n\n"
                f"🏅 当前等级：{rank_info['name']}\n"
                f"📈 总积分：{pts:,}\n"
                f"🏆 全球排名：第{position}/{total_users}名\n\n"
                f"━━━ 本周 ━━━\n"
                f"📦 贡献次数：{contribs_week}\n"
                f"💎 获得积分：+{pts_week}\n"
                f"🔁 影响力（贡献被使用次数）：{impact_week}\n"
                f"🌐 你的贡献永远保存在BNB Greenfield上。\n"
                f"{progress_str}"
            ),
            "zht": (
                f"📈 *每週報告 — Synergix*\n\n"
                f"🏅 當前等級：{rank_info['name']}\n"
                f"📈 總積分：{pts:,}\n"
                f"🏆 全球排名：第{position}/{total_users}名\n\n"
                f"━━━ 本週 ━━━\n"
                f"📦 貢獻次數：{contribs_week}\n"
                f"💎 獲得積分：+{pts_week}\n"
                f"🔁 影響力（貢獻被使用次數）：{impact_week}\n"
                f"🌐 你的貢獻永遠保存在BNB Greenfield上。\n"
                f"{progress_str}"
            ),
        }

        await bot.send_message(uid, msgs.get(lang, msgs["en"]), parse_mode="Markdown")
        logger.info("📈 Reporte semanal enviado uid=%d", uid)

        # Reset contadores semanales
        if uid_str in db.get("user_settings", {}):
            db["user_settings"][uid_str]["weekly_contribs"]    = "0"
            db["user_settings"][uid_str]["weekly_pts_earned"]  = "0"
            db["user_settings"][uid_str]["weekly_impact"]      = "0"
            save_db()

    except Exception as e:
        logger.warning("⚠️ send_weekly_report uid=%d: %s", uid, e)


async def daily_report_loop() -> None:
    """
    Loop que envía reportes diarios a las 00:00 UTC.
    Semanal (lunes) a las 00:05 UTC.
    """
    from datetime import timezone
    logger.info("📊 Daily report loop iniciado")
    while True:
        try:
            now_utc = datetime.now(timezone.utc)
            # Calcular segundos hasta próxima medianoche UTC
            tomorrow = (now_utc + timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0)
            secs = (tomorrow - now_utc).total_seconds()
            await asyncio.sleep(max(60, secs))

            now_utc  = datetime.now(timezone.utc)
            is_monday = now_utc.weekday() == 0  # 0 = lunes

            logger.info("📊 Enviando reportes %s...",
                        "diarios + semanales" if is_monday else "diarios")

            # Obtener usuarios activos (que han aportado en las últimas 24h)
            sent_daily  = 0
            sent_weekly = 0
            for uid_str, settings in db.get("user_settings", {}).items():
                try:
                    if not uid_str.isdigit():
                        continue
                    uid  = int(uid_str)
                    lang = settings.get("lang",
                           db.get("user_settings", {}).get(uid_str, {}).get("lang", "es"))

                    # Reporte diario — solo si tuvo actividad
                    contribs_today = int(settings.get("daily_count", 0))
                    pts_today      = int(settings.get("daily_pts_earned", 0))
                    impact_today   = int(settings.get("daily_impact", 0))

                    if contribs_today > 0 or pts_today > 0 or impact_today > 0:
                        await send_daily_report(uid, uid_str, lang)
                        sent_daily += 1
                        await asyncio.sleep(0.1)  # No spamear la API de Telegram

                    # Reporte semanal — solo los lunes
                    if is_monday:
                        weekly_contribs = int(settings.get("weekly_contribs", 0))
                        weekly_pts      = int(settings.get("weekly_pts_earned", 0))
                        if weekly_contribs > 0 or weekly_pts > 0:
                            await send_weekly_report(uid, uid_str, lang)
                            sent_weekly += 1
                            await asyncio.sleep(0.1)

                except Exception as e:
                    logger.warning("⚠️ report uid=%s: %s", uid_str, e)

            logger.info("✅ Reportes enviados: %d diarios, %d semanales",
                        sent_daily, sent_weekly)

        except Exception as e:
            logger.error("❌ daily_report_loop: %s", e)
            await asyncio.sleep(3600)  # Reintentar en 1h si hay error

async def log_flush_loop() -> None:
    """
    OPT GAS: Flush de logs 1 vez al día (medianoche UTC) en lugar de cada 5 min.
    El buffer local nunca pierde eventos — solo se retrasa la subida a GF.
    Ahorra ~440 createObject/mes (de 468 → 30).
    """
    while True:
        from datetime import timezone
        now_utc = datetime.now(timezone.utc)
        # Calcular segundos hasta medianoche UTC
        midnight = (now_utc + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0)
        secs_to_midnight = (midnight - now_utc).total_seconds()
        await asyncio.sleep(max(3600, secs_to_midnight))  # Mínimo 1h para no spamear
        await flush_logs_to_gf()


async def fusion_brain_loop() -> None:
    """
    Llama a scripts/fusion_brain.py cada 20 minutos (spec: buffering en lote).
    Actualiza SYNERGIXAI/Synergix_ia.txt con todos los aportes acumulados.
    """
    fusion_script = os.path.join(BASE_DIR, "scripts", "fusion_brain.py")
    await asyncio.sleep(120)  # Esperar 2 min al arranque
    while True:
        logger.info("🧠 [FusionBrain] Ejecutando fusion_brain.py...")
        try:
            loop = asyncio.get_running_loop()
            res = await loop.run_in_executor(
                None,
                lambda: subprocess.run(
                    ["python3", fusion_script],
                    capture_output=True, text=True, timeout=300
                )
            )
            if res.returncode == 0:
                logger.info("✅ [FusionBrain] Completado")
                if res.stdout.strip():
                    logger.info("[FusionBrain] Output: %s", res.stdout.strip()[-300:])
            else:
                logger.error("❌ [FusionBrain] Error (código %d):", res.returncode)
                logger.error("   STDOUT: %s", res.stdout.strip()[-300:])
                logger.error("   STDERR: %s", res.stderr.strip()[-300:])
        except Exception as e:
            logger.error("❌ [FusionBrain] Excepción: %s", e)
        await asyncio.sleep(1200)  # 20 minutos


# ═══════════════════════════════════════════════════════════════════════════════
# CHALLENGE SEMANAL AUTOMÁTICO
# ═══════════════════════════════════════════════════════════════════════════════

# Banco de challenges rotativos — se selecciona uno por semana automáticamente
CHALLENGE_BANK = [
    {
        "es": "Mejor estrategia DeFi 2026",
        "en": "Best DeFi Strategy 2026",
        "zh_cn": "2026年最佳DeFi策略",
        "zh":    "2026年最佳DeFi策略",
        "keywords": ["defi","estrategia","strategy","yield","farming","liquidity","staking","amm","dex","protocol"],
    },
    {
        "es": "Innovación en Web3 e identidad descentralizada",
        "en": "Web3 Innovation and Decentralized Identity",
        "zh_cn": "Web3创新与去中心化身份",
        "zh":    "Web3創新與去中心化身份",
        "keywords": ["web3","identity","did","soulbound","nft","wallet","zkproof","decentralized","identidad"],
    },
    {
        "es": "IA colectiva y aprendizaje federado",
        "en": "Collective AI and Federated Learning",
        "zh_cn": "集体人工智能与联邦学习",
        "zh":    "集體人工智能與聯邦學習",
        "keywords": ["ia","ai","federated","learning","llm","model","training","neural","colectiva","aprendizaje"],
    },
    {
        "es": "Tokenomics y diseño de economías descentralizadas",
        "en": "Tokenomics and Decentralized Economy Design",
        "zh_cn": "代币经济学与去中心化经济设计",
        "zh":    "代幣經濟學與去中心化經濟設計",
        "keywords": ["token","tokenomics","economy","dao","governance","voting","treasury","incentive","incentivos"],
    },
    {
        "es": "Seguridad en contratos inteligentes",
        "en": "Smart Contract Security",
        "zh_cn": "智能合约安全",
        "zh":    "智能合約安全",
        "keywords": ["security","audit","smart","contract","vulnerability","hack","exploit","reentrancy","seguridad"],
    },
    {
        "es": "Almacenamiento descentralizado y datos inmutables",
        "en": "Decentralized Storage and Immutable Data",
        "zh_cn": "去中心化存储与不可变数据",
        "zh":    "去中心化儲存與不可變數據",
        "keywords": ["storage","ipfs","greenfield","filecoin","arweave","immutable","decentralized","datos","almacenamiento"],
    },
    {
        "es": "Cross-chain y la interoperabilidad del futuro",
        "en": "Cross-chain and the Future of Interoperability",
        "zh_cn": "跨链与未来的互操作性",
        "zh":    "跨鏈與未來的互操作性",
        "keywords": ["crosschain","bridge","interoperability","cosmos","polkadot","layerzero","relay","interoperabilidad"],
    },
    {
        "es": "RWA: activos del mundo real en blockchain",
        "en": "RWA: Real World Assets on Blockchain",
        "zh_cn": "RWA：区块链上的现实世界资产",
        "zh":    "RWA：區塊鏈上的現實世界資產",
        "keywords": ["rwa","real","world","asset","tokenization","property","commodity","bond","activos"],
    },
]


def get_current_week_number() -> int:
    """Número de semana ISO del año actual."""
    return datetime.now().isocalendar()[1]


def get_active_challenge() -> dict:
    """
    Retorna el challenge activo de esta semana.
    Prioridad:
      1. Challenge generado por IA (si existe para esta semana)
      2. Override manual desde DB
      3. Banco rotativo por semana ISO
    """
    week = get_current_week_number()

    # 1. Challenge generado por IA para esta semana
    ai_challenge = db.get("global_stats", {}).get("ai_challenge_current")
    ai_week      = db.get("global_stats", {}).get("ai_challenge_week", 0)
    if ai_challenge and isinstance(ai_challenge, dict) and ai_week == week:
        return ai_challenge

    # 2. Override manual
    override = db.get("global_stats", {}).get("challenge_override")
    if override and isinstance(override, dict):
        return override

    # 3. Banco rotativo
    idx = week % len(CHALLENGE_BANK)
    return CHALLENGE_BANK[idx]


async def generate_ai_challenge() -> dict:
    """
    Usa Groq para generar un nuevo challenge basado en:
    - Los aportes más recientes de la comunidad
    - Los temas más impactantes de la semana anterior
    - Tendencias detectadas en el conocimiento colectivo
    """
    wisdom   = db["global_stats"].get("collective_wisdom", "")
    # Resumir los últimos aportes por tema
    recent_summaries = [
        e.get("summary", "")
        for u in db.get("memory", {}).values()
        for e in u[:3]
        if e.get("summary")
    ][:20]

    context = "\n".join(f"- {s}" for s in recent_summaries) if recent_summaries else "Sin aportes recientes."

    system_prompt = (
        "Eres el generador de challenges de Synergix, una plataforma de inteligencia colectiva descentralizada. "
        "Basándote en los aportes recientes de la comunidad, genera un challenge semanal que:\n"
        "1. Sea relevante para Web3, DeFi, IA, blockchain o tecnología descentralizada\n"
        "2. Esté conectado con los temas que la comunidad ya está explorando\n"
        "3. Sea inspirador y motive contribuciones de calidad\n"
        "4. Tenga entre 5 y 10 palabras\n\n"
        "Responde ÚNICAMENTE con un JSON válido, sin texto extra:\n"
        '{"es": "Tema en español", "en": "Topic in English", "zh_cn": "简体中文主题", "zh": "繁體中文主題", '
        '"keywords": ["kw1", "kw2", "kw3", "kw4", "kw5"]}'
    )

    user_msg = f"Aportes recientes de la comunidad:\n{context}\n\nSabiduría colectiva actual:\n{wisdom[:300]}"

    try:
        raw = await groq_call(
            [{"role": "system", "content": system_prompt},
             {"role": "user",   "content": user_msg}],
            model=MODEL_FAST,
            temperature=0.8,
        )
        raw = raw.strip().replace("```json", "").replace("```", "").strip()
        challenge = json.loads(raw)

        # Validar estructura mínima
        if not all(k in challenge for k in ["es", "en", "keywords"]):
            raise ValueError("JSON incompleto")

        logger.info("🤖 [Challenge AI] Generado: %s", challenge.get("es", ""))
        return challenge

    except Exception as e:
        logger.warning("⚠️ [Challenge AI] Error generando, usando banco: %s", e)
        # Fallback al banco rotativo
        week = get_current_week_number()
        return CHALLENGE_BANK[week % len(CHALLENGE_BANK)]


def get_challenge_text(lang: str = "es") -> str:
    """Retorna el texto del challenge en el idioma dado."""
    ch = get_active_challenge()
    return ch.get(lang, ch.get("es", "Challenge semanal activo"))


def get_challenge_keywords() -> list:
    """Retorna las keywords del challenge activo."""
    return get_active_challenge().get("keywords", [])


def is_challenge_related(text: str) -> bool:
    """Verifica si un texto está relacionado con el challenge activo."""
    kws = get_challenge_keywords()
    low = text.lower()
    return any(kw in low for kw in kws)


def get_week_start_end() -> tuple:
    """Retorna (lunes, domingo) de la semana actual como strings."""
    today = datetime.now()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    return monday.strftime("%Y-%m-%d"), sunday.strftime("%Y-%m-%d")


async def get_top_challenge_aportes(top_n: int = 3) -> list[dict]:
    """
    Busca los top N aportes de la semana actual relacionados con el challenge,
    ordenados por score + impact.
    Consulta la DB local (aportes guardados esta semana).
    """
    week_start, week_end = get_week_start_end()
    challenge = get_active_challenge()
    kws       = challenge.get("keywords", [])

    candidates = []
    for uid_str, entries in db.get("memory", {}).items():
        for entry in entries:
            # Filtrar por keywords del challenge en el summary
            summary = entry.get("summary", "").lower()
            if not any(kw in summary for kw in kws):
                continue
            # Verificar que es de esta semana (aproximado por ts)
            ts = entry.get("ts", 0)
            if ts:
                entry_date = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
                if not (week_start <= entry_date <= week_end):
                    continue
            candidates.append({
                "uid":     uid_str,
                "cid":     entry.get("cid", "N/A"),
                "summary": entry.get("summary", ""),
                "score":   entry.get("score", 5),
                "quality": entry.get("quality", "standard"),
                "ts":      ts,
            })

    # Ordenar por score desc, luego por ts desc (más reciente)
    candidates.sort(key=lambda x: (-x["score"], -x["ts"]))
    return candidates[:top_n]


async def announce_weekly_winners(top_aportes: list[dict]) -> None:
    """
    Broadcast a todos los usuarios activos con el top 3 del challenge semanal.
    """
    if not top_aportes:
        logger.info("📭 [Challenge] Sin ganadores esta semana")
        return

    challenge = get_active_challenge()
    medals    = ["🥇", "🥈", "🥉"]

    for lang, uids_lang in _get_users_by_lang().items():
        ch_text = challenge.get(lang, challenge["es"])

        lines = []
        for i, a in enumerate(top_aportes):
            medal = medals[i] if i < len(medals) else "⭐"
            uid_name = db.get("user_settings", {}).get(a["uid"], {}).get("name", f"Usuario {a['uid']}")
            lines.append(
                f"{medal} {uid_name}\n"
                f"   📝 {a['summary'][:100]}\n"
                f"   ⭐ Score: {a['score']}/10 | 🔗 CID: {a['cid'][:20]}..."
            )

        sep = "=============================="
        winners_block = "\n\n".join(lines)
        broadcast_msgs = {
            "es": f"🏆 RESULTADOS DEL CHALLENGE SEMANAL\n\n📌 Tema: {ch_text}\n\n{sep}\n\n{winners_block}\n\n🔥 ¡Felicitaciones a los guardianes del conocimiento! La red es más fuerte gracias a vosotros.\n\n📈 La próxima semana comienza un nuevo desafío. ¡Prepárate para inmortalizar tu sabiduría!",
            "en": f"🏆 WEEKLY CHALLENGE RESULTS\n\n📌 Topic: {ch_text}\n\n{sep}\n\n{winners_block}\n\n🔥 Congratulations to the knowledge guardians! The network is stronger thanks to you.\n\n📈 Next week starts a new challenge. Get ready to immortalize your wisdom!",
            "zh_cn": f"🏆 每周挑战结果\n\n📌 主题: {ch_text}\n\n{sep}\n\n{winners_block}\n\n🔥 恭喜知识守护者！感谢你们，网络更强大了。\n\n📈 下周开始新挑战，准备好让你的智慧永存！",
            "zh":    f"🏆 每週挑戰結果\n\n📌 主題: {ch_text}\n\n{sep}\n\n{winners_block}\n\n🔥 恭喜知識守護者！感謝你們，網路更強大了。\n\n📈 下週開始新挑戰，準備好讓你的智慧永存！",
        }

        msg = broadcast_msgs.get(lang, broadcast_msgs["es"])
        sent = 0
        for uid in uids_lang:
            try:
                await bot.send_message(int(uid), msg)
                sent += 1
                await asyncio.sleep(0.1)  # Rate limit Telegram
            except Exception as e:
                logger.debug("Broadcast skip uid=%s: %s", uid, e)

        logger.info("📢 [Challenge] Broadcast %s → %d usuarios", lang, sent)


def _get_users_by_lang() -> dict:
    """Agrupa UIDs activos por idioma para el broadcast."""
    by_lang = {"es": [], "en": [], "zh_cn": [], "zh": []}
    for uid_str in db.get("reputation", {}):
        lang = db.get("user_settings", {}).get(uid_str, {}).get("lang", "es")
        if lang in by_lang:
            by_lang[lang].append(uid_str)
    return by_lang


async def weekly_challenge_loop() -> None:
    """
    Loop que se ejecuta cada hora y verifica si es fin de semana (domingo 23:00 UTC).
    Cuando llega ese momento:
      1. Obtiene top 3 aportes del challenge
      2. Broadcast a todos los usuarios
      3. Registra ganadores en DB
      4. El lunes arranca automáticamente el siguiente challenge del banco
    """
    await asyncio.sleep(30)  # Esperar arranque del bot
    logger.info("🏆 [Challenge] Loop semanal iniciado")

    last_broadcast_week = db.get("global_stats", {}).get("last_challenge_week", 0)

    while True:
        try:
            now      = datetime.now(timezone.utc)
            week_num = now.isocalendar()[1]
            # Domingo = weekday 6, a partir de las 23:00 UTC
            is_broadcast_time = (now.weekday() == 6 and now.hour == 23)

            if is_broadcast_time and week_num != last_broadcast_week:
                logger.info("🏆 [Challenge] Iniciando cierre de semana %d...", week_num)

                # 1. Top 3 aportes
                top_aportes = await get_top_challenge_aportes(top_n=3)
                logger.info("🏆 [Challenge] Top %d aportes encontrados", len(top_aportes))

                # 2. Broadcast
                await announce_weekly_winners(top_aportes)

                # 3. Guardar ganadores en DB + Greenfield
                db["global_stats"]["last_challenge_week"] = week_num
                db["global_stats"]["last_challenge_winners"] = top_aportes
                db["global_stats"]["last_challenge_topic"] = get_active_challenge()
                save_db()

                # 4. Subir resultado del challenge a Greenfield logs
                challenge = get_active_challenge()
                winners_content = (
                    f"=== CHALLENGE SEMANAL SEMANA {week_num} ===\n"
                    f"Tema: {challenge.get('es', '')}\n"
                    f"Top aportes: {len(top_aportes)}\n\n" +
                    "\n".join(f"{i+1}. uid={a['uid']} score={a['score']} cid={a['cid']}"
                               for i, a in enumerate(top_aportes))
                )
                log_event("challenge_weekly_closed",
                          0,
                          f"week={week_num} winners={len(top_aportes)}",
                          "info")

                last_broadcast_week = week_num
                logger.info("✅ [Challenge] Semana %d cerrada. Próximo challenge: semana %d",
                            week_num, week_num + 1)

                # Generar el challenge de la próxima semana con IA
                logger.info("🤖 [Challenge AI] Generando challenge para semana %d...", week_num + 1)
                next_challenge = await generate_ai_challenge()
                db["global_stats"]["ai_challenge_current"] = next_challenge
                db["global_stats"]["ai_challenge_week"]    = week_num + 1
                save_db()
                logger.info("🤖 [Challenge AI] Próximo challenge: %s", next_challenge.get("es",""))

                # Anunciar el nuevo challenge
                await asyncio.sleep(60)  # Pequeña pausa
                for lang_c, uids_c in _get_users_by_lang().items():
                    next_txt = next_challenge.get(lang_c, next_challenge.get("es",""))
                    new_ch_msgs = {
                        "es": f"🚀 ¡Comienza una nueva semana en Synergix!\n\n🏆 Nuevo Challenge Semanal (generado por IA):\n{next_txt}\n\nContribuye sobre este tema para ganar puntos extra y aparecer en el Top 3 del domingo. 🔥",
                        "en": f"🚀 A new week begins in Synergix!\n\n🏆 New Weekly Challenge (AI-generated):\n{next_txt}\n\nContribute on this topic to earn extra points and appear in Sunday's Top 3. 🔥",
                        "zh_cn": f"🚀 Synergix新的一周开始！\n\n🏆 新每周挑战（AI生成）：\n{next_txt}\n\n就此主题贡献以获得额外积分。🔥",
                        "zh":    f"🚀 Synergix新的一週開始！\n\n🏆 新每週挑戰（AI生成）：\n{next_txt}\n\n就此主題貢獻以獲得額外積分。🔥",
                    }
                    announce_msg = new_ch_msgs.get(lang_c, new_ch_msgs["en"])
                    for uid_c in uids_c[:50]:  # Máx 50 por idioma para no spamear
                        try:
                            await bot.send_message(int(uid_c), announce_msg)
                            await asyncio.sleep(0.1)
                        except Exception:
                            pass

                # Esperar hasta el lunes para no re-ejecutar
                await asyncio.sleep(3600)

            else:
                # Verificar cada 30 minutos
                await asyncio.sleep(1800)

        except Exception as e:
            logger.error("❌ [Challenge] Error en loop: %s", e)
            await asyncio.sleep(3600)

# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

async def main() -> None:
    logger.info("🚀 Synergix Bot iniciando en producción (Hetzner)...")

    # ── Restaurar DB desde Greenfield (fuente de verdad) ─────────────────────
    db_restored = await restore_db_from_gf()
    if db_restored:
        logger.info("✅ DB restaurada desde GF — Greenfield es el disco duro")

    _load_session_from_db()
    logger.info("♻️  Sesiones restauradas: %d usuarios conocidos", len(welcomed_users))
    log_event("bot_start", 0, f"Synergix iniciado, {len(welcomed_users)} usuarios restaurados", "info")

    # Cargar aportes de Greenfield al cache RAG (si la DB local está vacía)
    await _bootstrap_rag_from_gf()
    _build_rag_cache_from_db()
    logger.info("🔍 RAG listo: %d aportes en cache", len(_rag_cache))

    # Leer cerebro SYNERGIXAI/ al arrancar
    brain_startup = await read_brain_from_gf()
    if brain_startup:
        logger.info("🧠 Cerebro cargado al arrancar: %d chars", len(brain_startup))
    else:
        logger.warning("⚠️ Cerebro vacío al arrancar — se generará en el primer ciclo")



    # Verificar Ollama + calentar modelo (primera inferencia lenta sin warmup)
    h = await ollama_health_check()
    if h.get("model_ready"):
        logger.info("✅ Qwen 1.5B listo — calentando modelo...")
        await ollama_warmup()
    else:
        logger.warning("⚠️ Qwen 1.5B no disponible. Ejecuta: ollama pull qwen2.5:1.5b")

    tasks = [
        asyncio.create_task(contrib_worker()),
        asyncio.create_task(federation_loop()),
        asyncio.create_task(log_flush_loop()),
        asyncio.create_task(fusion_brain_loop()),
        asyncio.create_task(weekly_challenge_loop()),
        asyncio.create_task(daily_report_loop()),
    ]
    try:
        await dp.start_polling(bot)
    finally:
        for task in tasks:
            task.cancel()

if __name__ == "__main__":
    asyncio.run(main())
