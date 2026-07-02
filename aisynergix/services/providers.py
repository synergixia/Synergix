"""providers.py — registro de proveedores de servicios (diseño §6.2, uso 2).

Profesionales VERIFICADOS (wallet propia verificada por firma → human_verified)
se registran dentro de su nodo con una categoría y una descripción corta.
Cualquier miembro puede consultarlos; la reputación es el propio historial
on-chain del proveedor (rango, aportes, impactos).
"""

import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Categorías de servicio (clave interna → icono).  Etiqueta legible vía
# locales con la clave ``prov_<key>``.
PROVIDER_CATEGORIES: Dict[str, str] = {
    "electricidad": "⚡",
    "plomeria":     "🔧",
    "mecanica":     "🔩",
    "construccion": "🧱",
    "salud":        "🩺",
    "educacion":    "📖",
    "tecnologia":   "💻",
    "otros":        "🧰",
}

MAX_DESC_LEN = 200
MIN_DESC_LEN = 10


def _hash_uid(uid: int) -> str:
    from aisynergix.bot.identity import _hash_uid as h
    return h(uid)


async def register_provider(
    uid: int, node_id: str, category: str, description: str
) -> Optional[str]:
    """Registra al usuario como proveedor de su nodo.

    Requisitos: wallet verificada (human_verified) y ser miembro del nodo.
    Retorna la clave de error ("not_verified" | "not_member" | "bad_input")
    o None si todo fue bien.
    """
    from aisynergix.bot.identity import get_identity_manager
    from aisynergix.services.irys import write_provider, get_node_member

    if category not in PROVIDER_CATEGORIES:
        return "bad_input"
    desc = (description or "").strip()
    if not (MIN_DESC_LEN <= len(desc) <= MAX_DESC_LEN):
        return "bad_input"

    profile = await get_identity_manager().get_profile(uid)
    if not profile.human_verified:
        return "not_verified"

    uid_hash = _hash_uid(uid)
    member = await get_node_member(node_id, uid_hash)
    if not member or member.get("member-status", "active") != "active":
        return "not_member"

    await write_provider(node_id, uid_hash, category, desc, status="active")
    return None


async def deregister_provider(uid: int, node_id: str) -> None:
    """Da de baja el registro del usuario (última versión gana)."""
    from aisynergix.services.irys import write_provider
    await write_provider(node_id, _hash_uid(uid), "otros", "", status="inactive")


async def get_providers(node_id: str) -> List[Dict[str, str]]:
    """Proveedores activos del nodo, con su categoría y descripción."""
    from aisynergix.services.irys import list_node_providers
    return await list_node_providers(node_id)


__all__ = [
    "PROVIDER_CATEGORIES",
    "MAX_DESC_LEN",
    "MIN_DESC_LEN",
    "register_provider",
    "deregister_provider",
    "get_providers",
]
