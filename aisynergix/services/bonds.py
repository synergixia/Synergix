"""bonds.py — bonds de nodo en SYNERGIX real (Fase A del token real).

Crear un nodo exige bloquear un BOND de SYNERGIX real en la wallet custodial
del creador (skin in the game anti-Sybil: N nodos = N bonds inmovilizados).
El token NO se mueve de la wallet del usuario: se marca como bloqueado con un
DataItem ``node-bond`` en Irys, y retiro/venta descuentan el bond del saldo
disponible (``disponible = saldo on-chain − bloqueado``).

Estados del bond:
  * locked     — bond activo (nodo en operación).
  * unbonding  — el creador salió; temporizador de UNBOND_DAYS corriendo.
  * released   — liberado (los tokens vuelven a estar disponibles).
  * slashed    — penalizado por abuso (forfeit; se mantiene bloqueado hasta
                 que exista el tesoro para transferirlo — Fase C).

La liberación es PEREZOSA: un bond ``unbonding`` cuya ventana ya expiró se
considera libre sin necesidad de un cron (no cuenta como bloqueado).
"""

import logging
import os
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _int_env(name: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(name, str(default))))
    except (ValueError, TypeError):
        return default


# BOND por nodo (SYNERGIX real) y ventana de unbonding.  Configurables.
BOND_AMOUNT: float = float(_int_env("SYNERGIX_NODE_BOND", 200_000))
UNBOND_DAYS: int = _int_env("SYNERGIX_UNBOND_DAYS", 7)
UNBOND_SECONDS: int = UNBOND_DAYS * 86_400

# Estados que mantienen el token bloqueado (no disponible para retiro/venta).
_ACTIVE_STATES = ("locked", "slashed")


def _hash_uid(uid: int) -> str:
    from aisynergix.bot.identity import _hash_uid as h
    return h(uid)


# ── Lógica pura (testeable sin red) ──────────────────────────────────────

def is_bond_active(status: str, unbond_until: int, now: Optional[int] = None) -> bool:
    """True si el bond mantiene el token bloqueado en este instante.

    locked/slashed → siempre bloqueado.  unbonding → bloqueado hasta que la
    ventana expira (now < unbond_until).  released → libre.
    """
    now = now if now is not None else int(time.time())
    if status in _ACTIVE_STATES:
        return True
    if status == "unbonding":
        return now < unbond_until
    return False


def sum_locked(bonds: List[Dict[str, str]], now: Optional[int] = None) -> float:
    """Suma los montos de los bonds que siguen bloqueados (función pura)."""
    now = now if now is not None else int(time.time())
    total = 0.0
    for b in bonds:
        status = b.get("bond-status", "locked")
        try:
            until = int(b.get("unbond-until", 0) or 0)
            amount = float(b.get("amount", 0) or 0)
        except (ValueError, TypeError):
            continue
        if is_bond_active(status, until, now):
            total += amount
    return round(total, 4)


# ── E/S (Irys + custodia) ────────────────────────────────────────────────

async def locked_synergix(uid: int) -> float:
    """Total de SYNERGIX bloqueado por bonds activos del usuario."""
    from aisynergix.services.irys import list_user_bonds
    try:
        bonds = await list_user_bonds(_hash_uid(uid))
    except Exception:
        bonds = []
    return sum_locked(bonds)


async def available_synergix(uid: int, onchain_balance: float) -> float:
    """SYNERGIX disponible = saldo on-chain − bonds bloqueados. Nunca negativo."""
    return max(0.0, round((onchain_balance or 0.0) - await locked_synergix(uid), 4))


async def can_afford_bond(uid: int) -> bool:
    """True si el usuario tiene BOND_AMOUNT de SYNERGIX real DISPONIBLE."""
    if BOND_AMOUNT <= 0:
        return True
    from aisynergix.services.wallet import ensure_custodial_wallet
    from aisynergix.services.trading import get_token_balance
    address = await ensure_custodial_wallet(uid)
    if not address:
        return False
    balance = await get_token_balance(address) or 0.0
    return await available_synergix(uid, balance) >= BOND_AMOUNT


async def lock_node_bond(uid: int, node_id: str) -> bool:
    """Bloquea el BOND del creador para un nodo. Re-verifica el saldo disponible."""
    from aisynergix.services.irys import write_node_bond
    if BOND_AMOUNT <= 0:
        return True
    if not await can_afford_bond(uid):
        return False
    try:
        await write_node_bond(node_id, _hash_uid(uid), BOND_AMOUNT, status="locked")
        return True
    except Exception as exc:
        logger.error("lock_node_bond %s falló: %s", node_id, exc)
        return False


async def begin_unbond(uid: int, node_id: str) -> Optional[int]:
    """Inicia el unbonding del bond de un nodo. Retorna el ts de liberación o None.

    Solo el dueño del bond puede iniciarlo, y solo si está en 'locked'.
    """
    from aisynergix.services.irys import get_node_bond, write_node_bond
    uid_hash = _hash_uid(uid)
    bond = await get_node_bond(node_id)
    if not bond or bond.get("uid-hash") != uid_hash:
        return None
    if bond.get("bond-status") != "locked":
        return None
    try:
        amount = float(bond.get("amount", BOND_AMOUNT) or BOND_AMOUNT)
    except (ValueError, TypeError):
        amount = BOND_AMOUNT
    unbond_until = int(time.time()) + UNBOND_SECONDS
    await write_node_bond(node_id, uid_hash, amount, status="unbonding",
                          unbond_until=unbond_until)
    return unbond_until


async def user_bonds(uid: int) -> List[Dict[str, str]]:
    from aisynergix.services.irys import list_user_bonds
    return await list_user_bonds(_hash_uid(uid))


async def slash_bond(node_id: str) -> bool:
    """Penaliza (forfeit) el bond de un nodo — llamado por gobernanza/jurado.

    Fase A: marca 'slashed' (se mantiene bloqueado). La transferencia al
    tesoro se implementa cuando exista el tesoro (Fase C).
    """
    from aisynergix.services.irys import get_node_bond, write_node_bond
    bond = await get_node_bond(node_id)
    if not bond:
        return False
    try:
        amount = float(bond.get("amount", BOND_AMOUNT) or BOND_AMOUNT)
    except (ValueError, TypeError):
        amount = BOND_AMOUNT
    await write_node_bond(node_id, bond.get("uid-hash", ""), amount, status="slashed")
    logger.warning("⚔️ Bond del nodo %s SLASHED (%.0f SYNERGIX).", node_id, amount)
    return True


__all__ = [
    "BOND_AMOUNT",
    "UNBOND_DAYS",
    "UNBOND_SECONDS",
    "is_bond_active",
    "sum_locked",
    "locked_synergix",
    "available_synergix",
    "can_afford_bond",
    "lock_node_bond",
    "begin_unbond",
    "user_bonds",
    "slash_bond",
]
