"""
aisynergix/config/paths.py
═══════════════════════════════════════════════════════════════════════════════
Mapeo universal de rutas — Local (Hetzner) y DCellar (BNB Greenfield).

Principio soberano: toda ruta del proyecto pasa por este módulo.
Nunca hardcodear rutas en otros archivos.
═══════════════════════════════════════════════════════════════════════════════
"""
import os

# ── Raíz del proyecto (directorio donde está este archivo) ───────────────────
_HERE     = os.path.dirname(os.path.abspath(__file__))
PROJECT   = os.path.dirname(os.path.dirname(_HERE))   # raíz del repo

# ── Directorio de trabajo local en Hetzner ───────────────────────────────────
LOCAL_ROOT      = os.environ.get("LOCAL_ROOT", os.path.join(PROJECT, "aisynergix"))
LOCAL_BRAIN_DIR = os.path.join(LOCAL_ROOT, "SYNERGIXAI")
LOCAL_DATA_DIR  = os.path.join(LOCAL_ROOT, "data")
LOCAL_LOGS_DIR  = os.path.join(LOCAL_ROOT, "logs")
LOCAL_MODEL_DIR = os.environ.get("OLLAMA_MODELS", os.path.expanduser("~/.ollama/models"))

# DB local (JSON)
DB_FILE   = os.path.join(LOCAL_DATA_DIR, "synergix_db.json")

# ── Backend (upload.js para Greenfield SDK) ───────────────────────────────────
BACKEND_DIR = os.path.join(LOCAL_ROOT, "..", "backend")
UPLOAD_JS   = os.path.join(BACKEND_DIR, "upload.js")

# ══════════════════════════════════════════════════════════════════════════════
# RUTAS DCellar / BNB Greenfield
# Bucket principal: synergix
# Carpeta raíz única: aisynergix/
# ══════════════════════════════════════════════════════════════════════════════

GF_ROOT = "aisynergix"  # Prefijo raíz en el bucket synergix

class GF:
    """Rutas on-chain en el bucket synergix/aisynergix/"""

    # Cerebro fusionado
    BRAIN_DIR  = f"{GF_ROOT}/SYNERGIXAI"
    BRAIN_FILE = f"{GF_ROOT}/SYNERGIXAI/Synergix_ia.txt"

    # Usuarios (archivos JSON con perfil completo)
    USERS_DIR  = f"{GF_ROOT}/users"

    # Aportes de la comunidad (memoria inmortal)
    APORTES_DIR = f"{GF_ROOT}/aportes"

    # Modelo Qwen (backup inmutable en Greenfield)
    AI_MODEL_DIR  = f"{GF_ROOT}/ai/Qwen2.5-1.5B"

    # Discovery (tendencias de redes sociales)
    DISCOVERY_DIR = f"{GF_ROOT}/discovery"

    # Logs de auditoría descentralizada
    LOGS_DIR      = f"{GF_ROOT}/logs"

    # Backups del estado del sistema
    BACKUPS_DIR   = f"{GF_ROOT}/backups"

    # DB completa versionada
    DB_DIR        = f"{GF_ROOT}/data"

    @staticmethod
    def user(uid_hash: str) -> str:
        """Ruta del perfil de un usuario."""
        return f"{GF.USERS_DIR}/{uid_hash}.json"

    @staticmethod
    def aporte(month: str, uid_hash: str, ts: int) -> str:
        """Ruta de un aporte mensual."""
        return f"{GF.APORTES_DIR}/{month}/{uid_hash}_{ts}.txt"

    @staticmethod
    def brain_versioned(timestamp: str) -> str:
        """Cerebro fusionado versionado."""
        return f"{GF.BRAIN_DIR}/Synergix_ia_{timestamp}.txt"

    @staticmethod
    def log(date: str) -> str:
        return f"{GF.LOGS_DIR}/{date}_events.log"

    @staticmethod
    def backup(timestamp: str) -> str:
        return f"{GF.BACKUPS_DIR}/snapshot_{timestamp}.bak"

    @staticmethod
    def db_versioned(timestamp: str) -> str:
        return f"{GF.DB_DIR}/synergix_db_{timestamp}.json"

    @staticmethod
    def discovery(source: str, date: str) -> str:
        return f"{GF.DISCOVERY_DIR}/{source}/{date}.json"


# ── Crear directorios locales al importar ─────────────────────────────────────
def ensure_local_dirs():
    for d in [LOCAL_BRAIN_DIR, LOCAL_DATA_DIR, LOCAL_LOGS_DIR]:
        os.makedirs(d, exist_ok=True)
