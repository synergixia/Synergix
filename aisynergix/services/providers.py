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


# Pago SYNX a un proveedor (§6.2, uso 2).
MIN_PAYMENT_SYNX = 1.0


async def pay_provider(
    payer_uid: int, node_id: str, provider_hash: str, amount: float, memo: str = "",
) -> Optional[str]:
    """Transfiere SYNX del pagador al proveedor y lo registra en Irys.

    Débito-antes-crédito con reversión: si el crédito o el sellado fallan, se
    devuelve el SYNX al pagador para no perder saldo. Retorna clave de error
    ("bad_amount" | "self_payment" | "not_provider" | "insufficient") o None.
    """
    from aisynergix.bot.identity import get_identity_manager
    from aisynergix.services.irys import (
        get_node_member, list_node_providers, write_payment,
    )

    amount = round(float(amount or 0), 2)
    if amount < MIN_PAYMENT_SYNX:
        return "bad_amount"

    payer_hash = _hash_uid(payer_uid)
    if payer_hash == provider_hash:
        return "self_payment"

    # El destinatario debe ser un proveedor activo del nodo.
    providers = await list_node_providers(node_id)
    if not any(p.get("uid-hash") == provider_hash
               and p.get("provider-status", "active") == "active"
               for p in providers):
        return "not_provider"

    identity = get_identity_manager()
    debited = await identity.debit_synx(payer_hash, amount)
    if debited is None:
        return "insufficient"

    credited = await identity.credit_synx(provider_hash, debited)
    if credited is None:
        # El crédito falló: devolver el SYNX al pagador (aún no se movió nada).
        await identity.credit_synx(payer_hash, debited)
        return "insufficient"

    # Los saldos ya se movieron y sellaron en Irys (débito y crédito sellan el
    # perfil). El registro de pago es auditoría best-effort: si falla, los
    # saldos siguen siendo correctos, solo se pierde la traza del evento.
    try:
        await write_payment(node_id, payer_hash, provider_hash, debited, memo)
    except Exception as exc:
        logger.error(
            "pay_provider: transferencia OK pero no se selló el registro: %s", exc
        )

    logger.info("💸 %s pagó %.2f SYNX a %s", payer_hash, debited, provider_hash)
    return None


__all__ = [
    "PROVIDER_CATEGORIES",
    "MAX_DESC_LEN",
    "MIN_DESC_LEN",
    "MIN_PAYMENT_SYNX",
    "register_provider",
    "deregister_provider",
    "get_providers",
    "pay_provider",
]
