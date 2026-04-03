"""
aisynergix/config/paths.py
═══════════════════════════════════════════════════════════════════════════════
Mapeo universal de rutas — Local (Hetzner) y DCellar (BNB Greenfield).

Bucket principal: synergixai
Carpeta raíz:     aisynergix/
═══════════════════════════════════════════════════════════════════════════════
"""
import os

_HERE     = os.path.dirname(os.path.abspath(__file__))
PROJECT   = os.path.dirname(os.path.dirname(_HERE))

LOCAL_ROOT      = os.environ.get("LOCAL_ROOT", os.path.join(PROJECT, "aisynergix"))
LOCAL_BRAIN_DIR = os.path.join(LOCAL_ROOT, "SYNERGIXAI")
LOCAL_DATA_DIR  = os.path.join(LOCAL_ROOT, "data")
LOCAL_LOGS_DIR  = os.path.join(LOCAL_ROOT, "logs")
LOCAL_MODEL_DIR = os.environ.get("OLLAMA_MODELS", os.path.expanduser("~/.ollama/models"))

DB_FILE    = os.path.join(LOCAL_DATA_DIR, "synergix_db.json")
BACKEND_DIR = os.path.join(LOCAL_ROOT, "..", "backend")
UPLOAD_JS   = os.path.join(PROJECT, "aisynergix", "backend", "upload.js")

# ══════════════════════════════════════════════════════════════════════════════
# RUTAS DCellar — Bucket: synergixai / Raíz: aisynergix/
# ══════════════════════════════════════════════════════════════════════════════

GF_BUCKET = os.environ.get("GF_BUCKET", "synergixai")   # ← nombre real del bucket
GF_ROOT   = "aisynergix"                                  # ← carpeta raíz soberana

class GF:
    """Rutas on-chain en bucket synergixai/aisynergix/"""

    BRAIN_DIR   = f"{GF_ROOT}/SYNERGIXAI"
    BRAIN_FILE  = f"{GF_ROOT}/SYNERGIXAI/Synergix_ia.txt"
    USERS_DIR   = f"{GF_ROOT}/users"
    APORTES_DIR = f"{GF_ROOT}/aportes"
    AI_DIR      = f"{GF_ROOT}/ai/Qwen2.5-1.5B"
    DISCOVERY   = f"{GF_ROOT}/discovery"
    LOGS_DIR    = f"{GF_ROOT}/logs"
    BACKUPS_DIR = f"{GF_ROOT}/backups"
    DB_DIR      = f"{GF_ROOT}/data"

    @staticmethod
    def user(uid_hash: str) -> str:
        return f"{GF.USERS_DIR}/{uid_hash}.json"

    @staticmethod
    def aporte(month: str, uid_hash: str, ts: int) -> str:
        return f"{GF.APORTES_DIR}/{month}/{uid_hash}_{ts}.txt"

    @staticmethod
    def brain_versioned(timestamp: str) -> str:
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
        return f"{GF.DISCOVERY}/{source}/{date}.json"


def ensure_local_dirs():
    for d in [LOCAL_BRAIN_DIR, LOCAL_DATA_DIR, LOCAL_LOGS_DIR]:
        os.makedirs(d, exist_ok=True)
