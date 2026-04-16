"""
constants.py — ADN de Synergix.
Define URLs de Storage Providers, umbrales de rango, rutas de DCellar, 
límites de configuración y la máscara secreta para ofuscación de identidad.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ─────────────────────────────────────────────
# IDENTIDAD Y OFUSCACIÓN (Privacidad PbD)
# ─────────────────────────────────────────────
# Máscara XOR de 64 bits para ofuscar UIDs de Telegram
SECRET_MASK: int = int(os.getenv("SECRET_MASK", "0x5A9A7B8C9D0E1F2A"), 16)

# ─────────────────────────────────────────────
# UMBRALES DE RANGO (Gamificación)
# ─────────────────────────────────────────────
RANKS = {
    0: "Iniciado",
    100: "Activo",
    500: "Sincronizado",
    1500: "Arquitecto",
    5000: "Mente Colmena",
    15000: "Oráculo"
}

def get_rank_for_points(points: int) -> str:
    """Devuelve el rango correspondiente según los puntos actuales."""
    current_rank = "Iniciado"
    for threshold, rank_name in sorted(RANKS.items()):
        if points >= threshold:
            current_rank = rank_name
        else:
            break
    return current_rank

# ─────────────────────────────────────────────
# BNB GREENFIELD — STORAGE PROVIDER ENDPOINTS
# ─────────────────────────────────────────────
GREENFIELD_SP_ENDPOINT: str = os.getenv("GREENFIELD_SP_ENDPOINT", "https://gnfd-testnet-sp1.bnbchain.org")
GREENFIELD_CHAIN_RPC: str = os.getenv("GREENFIELD_CHAIN_RPC", "https://gnfd-testnet-fullnode-tendermint-us.bnbchain.org")
GREENFIELD_BUCKET: str = os.getenv("GREENFIELD_BUCKET", "synergixai")
OPERATOR_ADDRESS: str = os.getenv("OPERATOR_ADDRESS", "")
OPERATOR_PRIVATE_KEY: str = os.getenv("OPERATOR_PRIVATE_KEY", "")

# ─────────────────────────────────────────────
# DCELLER — RUTAS DE ALMACENAMIENTO (Stateless)
# ─────────────────────────────────────────────
USERS_PREFIX: str = "aisynergix/users"
APORTES_PREFIX: str = "aisynergix/aportes"
BRAIN_PREFIX: str = "aisynergix/data/brains"
TOP10_OBJECT: str = "aisynergix/data/top10.json"
BRAIN_POINTER_OBJECT: str = "aisynergix/data/brain_pointer"

# ─────────────────────────────────────────────
# INFRAESTRUCTURA LOCAL (Contenedores Docker)
# ─────────────────────────────────────────────
IA_JUEZ_URL: str = os.getenv("IA_JUEZ_URL", "http://synergix-ia-juez:8080")
IA_PENSADOR_URL: str = os.getenv("IA_PENSADOR_URL", "http://synergix-ia-pensador:8081")
IA_TIMEOUT_SECONDS: float = float(os.getenv("IA_TIMEOUT_SECONDS", "120.0"))

# ─────────────────────────────────────────────
# MOTOR RAG
# ─────────────────────────────────────────────
RAG_MIN_QUALITY_SCORE: float = float(os.getenv("RAG_MIN_QUALITY_SCORE", "7.0"))
RAG_TOP_K: int = int(os.getenv("RAG_TOP_K", "5"))
EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
LOCAL_BRAIN_DIR: str = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "brains")
TOP10_LOCAL_PATH: str = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "top10.json")

# ─────────────────────────────────────────────
# SCHEDULER
# ─────────────────────────────────────────────
FUSION_INTERVAL_MINUTES: int = int(os.getenv("FUSION_INTERVAL_MINUTES", "10"))
DAILY_NOTIFICATION_TIME: str = os.getenv("DAILY_NOTIFICATION_TIME", "23:59")
