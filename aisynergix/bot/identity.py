"""
identity.py — Hidratador y Sistema de Identidad de Synergix.
Maneja la ofuscación criptográfica (XOR + Base36), el caché LRU en RAM (TTL 10m) 
y la hidratación/deshidratación de usuarios hacia BNB Greenfield (Stateless).
"""

import logging
from typing import Dict, Optional
from cachetools import TTLCache

from aisynergix.config.constants import SECRET_MASK, USERS_PREFIX, get_rank_for_points
from aisynergix.services.greenfield import get_user_metadata, put_object

logger = logging.getLogger(__name__)

# Caché LRU en RAM: retiene hasta 5000 usuarios activos por 10 minutos para evitar latencia RPC
_user_cache = TTLCache(maxsize=5000, ttl=600)

# ─────────────────────────────────────────────────────────────────────────────
# CRIPTOGRAFÍA Y OFUSCACIÓN (Privacidad PbD)
# ─────────────────────────────────────────────────────────────────────────────
BASE36_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"

def _encode_base36(num: int) -> str:
    """Convierte un entero a cadena Base36."""
    if num == 0:
        return "0"
    base36 = ""
    while num != 0:
        num, i = divmod(num, 36)
        base36 = BASE36_ALPHABET[i] + base36
    return base36

def _decode_base36(s: str) -> int:
    """Decodifica una cadena Base36 a entero."""
    return int(s, 36)

def mask_uid(raw_uid: int) -> str:
    """Ofusca el UID de Telegram usando XOR de 64 bits y Base36."""
    masked_int = raw_uid ^ SECRET_MASK
    return _encode_base36(masked_int)

def unmask_uid(masked_uid_str: str) -> int:
    """Desofusca el string Base36 y revierte el XOR para obtener el UID real de Telegram."""
    masked_int = _decode_base36(masked_uid_str)
    return masked_int ^ SECRET_MASK

# ─────────────────────────────────────────────────────────────────────────────
# ESTRUCTURA DE USUARIO
# ─────────────────────────────────────────────────────────────────────────────
class UserContext:
    """Contexto de usuario en RAM."""
    def __init__(self, masked_uid: str, tags: Dict[str, str]):
        self.masked_uid = masked_uid
        self.points = int(tags.get("points", 0))
        self.rank = tags.get("rank", "Iniciado")
        self.welcomed = tags.get("welcomed", "false").lower() == "true"
        self.language = tags.get("language", "auto")
        self.first_name = tags.get("first_name", "Usuario")

    def to_tags(self) -> Dict[str, str]:
        return {
            "points": str(self.points),
            "rank": self.rank,
            "welcomed": str(self.welcomed).lower(),
            "language": self.language,
            "first_name": self.first_name
        }

# ─────────────────────────────────────────────────────────────────────────────
# HIDRATACIÓN Y DESHIDRATACIÓN
# ─────────────────────────────────────────────────────────────────────────────
async def hydrate_user(raw_uid: int, first_name: str = "Usuario") -> UserContext:
    """
    Recupera el contexto del usuario. Prioriza el Caché LRU.
    Si no está en caché, consulta a Greenfield (HEAD). Si es 404, crea archivo 0-bytes.
    """
    masked_uid = mask_uid(raw_uid)
    
    # 1. Leer de Caché LRU
    if masked_uid in _user_cache:
        ctx = _user_cache[masked_uid]
        if ctx.first_name != first_name:
            ctx.first_name = first_name # Actualizamos nombre en RAM pasivamente
        return ctx

    # 2. Leer de Greenfield
    tags = await get_user_metadata(masked_uid)
    
    if tags is None:
        # Idempotencia: Crear usuario nuevo 0-bytes
        logger.info(f"[Identity] Nuevo usuario detectado. Creando identidad ofuscada: {masked_uid}")
        default_tags = {
            "points": "0",
            "rank": "Iniciado",
            "welcomed": "false",
            "language": "auto",
            "first_name": first_name
        }
        await put_object(f"{USERS_PREFIX}/{masked_uid}", b"", tags=default_tags)
        ctx = UserContext(masked_uid, default_tags)
    else:
        ctx = UserContext(masked_uid, tags)

    # 3. Actualizar nombre si cambió
    if ctx.first_name != first_name:
        ctx.first_name = first_name

    # Guardar en caché LRU
    _user_cache[masked_uid] = ctx
    return ctx

async def dehydrate_user(ctx: UserContext):
    """
    Sincroniza el contexto de RAM a Greenfield y actualiza el caché local.
    Aplica cálculo dinámico del rango.
    """
    ctx.rank = get_rank_for_points(ctx.points)
    _user_cache[ctx.masked_uid] = ctx  # Actualizar caché local
    
    # Subir metadatos actualizados (archivo de 0-bytes)
    tags = ctx.to_tags()
    success = await put_object(f"{USERS_PREFIX}/{ctx.masked_uid}", b"", tags=tags)
    
    if not success:
        logger.error(f"[Identity] Fallo al deshidratar usuario {ctx.masked_uid} a Greenfield.")
