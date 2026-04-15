"""
identity.py — Hidratador de identidad de Synergix.
Maneja el contexto del usuario extrayendo metadatos de BNB Greenfield (Stateless).
"""

import logging
from typing import Optional, Dict, Any
from aisynergix.services.greenfield import get_user_metadata, put_object, update_user_metadata
from aisynergix.config.constants import USERS_PREFIX, get_rank_for_points

logger = logging.getLogger(__name__)

class UserContext:
    """Contexto de usuario en RAM (Stateless)."""
    def __init__(self, uid: str, tags: Dict[str, str]):
        self.uid = uid
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

async def hydrate_user(uid: str, first_name: str = "Usuario") -> UserContext:
    """
    Recupera el contexto del usuario desde Greenfield.
    Si no existe (HEAD 404), crea un archivo de 0 bytes con tags por defecto.
    """
    uid_str = str(uid)
    tags = await get_user_metadata(uid_str)
    
    if tags is None:
        # Idempotencia: Crear usuario nuevo
        logger.info(f"[Identity] Nuevo usuario detectado: {uid_str}")
        default_tags = {
            "points": "0",
            "rank": "Iniciado",
            "welcomed": "false",
            "language": "auto",
            "first_name": first_name
        }
        # Crear objeto 0-bytes
        await put_object(f"{USERS_PREFIX}/{uid_str}", b"", tags=default_tags)
        return UserContext(uid_str, default_tags)
    
    # Actualizar nombre si cambió
    if tags.get("first_name") != first_name:
        tags["first_name"] = first_name
        # No bloqueamos el flujo por esto, lo hacemos lazy
    
    return UserContext(uid_str, tags)

async def dehydrate_user(ctx: UserContext):
    """Sincroniza el contexto de RAM a Greenfield."""
    # Recalcular rango antes de guardar
    new_rank = get_rank_for_points(ctx.points)
    if ctx.rank != new_rank:
        logger.info(f"[Identity] Usuario {ctx.uid} subió a {new_rank}")
        ctx.rank = new_rank
        
    await update_user_metadata(ctx.uid, ctx.to_tags())
