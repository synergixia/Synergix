"""
constants.py — ADN de Synergix.
Define URLs de Storage Providers, umbrales de rango, rutas de DCellar y límites de Gas.
"""

import os

# ─────────────────────────────────────────────
# BNB GREENFIELD — STORAGE PROVIDER ENDPOINTS
# ─────────────────────────────────────────────

# URL pública del SP principal (API REST de Greenfield)
GREENFIELD_SP_ENDPOINT: str = os.getenv(
    "GREENFIELD_SP_ENDPOINT",
    "https://gnfd-testnet-sp1.bnbchain.org"
)

# URL de la API de cadena (RPC) de Greenfield
GREENFIELD_CHAIN_RPC: str = os.getenv(
    "GREENFIELD_CHAIN_RPC",
    "https://gnfd-testnet-fullnode-tendermint-us.bnbchain.org"
)

# Nombre del bucket principal del proyecto en Greenfield
GREENFIELD_BUCKET: str = os.getenv("GREENFIELD_BUCKET", "synergixai")

# Ruta base para los archivos de usuario (0-bytes idempotentes)
USERS_PREFIX: str = "aisynergix/users"

# Ruta base para los aportes de conocimiento
APORTES_PREFIX: str = "aisynergix/aportes"

# Ruta base para los índices FAISS del cerebro
BRAIN_PREFIX: str = "aisynergix/brain"

# Nombre del objeto puntero que indica el índice activo
BRAIN_POINTER_OBJECT: str = "aisynergix/brain/brain_pointer.txt"

# ─────────────────────────────────────────────
# DCELLER — RUTAS DE ALMACENAMIENTO DE ÍNDICES
# ─────────────────────────────────────────────

# Directorio local donde se almacenan los índices descargados
LOCAL_BRAIN_DIR: str = os.getenv("LOCAL_BRAIN_DIR", "/app/brain")

# Nombre del archivo de índice FAISS local activo
LOCAL_INDEX_FILE: str = "synergix.index"

# Nombre del archivo de metadatos del índice (mapa uid → chunk)
LOCAL_INDEX_META: str = "synergix_meta.json"

# ─────────────────────────────────────────────
# CREDENCIALES WEB3 (cargadas desde entorno)
# ─────────────────────────────────────────────

# Clave privada ECDSA del operador del nodo (hex sin 0x)
OPERATOR_PRIVATE_KEY: str = os.getenv("OPERATOR_PRIVATE_KEY", "")

# Dirección pública del operador (checksum Ethereum-compatible)
OPERATOR_ADDRESS: str = os.getenv("OPERATOR_ADDRESS", "")

# ─────────────────────────────────────────────
# LÍMITES DE GAS (BNB GREENFIELD)
# ─────────────────────────────────────────────

# Gas máximo permitido por transacción de escritura
GAS_LIMIT: int = int(os.getenv("GAS_LIMIT", "1200000"))

# Precio de gas en atto-BNB (1 BNB = 1e18 atto-BNB)
GAS_PRICE: str = os.getenv("GAS_PRICE", "5000000000")  # 5 Gwei

# ─────────────────────────────────────────────
# UMBRALES DE RANGO (Sistema de Puntos Synergix)
# ─────────────────────────────────────────────

# Mapa ordenado: nombre_rango → puntos_mínimos
RANK_THRESHOLDS: dict[str, int] = {
    "Iniciado":      0,
    "Activo":        100,
    "Sincronizado":  500,
    "Arquitecto":    1500,
    "Mente Colmena": 5000,
    "Oráculo":       15000,
}

# Lista ordenada de rangos de menor a mayor (útil para cálculo de ascenso)
RANK_ORDER: list[str] = list(RANK_THRESHOLDS.keys())


def get_rank_for_points(points: int) -> str:
    """
    Devuelve el nombre del rango correspondiente a una cantidad de puntos.

    Itera los rangos de mayor a menor umbral y retorna el primero
    cuyo umbral sea <= points.

    Args:
        points (int): Puntos acumulados del usuario.

    Returns:
        str: Nombre del rango (ej. 'Activo', 'Arquitecto').
    """
    for rank_name in reversed(RANK_ORDER):
        if points >= RANK_THRESHOLDS[rank_name]:
            return rank_name
    return "Iniciado"


# ─────────────────────────────────────────────
# TELEGRAM BOT
# ─────────────────────────────────────────────

TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")

# ─────────────────────────────────────────────
# IA LOCAL — ENDPOINTS INTERNOS DOCKER
# ─────────────────────────────────────────────

# Juez (0.5B) — valida y puntúa aportes
IA_JUEZ_URL: str = os.getenv(
    "IA_JUEZ_URL",
    "http://synergix-ia-juez:8080"
)

# Pensador (1.5B) — genera respuestas expertas
IA_PENSADOR_URL: str = os.getenv(
    "IA_PENSADOR_URL",
    "http://synergix-ia-pensador:8081"
)

# Timeout en segundos para llamadas a las IAs locales
IA_TIMEOUT_SECONDS: int = int(os.getenv("IA_TIMEOUT_SECONDS", "60"))

# ─────────────────────────────────────────────
# RAG ENGINE
# ─────────────────────────────────────────────

# Umbral mínimo de quality_score para que un aporte entre al índice FAISS
RAG_MIN_QUALITY_SCORE: float = float(os.getenv("RAG_MIN_QUALITY_SCORE", "7.0"))

# Número máximo de resultados que devuelve el RAG por consulta
RAG_TOP_K: int = int(os.getenv("RAG_TOP_K", "5"))

# Modelo de embeddings a usar (HuggingFace)
EMBEDDING_MODEL: str = os.getenv(
    "EMBEDDING_MODEL",
    "sentence-transformers/all-MiniLM-L6-v2"
)

# ─────────────────────────────────────────────
# SCHEDULER — INTERVALOS DE TAREAS
# ─────────────────────────────────────────────

# Intervalo de fusión del cerebro (minutos)
FUSION_INTERVAL_MINUTES: int = int(os.getenv("FUSION_INTERVAL_MINUTES", "10"))

# Hora de notificación diaria de puntos residuales (HH:MM UTC)
DAILY_NOTIFICATION_TIME: str = os.getenv("DAILY_NOTIFICATION_TIME", "23:59")

# Día y hora de generación de retos semanales (lunes, 00:00 UTC)
WEEKLY_CHALLENGE_DAY: str = os.getenv("WEEKLY_CHALLENGE_DAY", "mon")
WEEKLY_CHALLENGE_TIME: str = os.getenv("WEEKLY_CHALLENGE_TIME", "00:00")

# ─────────────────────────────────────────────
# FIRMA V4 — CONSTANTES DE CANONICAL REQUEST
# ─────────────────────────────────────────────

# Nombre del servicio para la firma ECDSA V4 de Greenfield
SIGNING_SERVICE: str = "greenfield"

# Región del SP (Greenfield usa siempre "us-east-1" como placeholder)
SIGNING_REGION: str = "us-east-1"

# Algoritmo de firma utilizado
SIGNING_ALGORITHM: str = "GNFD1-ECDSA"
