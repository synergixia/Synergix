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
    
    BRAIN_FILE  = f"{BRAIN_DIR}/Synergix_ia.txt"

    @staticmethod
    def user_path(uid: str) -> str:
        return f"{GF.USERS_DIR}/{uid}.json"

    @staticmethod
    def aporte_path(uid: str, ts: int) -> str:
        month = datetime.now().strftime("%Y-%m")
        return f"{GF.APORTES_DIR}/{month}/{uid}_{ts}.txt"

# --- LOCAL REPOSITORY STRUCTURE ---
_HERE = os.path.dirname(os.path.abspath(__file__))
# Raíz del repo: synergix/
REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))

AISYNERGIX  = os.path.join(REPO_ROOT, "aisynergix")
DATA_LOCAL  = os.path.join(AISYNERGIX, "data") # Carpeta para persistencia local
SYNERGIXAI  = os.path.join(AISYNERGIX, "SYNERGIXAI")
LOGS_LOCAL  = os.path.join(AISYNERGIX, "logs")

# Archivos Críticos
DB_FILE     = os.path.join(DATA_LOCAL, "synergix_db.json")
UPLOAD_JS   = os.path.join(AISYNERGIX, "backend", "upload.js")
HOT_CACHE   = os.path.join(DATA_LOCAL, "hot_cache.json")

# Asegurar directorios
for d in [DATA_LOCAL, SYNERGIXAI, LOGS_LOCAL]:
    os.makedirs(d, exist_ok=True)
