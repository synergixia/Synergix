"""
aisynergix/config/paths.py
═══════════════════════════════════════════════════════════════════════════════
Mapeo universal de rutas — Local (Hetzner) y DCellar (BNB Greenfield).

Bucket principal: synergixai
Carpeta raíz:     aisynergix/
═══════════════════════════════════════════════════════════════════════════════
"""
import os

# --- CONFIGURACIÓN MAESTRA DEL BUCKET ---
GF_BUCKET = "synergixai"  # Nombre oficial del bucket en Greenfield
GF_ROOT   = "aisynergix"   # Carpeta raíz soberana

class GF:
    """Mapeo exacto de la estructura en DCellar (Greenfield)"""
    
    # Rutas Base
    BRAIN_DIR   = f"{GF_ROOT}/SYNERGIXAI"
    USERS_DIR   = f"{GF_ROOT}/users"
    APORTES_DIR = f"{GF_ROOT}/aportes"
    AI_DIR      = f"{GF_ROOT}/ai/Qwen2.5-1.5B"
    DISCOVERY   = f"{GF_ROOT}/discovery"
    LOGS_DIR    = f"{GF_ROOT}/logs"
    BACKUPS_DIR = f"{GF_ROOT}/backups"
    DB_DIR      = f"{GF_ROOT}/data"
    
    # Archivo Maestro
    BRAIN_FILE  = f"{BRAIN_DIR}/Synergix_ia.txt"

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

# --- CONFIGURACIÓN LOCAL (SERVIDOR HETZNER) ---
_HERE     = os.path.dirname(os.path.abspath(__file__))
# Subimos dos niveles para llegar a la raíz del proyecto (donde está aisynergix/)
PROJECT   = os.path.abspath(os.path.join(_HERE, "..", ".."))

LOCAL_ROOT      = os.path.join(PROJECT, "aisynergix")
LOCAL_BRAIN_DIR = os.path.join(LOCAL_ROOT, "SYNERGIXAI")
LOCAL_DATA_DIR  = os.path.join(LOCAL_ROOT, "data")
LOCAL_LOGS_DIR  = os.path.join(LOCAL_ROOT, "logs")
LOCAL_BRAIN     = os.path.join(LOCAL_BRAIN_DIR, "Synergix_ia.txt")

# Archivos de Sistema
DB_FILE    = os.path.join(LOCAL_DATA_DIR, "synergix_db.json")
UPLOAD_JS  = os.path.join(LOCAL_ROOT, "backend", "upload.js")

def ensure_local_dirs():
    """Crea la estructura de carpetas local si no existe"""
    for d in [LOCAL_BRAIN_DIR, LOCAL_DATA_DIR, LOCAL_LOGS_DIR]:
        os.makedirs(d, exist_ok=True)
