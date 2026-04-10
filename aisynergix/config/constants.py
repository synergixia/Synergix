import os
from dotenv import load_dotenv

load_dotenv()

# Gamificación y Límites
RANK_TABLE = [
    {"name": "🌱 Iniciado",     "min_pts": 0,      "benefit": "Acceso base (5 msgs/día)", "limit": 5, "multiplier": 1.0},
    {"name": "⚙️ Colaborador",  "min_pts": 500,    "benefit": "Voto en Fusión (12 msgs/día)", "limit": 12, "multiplier": 1.1},
    {"name": "🛠️ Arquitecto",   "min_pts": 1500,   "benefit": "Prioridad IA (25 msgs/día)", "limit": 25, "multiplier": 1.25},
    {"name": "🔮 Oráculo",      "min_pts": 5000,   "benefit": "Acceso Ilimitado & Admin", "limit": 999, "multiplier": 1.5}
]

# Entorno Web3 DCellar
SP_URL = os.getenv("SP_URL", "https://greenfield-chain.bnbchain.org")
BUCKET_NAME = os.getenv("BUCKET_NAME", "synergixai")

# Seguridad: Semilla para el Ghost Protocol
SALT = os.getenv("SALT", "synergix_ghost_protocol_v1_super_secret")

# Privilegios de Administrador
MASTER_UIDS_ENV = os.getenv("MASTER_UIDS", "")
MASTER_UIDS = set(map(int, MASTER_UIDS_ENV.split(","))) if MASTER_UIDS_ENV else set()
