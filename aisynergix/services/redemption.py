"""redemption.py — gate anti-Sybil del canje SYNX → SYNERGIX real (Fase B).

Esta fase construye TODA la lógica de elegibilidad pero NO paga ni un token:
``REDEEM_ENABLED`` viene apagado.  La Fase C lo enciende y añade el pago real
desde el tesoro sobre este mismo gate.

Defensas (todas deben pasar para poder canjear):
  1. Wallet propia VERIFICADA por firma (ecrecover) — el pago solo va a una
     dirección cuya propiedad se demostró.  Una wallet por humano.
  2. Reputación mínima — historial real de aportes validados (no cuentas recién
     creadas que farmean y huyen).
  3. Monto mínimo y ≤ saldo SYNX.
  4. Tope por-usuario en una ventana móvil (limita el ritmo de cash-out).
  5. Tope por-DIRECCIÓN de pago + máximo de Ghost IDs distintos por dirección
     — el corazón de la defensa Sybil: N cuentas falsas → una wallet → tope.

La decisión de elegibilidad es una función pura (``evaluate``) para poder
testearla sin red.
"""

import hashlib
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _num_env(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.getenv(name, str(default))))
    except (ValueError, TypeError):
        return default


def _int_env(name: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(name, str(default))))
    except (ValueError, TypeError):
        return default


# Interruptor maestro. Fase B lo deja apagado (no se paga nada); Fase C lo
# enciende junto con el tesoro y el presupuesto de emisión.
REDEEM_ENABLED: bool = os.getenv("SYNERGIX_REDEEM_ENABLED", "false").strip().lower() in (
    "1", "true", "yes", "on",
)

# Fase C — tasa de conversión y presupuesto de emisión (la red de seguridad
# principal con hot wallet: acota el peor caso de pérdida por día).
REDEEM_RATE = _num_env("SYNERGIX_REDEEM_RATE", 1.0)          # SYNERGIX por SYNX
REDEEM_DAILY_BUDGET = _num_env("SYNERGIX_REDEEM_DAILY_BUDGET", 1000.0)  # soft-launch pequeño

REDEEM_MIN_CONTRIBUTIONS = _int_env("SYNERGIX_REDEEM_MIN_CONTRIBUTIONS", 5)
REDEEM_MIN_AMOUNT = _num_env("SYNERGIX_REDEEM_MIN_AMOUNT", 100.0)
REDEEM_USER_WINDOW_CAP = _num_env("SYNERGIX_REDEEM_USER_CAP", 5000.0)
REDEEM_ADDRESS_WINDOW_CAP = _num_env("SYNERGIX_REDEEM_ADDRESS_CAP", 5000.0)
REDEEM_ADDRESS_MAX_USERS = _int_env("SYNERGIX_REDEEM_ADDRESS_MAX_USERS", 1)
REDEEM_WINDOW_HOURS = _int_env("SYNERGIX_REDEEM_WINDOW_HOURS", 24)
REDEEM_WINDOW_SECONDS = REDEEM_WINDOW_HOURS * 3600

# Estados que cuentan para los topes (una solicitud pendiente ya reserva cupo,
# para que no se pueda spamear solicitudes por encima del límite).
_COUNTED_STATES = ("requested", "paid")


# ── Lógica pura (testeable sin red) ──────────────────────────────────────

