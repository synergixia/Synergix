import os
from dotenv import load_dotenv

load_dotenv()

# Jerarquía de 6 Niveles Synergix
RANK_TABLE = [
    {"name": "🌱 Iniciado",     "min_pts": 0,      "limit": 5,    "multiplier": 1.0, "benefit": "Acceso base al Nodo"},
    {"name": "📈 Activo",       "min_pts": 100,    "limit": 12,   "multiplier": 1.1, "benefit": "Prioridad de flujo nivel 1"},
    {"name": "🧬 Sincronizado", "min_pts": 500,    "limit": 25,   "multiplier": 1.5, "benefit": "Sincronización RAG extendida"},
    {"name": "🏗️ Arquitecto",   "min_pts": 1500,   "limit": 40,   "multiplier": 2.5, "benefit": "Capacidad de diseño de memoria"},
    {"name": "🧠 Mente Colmena","min_pts": 5000,   "limit": 60,   "multiplier": 3.0, "benefit": "Acceso a capas profundas de IA"},
    {"name": "🔮 Oráculo",      "min_pts": 15000,  "limit": 9999, "multiplier": 5.0, "benefit": "Acceso Ilimitado e Inmortal"}
]

# Configuración de Red Soberana Greenfield
SP_URL = os.getenv("SP_URL", "https://greenfield-chain.bnbchain.org")
BUCKET_NAME = os.getenv("BUCKET_NAME", "synergixai")
SALT = os.getenv("SALT", "default_salt_synergix")

# Administradores con Bypass de cuota
MASTER_UIDS_ENV = os.getenv("MASTER_UIDS", "")
MASTER_UIDS = set(map(int, MASTER_UIDS_ENV.split(","))) if MASTER_UIDS_ENV else set()
