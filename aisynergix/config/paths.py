# aisynergix/config/paths.py
import os
from datetime import datetime

# --- GREENFIELD SOBERANO ---
GF_BUCKET = "synergixai"
GF_ROOT   = "aisynergix"

class GF:
    BRAIN_DIR   = f"{GF_ROOT}/SYNERGIXAI"
    USERS_DIR   = f"{GF_ROOT}/users"
    APORTES_DIR = f"{GF_ROOT}/aportes"
    AI_DIR      = f"{GF_ROOT}/ai/Qwen2.5-0.5B"
    DISCOVERY   = f"{GF_ROOT}/discovery"
    LOGS_DIR    = f"{GF_ROOT}/logs"
    BACKUPS_DIR = f"{GF_ROOT}/backups"
    DB_DIR      = f"{GF_ROOT}/data"
    
    BRAIN_FILE  = f"{BRAIN_DIR}/Synergix_ia.txt"

    @staticmethod
    def user(uid: str) -> str:
        return f"{GF.USERS_DIR}/{uid}.json"

    @staticmethod
    def aporte(month: str, uid: str, ts: int) -> str:
        return f"{GF.APORTES_DIR}/{month}/{uid}_{ts}.txt"

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

# --- LOCAL REPOSITORY STRUCTURE ---
_HERE = os.path.dirname(os.path.abspath(__file__))
AISYNERGIX  = os.path.abspath(os.path.join(_HERE, ".."))
REPO_ROOT   = os.path.abspath(os.path.join(AISYNERGIX, ".."))

DATA_LOCAL  = os.path.join(AISYNERGIX, "data")
SYNERGIXAI  = os.path.join(AISYNERGIX, "SYNERGIXAI")
LOGS_LOCAL  = os.path.join(AISYNERGIX, "logs")

# Archivos Críticos
DB_FILE     = os.path.join(DATA_LOCAL, "synergix_db.json")
UPLOAD_JS   = os.path.join(AISYNERGIX, "backend", "upload.js")
HOT_CACHE   = os.path.join(DATA_LOCAL, "hot_cache.json")

# Asegurar directorios
for d in [DATA_LOCAL, SYNERGIXAI, LOGS_LOCAL]:
    os.makedirs(d, exist_ok=True)