def evaluate(
    amount: float,
    *,
    verified: bool,
    contribution_count: int,
    synx_balance: float,
    user_window_used: float,
    address_window_used: float,
    address_user_count: int,
    is_new_address_user: bool,
) -> Tuple[bool, str, float]:
    """Decide si un canje es elegible. Retorna (ok, motivo, max_permitido).

    Motivos: not_verified | low_reputation | below_min | insufficient_balance |
    user_cap | address_cap | address_shared | over_limit | ok.
    """
    from aisynergix.services.rewards import is_valid_amount
    if not verified:
        return (False, "not_verified", 0.0)
    if contribution_count < REDEEM_MIN_CONTRIBUTIONS:
        return (False, "low_reputation", 0.0)
    if not is_valid_amount(amount) or amount < REDEEM_MIN_AMOUNT:
        return (False, "below_min", 0.0)
    if amount > synx_balance:
        return (False, "insufficient_balance", 0.0)

    user_remaining = REDEEM_USER_WINDOW_CAP - user_window_used
    if user_remaining <= 0:
        return (False, "user_cap", 0.0)
    addr_remaining = REDEEM_ADDRESS_WINDOW_CAP - address_window_used
    if addr_remaining <= 0:
        return (False, "address_cap", 0.0)

    effective_users = address_user_count + (1 if is_new_address_user else 0)
    if effective_users > REDEEM_ADDRESS_MAX_USERS:
        return (False, "address_shared", 0.0)

    max_allowed = round(min(synx_balance, user_remaining, addr_remaining), 4)
    if amount > max_allowed:
        return (False, "over_limit", max_allowed)
    return (True, "ok", max_allowed)


def _window_used(records: List[Dict[str, str]], now: int) -> float:
    """Suma los montos canjeados dentro de la ventana móvil (estados contados)."""
    cutoff = now - REDEEM_WINDOW_SECONDS
    total = 0.0
    for r in records:
        if r.get("redeem-status") not in _COUNTED_STATES:
            continue
        try:
            ts = int(r.get("ts", 0) or 0)
            amount = float(r.get("amount", 0) or 0)
        except (ValueError, TypeError):
            continue
        if ts >= cutoff:
            total += amount
    return round(total, 4)


def _distinct_address_users(records: List[Dict[str, str]]) -> set:
    """Ghost IDs distintos que han canjeado (o solicitado) hacia una dirección."""
    return {
        r.get("uid-hash", "") for r in records
        if r.get("uid-hash") and r.get("redeem-status") in _COUNTED_STATES
    }


def synergix_for(synx_amount: float) -> float:
    """Convierte SYNX canjeado a SYNERGIX real según la tasa."""
    return round(max(0.0, synx_amount) * REDEEM_RATE, 4)


def spent_today(records: List[Dict[str, str]], day_start: int) -> float:
    """SYNERGIX pagado hoy (para el presupuesto de emisión). Función pura."""
    total = 0.0
    for r in records:
        if r.get("redeem-status") != "paid":
            continue
        try:
            ts = int(r.get("ts", 0) or 0)
            syn = float(r.get("synergix", 0) or 0)
        except (ValueError, TypeError):
            continue
        if ts >= day_start:
            total += syn
    return round(total, 4)


def _day_start(now: Optional[int] = None) -> int:
    from datetime import datetime, timezone
    n = datetime.fromtimestamp(now, tz=timezone.utc) if now else datetime.now(timezone.utc)
    return int(n.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())


async def budget_remaining() -> float:
    """SYNERGIX que aún se puede emitir hoy (presupuesto − gastado hoy)."""
    from aisynergix.services.irys import list_all_redemptions
    try:
        recs = await list_all_redemptions()
    except Exception:
        recs = []
    return max(0.0, round(REDEEM_DAILY_BUDGET - spent_today(recs, _day_start()), 4))


# ── E/S (perfil + Irys) ──────────────────────────────────────────────────

def _hash_uid(uid: int) -> str:
    from aisynergix.bot.identity import _hash_uid as h
    return h(uid)


def generate_redemption_id(uid_hash: str, amount: float) -> str:
    raw = f"{uid_hash}:{amount}:{time.time()}"
    return "rdm_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


