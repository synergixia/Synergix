"""tiers.py — Membresía por tenencia de SYNERGIX real (utilidad Capa 1).

Tener SYNERGIX real en la wallet custodial (la que el bot controla, 1:1 con
el usuario → anti-Sybil por diseño) desbloquea beneficios dentro del bot. Es
utilidad de *tenencia*: no se gasta ni se quema el token, solo se demuestra
que se posee, lo que crea demanda de compra sin prometer rendimiento.

El saldo se lee on-chain y se cachea en RAM (la tenencia no cambia segundo a
segundo). La lógica de umbrales es pura y testeable.
"""

import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Escalera de tenencia (SYNERGIX real) → beneficios.  Ordenada de mayor a
# menor; ``daily_bonus`` son aportes/día extra sobre el límite del rango.
TIERS: List[Dict[str, Any]] = [
    {"key": "diamante", "name": "💎 Diamante", "min": 200_000.0, "daily_bonus": 30, "priority": True},
    {"key": "oro",      "name": "🥇 Oro",      "min": 50_000.0,  "daily_bonus": 15, "priority": True},
    {"key": "plata",    "name": "🥈 Plata",    "min": 10_000.0,  "daily_bonus": 7,  "priority": False},
    {"key": "bronce",   "name": "🥉 Bronce",   "min": 1_000.0,   "daily_bonus": 3,  "priority": False},
]

BASE_TIER: Dict[str, Any] = {
    "key": "base", "name": "", "min": 0.0, "daily_bonus": 0, "priority": False,
}

_CACHE_TTL = 300  # 5 min
# uid -> (balance, cached_at)
_cache: Dict[int, Any] = {}


def tier_for(balance: float) -> Dict[str, Any]:
    """Devuelve la config del tier para un saldo de SYNERGIX real (función pura)."""
    try:
        bal = float(balance)
    except (ValueError, TypeError):
        return BASE_TIER
    if bal != bal or bal in (float("inf"), float("-inf")):  # NaN / inf
        return BASE_TIER
    for tier in TIERS:  # de mayor a menor
        if bal >= tier["min"]:
            return tier
    return BASE_TIER


def daily_bonus_for(balance: float) -> int:
    """Aportes/día extra que otorga la tenencia (0 si no llega a Bronce)."""
    return int(tier_for(balance)["daily_bonus"])


def next_tier(balance: float) -> Optional[Dict[str, Any]]:
    """El siguiente tier por alcanzar, o None si ya está en el máximo."""
    current = tier_for(balance)
    higher = [t for t in TIERS if t["min"] > current["min"]]
    return higher[-1] if higher else None  # el más cercano por encima


# ── Lectura de tenencia on-chain (con caché) ─────────────────────────────

async def synergix_balance(uid: int, force: bool = False) -> float:
    """Saldo de SYNERGIX real en la wallet custodial del usuario (cacheado).

    Solo la custodial (no la wallet externa verificada) para evitar que una
    misma wallet grande verificada en varias cuentas otorgue el tier a todas.
    Mover tokens a la custodial para subir de tier es, además, demanda real.
    """
    entry = _cache.get(uid)
    if not force and entry and time.time() - entry[1] < _CACHE_TTL:
        return entry[0]
    total = 0.0
    try:
        from aisynergix.services import wallet as wallet_svc
        from aisynergix.services import trading as trading_svc
        if wallet_svc.wallet_enabled():
            addr = await wallet_svc.ensure_custodial_wallet(uid)
            if addr:
                bal = await trading_svc.get_token_balance(addr)
                total = float(bal or 0.0)
    except Exception as exc:
        logger.warning("synergix_balance uid=%s falló: %s", uid, exc)
        # Ante fallo de red no penalizar: si había caché, devolverla.
        return entry[0] if entry else 0.0
    total = round(total, 2)
    _cache[uid] = (total, time.time())
    if len(_cache) > 5000:
        _cache.pop(next(iter(_cache)), None)
    return total


def invalidate(uid: int) -> None:
    """Fuerza recálculo del saldo en la próxima consulta (p.ej. tras un swap)."""
    _cache.pop(uid, None)


async def holder_daily_bonus(uid: int) -> int:
    """Bono de aportes/día por tenencia (0 si no alcanza ningún tier)."""
    return daily_bonus_for(await synergix_balance(uid))


async def membership(uid: int) -> Dict[str, Any]:
    """Estado de membresía del usuario: {balance, tier, next, to_next}."""
    bal = await synergix_balance(uid)
    tier = tier_for(bal)
    nxt = next_tier(bal)
    return {
        "balance": bal,
        "tier": tier,
        "next": nxt,
        "to_next": round(nxt["min"] - bal, 2) if nxt else 0.0,
    }


__all__ = [
    "TIERS",
    "BASE_TIER",
    "tier_for",
    "daily_bonus_for",
    "next_tier",
    "synergix_balance",
    "invalidate",
    "holder_daily_bonus",
    "membership",
]