async def _context(uid: int) -> Optional[Dict[str, Any]]:
    """Reúne todo lo que necesita el gate (perfil + ventanas de Irys)."""
    from aisynergix.bot.identity import get_identity_manager
    from aisynergix.services.irys import list_user_redemptions, list_address_redemptions

    profile = await get_identity_manager().get_profile(uid)
    address = (profile.wallet_address or "").lower()
    verified = bool(profile.human_verified and address)
    uid_hash = _hash_uid(uid)
    now = int(time.time())

    if not verified:
        return {
            "verified": False, "address": "", "uid_hash": uid_hash,
            "contribution_count": profile.contribution_count,
            "synx_balance": profile.synx_balance,
            "user_window_used": 0.0, "address_window_used": 0.0,
            "address_user_count": 0, "is_new_address_user": True, "now": now,
        }

    user_recs = await list_user_redemptions(uid_hash)
    addr_recs = await list_address_redemptions(address)
    addr_users = _distinct_address_users(addr_recs)

    return {
        "verified": True,
        "address": address,
        "uid_hash": uid_hash,
        "contribution_count": profile.contribution_count,
        "synx_balance": profile.synx_balance,
        "user_window_used": _window_used(user_recs, now),
        "address_window_used": _window_used(addr_recs, now),
        "address_user_count": len(addr_users - {uid_hash}),
        "is_new_address_user": uid_hash not in addr_users,
        "now": now,
    }


async def can_redeem(uid: int, amount: float) -> Tuple[bool, str, float]:
    """Evalúa un canje concreto. Pago SIEMPRE a la wallet verificada del usuario."""
    ctx = await _context(uid)
    if ctx is None:
        return (False, "not_verified", 0.0)
    return evaluate(
        amount,
        verified=ctx["verified"],
        contribution_count=ctx["contribution_count"],
        synx_balance=ctx["synx_balance"],
        user_window_used=ctx["user_window_used"],
        address_window_used=ctx["address_window_used"],
        address_user_count=ctx["address_user_count"],
        is_new_address_user=ctx["is_new_address_user"],
    )


async def redeemable_now(uid: int) -> Dict[str, Any]:
    """Estado de canje del usuario para mostrar en el bot (sin pagar nada)."""
    ctx = await _context(uid)
    if ctx is None or not ctx["verified"]:
        return {"eligible": False, "reason": "not_verified", "max": 0.0,
                "synx_balance": ctx["synx_balance"] if ctx else 0.0, "address": ""}
    # Máximo canjeable ahora mismo: probamos con el mínimo para ver si pasa.
    ok, reason, _ = evaluate(
        REDEEM_MIN_AMOUNT,
        verified=True,
        contribution_count=ctx["contribution_count"],
        synx_balance=ctx["synx_balance"],
        user_window_used=ctx["user_window_used"],
        address_window_used=ctx["address_window_used"],
        address_user_count=ctx["address_user_count"],
        is_new_address_user=ctx["is_new_address_user"],
    )
    max_allowed = round(min(
        ctx["synx_balance"],
        REDEEM_USER_WINDOW_CAP - ctx["user_window_used"],
        REDEEM_ADDRESS_WINDOW_CAP - ctx["address_window_used"],
    ), 4) if ok else 0.0
    return {
        "eligible": ok, "reason": reason, "max": max(0.0, max_allowed),
        "synx_balance": ctx["synx_balance"], "address": ctx["address"],
        "enabled": REDEEM_ENABLED,
    }


_pay_locks: Dict[str, Any] = {}


def _lock_for(uid_hash: str):
    import asyncio
    lock = _pay_locks.get(uid_hash)
    if lock is None:
        lock = asyncio.Lock()
        _pay_locks[uid_hash] = lock
    return lock


async def request_redemption(uid: int, amount: float) -> Dict[str, Any]:
    """Procesa un canje: gate → presupuesto → debita SYNX → paga SYNERGIX real.

    Orden a prueba de doble-gasto: se debita el SYNX ANTES de transferir (el
    usuario no puede volver a gastarlo); si la transferencia falla, se
    reembolsa.  Cada paso se sella en Irys (requested → paid|rejected) para
    auditoría e idempotencia.  Si el proceso muere entre la transferencia y el
    sellado 'paid', el registro queda 'requested' y NO se reintenta
    automáticamente (evita doble pago): el operador reconcilia a mano — por eso
    el presupuesto de soft-launch debe ser pequeño.
    """
    if not REDEEM_ENABLED:
        return {"ok": False, "reason": "disabled"}

    # Freeze de emergencia (reutiliza el emergency-lock del admin).
    from aisynergix.services.irys import is_emergency_locked, write_redemption
    if is_emergency_locked():
        return {"ok": False, "reason": "frozen"}

    ok, reason, _ = await can_redeem(uid, amount)
    if not ok:
        return {"ok": False, "reason": reason}

    synergix_amount = synergix_for(amount)
    if synergix_amount <= 0:
        return {"ok": False, "reason": "below_min"}

    # Presupuesto de emisión diario (acota el peor caso con hot wallet).
    if synergix_amount > await budget_remaining():
        return {"ok": False, "reason": "budget"}

    from aisynergix.services.treasury import treasury_enabled, pay_synergix
    if not treasury_enabled():
        return {"ok": False, "reason": "disabled"}

    from aisynergix.bot.identity import get_identity_manager
    identity = get_identity_manager()
    profile = await identity.get_profile(uid)
    uid_hash = _hash_uid(uid)
    address = (profile.wallet_address or "").lower()
    rid = generate_redemption_id(uid_hash, amount)

    async with _lock_for(uid_hash):
        # 1) Debitar el SYNX (autoritativo, atómico) — punto de no retorno.
        debited = await identity.debit_synx(uid_hash, amount)
        if debited is None:
            return {"ok": False, "reason": "insufficient_balance"}

        # 2) Anclar 'requested' (idempotencia/auditoría) antes de pagar.
        try:
            await write_redemption(rid, uid_hash, address, amount,
                                   status="requested", synergix=synergix_amount)
        except Exception as exc:
            logger.error("canje %s: sellado requested falló, reembolso: %s", rid, exc)
            await identity.credit_synx(uid_hash, debited)
            return {"ok": False, "reason": "error"}

        # 3) Pagar SYNERGIX real desde el tesoro → wallet verificada.
        result = await pay_synergix(address, synergix_amount)
        if not result.get("ok"):
            logger.warning("canje %s: pago falló (%s) — reembolso SYNX",
                           rid, result.get("error"))
            await identity.credit_synx(uid_hash, debited)
            try:
                await write_redemption(rid, uid_hash, address, amount,
                                       status="rejected", synergix=synergix_amount)
            except Exception:
                pass
            return {"ok": False, "reason": "pay_" + str(result.get("error", "broadcast"))}

        # 4) Sellar 'paid' con el hash on-chain.
        tx = result["tx"]
        await write_redemption(rid, uid_hash, address, amount,
                               status="paid", tx=tx, synergix=synergix_amount)
        logger.info("✅ Canje %s pagado: %.2f SYNX → %.2f SYNERGIX → %s tx=%s",
                    rid, amount, synergix_amount, address, tx)
        return {"ok": True, "redemption_id": rid, "amount": amount,
                "synergix": synergix_amount, "address": address, "tx": tx}


__all__ = [
    "REDEEM_ENABLED",
    "REDEEM_MIN_AMOUNT",
    "REDEEM_MIN_CONTRIBUTIONS",
    "REDEEM_USER_WINDOW_CAP",
    "REDEEM_ADDRESS_WINDOW_CAP",
    "REDEEM_ADDRESS_MAX_USERS",
    "REDEEM_RATE",
    "REDEEM_DAILY_BUDGET",
    "evaluate",
    "synergix_for",
    "spent_today",
    "budget_remaining",
    "can_redeem",
    "redeemable_now",
    "request_redemption",
    "generate_redemption_id",
]
