"""bounties.py — Mercado de Bounties de Conocimiento (Proof-of-Knowledge).

Un patrocinador (miembro verificado; en el futuro también una organización vía
API) financia un pool en SYNX para llenar un vacío de conocimiento concreto de
un nodo (nodo + tema). Cada aporte que:

    1. pertenece a ese nodo y tema,
    2. supera al Juez IA (calidad ≥ umbral) y no fue marcado por el Juez 3,

paga a su autor una RECOMPENSA FIJA desde el pool, hasta agotarlo. Un tope por
usuario evita que una sola persona vacíe el bounty. Al vencer el plazo, el
sobrante se reembolsa al patrocinador.

El escrow es contable (ledger SYNX en Irys), igual que los proyectos: el pool
se debita del patrocinador al crear, y cada pago acredita al autor. El estado
autoritativo vive sellado en Irys; el registro `bounty` es versionado (última
versión gana) y `bounty-claim` es append-only e idempotente por aporte.

La lógica de elegibilidad y contabilidad es pura y testeable sin red.
"""

import asyncio
import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

BOUNTY_MIN_POOL = 50.0        # pool mínimo (SYNX)
REWARD_MIN = 5.0             # recompensa mínima por aporte
DEFAULT_DURATION_DAYS = 30
MAX_DURATION_DAYS = 180
PER_USER_DEFAULT = 3         # aportes pagados como máximo por usuario y bounty
PER_USER_MAX = 20

_locks: Dict[str, asyncio.Lock] = {}


def _lock_for(bounty_id: str) -> asyncio.Lock:
    lock = _locks.get(bounty_id)
    if lock is None:
        lock = asyncio.Lock()
        _locks[bounty_id] = lock
    return lock


def _hash_uid(uid: int) -> str:
    from aisynergix.bot.identity import _hash_uid as h
    return h(uid)


def _identity():
    from aisynergix.bot.identity import get_identity_manager
    return get_identity_manager()


def generate_bounty_id(sponsor_hash: str, node_id: str, topic: str, ts: int) -> str:
    raw = f"{sponsor_hash}:{node_id}:{topic}:{ts}"
    return "bty_" + hashlib.sha256(raw.encode()).hexdigest()[:16]


# ── Lógica pura (testeable sin red) ──────────────────────────────────────

def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (ValueError, TypeError):
        return default


def _i(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except (ValueError, TypeError):
        return default


def is_open(record: Dict[str, str], now: int) -> bool:
    """Un bounty está abierto si su estado es 'open' y no venció el plazo."""
    if record.get("bounty-status", "open") != "open":
        return False
    deadline = _i(record.get("deadline", 0))
    return deadline == 0 or now < deadline


def remaining_pool(record: Dict[str, str]) -> float:
    """SYNX que quedan en el pool = pool − recompensa × pagados."""
    pool = _f(record.get("pool", 0))
    reward = _f(record.get("reward", 0))
    paid = _i(record.get("paid-count", 0))
    return round(max(0.0, pool - reward * paid), 2)


def refund_amount(record: Dict[str, str]) -> float:
    """Sobrante a devolver al patrocinador (lo mismo que queda en el pool)."""
    return remaining_pool(record)


def can_pay(
    record: Dict[str, str], user_paid_count: int, now: int,
) -> Tuple[bool, str]:
    """¿Se puede pagar un aporte más contra este bounty? (ok, motivo).

    Motivos: "closed" | "expired" | "exhausted" | "user_cap".
    """
    if record.get("bounty-status", "open") != "open":
        return False, "closed"
    deadline = _i(record.get("deadline", 0))
    if deadline and now >= deadline:
        return False, "expired"
    reward = _f(record.get("reward", 0))
    if remaining_pool(record) + 1e-9 < reward:
        return False, "exhausted"
    per_user = _i(record.get("per-user", PER_USER_DEFAULT))
    if per_user > 0 and user_paid_count >= per_user:
        return False, "user_cap"
    return True, "ok"


def validate_params(pool: float, reward: float, days: int) -> Optional[str]:
    """Valida los parámetros de creación. Devuelve clave de error o None."""
    if pool < BOUNTY_MIN_POOL:
        return "pool_too_small"
    if reward < REWARD_MIN or reward > pool:
        return "bad_reward"
    if days < 1 or days > MAX_DURATION_DAYS:
        return "bad_duration"
    return None


def max_recipients(record: Dict[str, str]) -> int:
    """Cuántos aportes puede pagar el pool en total (pool ÷ recompensa)."""
    reward = _f(record.get("reward", 0))
    if reward <= 0:
        return 0
    return int(_f(record.get("pool", 0)) // reward)


# ── Orquestación (con red / escrow) ──────────────────────────────────────

async def create_bounty(
    uid: int, node_id: str, topic: str, pool: float,
    reward: float = REWARD_MIN, per_user: int = PER_USER_DEFAULT,
    days: int = DEFAULT_DURATION_DAYS,
) -> Tuple[Optional[str], Optional[str]]:
    """Crea un bounty y pone el pool en escrow. Devuelve (bounty_id, error).

    Errores: "not_member" | "pool_too_small" | "bad_reward" | "bad_duration"
    | "insufficient".
    """
    from aisynergix.services.irys import get_node_member, write_bounty

    err = validate_params(pool, reward, days)
    if err:
        return None, err
    per_user = max(0, min(PER_USER_MAX, int(per_user)))

    sponsor_hash = _hash_uid(uid)
    member = await get_node_member(node_id, sponsor_hash)
    if not member or member.get("member-status", "active") != "active":
        return None, "not_member"

    identity = _identity()
    debited = await identity.debit_synx(sponsor_hash, round(pool, 2))
    if debited is None:
        return None, "insufficient"

    ts = int(datetime.now(timezone.utc).timestamp())
    bounty_id = generate_bounty_id(sponsor_hash, node_id, topic, ts)
    deadline = ts + days * 86400
    try:
        await write_bounty(
            bounty_id, node_id, (topic or "").strip().lower(), sponsor_hash,
            round(debited, 2), round(reward, 2), per_user, deadline,
            status="open", paid_count=0,
        )
    except Exception as exc:
        await identity.credit_synx(sponsor_hash, debited)  # revertir escrow
        logger.warning("create_bounty: sellado falló, escrow devuelto: %s", exc)
        return None, "insufficient"
    logger.info("🎯 Bounty %s creado por %s (pool %.0f SYNX)", bounty_id, sponsor_hash, debited)
    return bounty_id, None


async def open_bounty_for(node_id: str, topic: str, now: Optional[int] = None) -> Optional[Dict[str, str]]:
    """Devuelve el bounty abierto de mayor recompensa para un nodo+tema, o None."""
    from aisynergix.services.irys import list_node_bounties
    now = now if now is not None else int(datetime.now(timezone.utc).timestamp())
    topic = (topic or "").strip().lower()
    candidates = [
        b for b in await list_node_bounties(node_id)
        if b.get("topic", "") == topic and is_open(b, now)
        and remaining_pool(b) >= _f(b.get("reward", 0)) > 0
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda b: _f(b.get("reward", 0)))


async def reward_aporte(
    node_id: str, topic: str, author_hash: str, aporte_tx: str,
    now: Optional[int] = None,
) -> Optional[float]:
    """Paga (si procede) un aporte verificado contra el bounty abierto del tema.

    Idempotente por aporte: si el aporte ya fue pagado, no paga de nuevo.
    Devuelve el monto pagado, o None si no había bounty aplicable / no procede.
    """
    from aisynergix.services.irys import (
        read_bounty, list_bounty_claims, write_bounty, write_bounty_claim,
    )
    if not aporte_tx or aporte_tx.startswith("local:"):
        return None
    now = now if now is not None else int(datetime.now(timezone.utc).timestamp())

    bounty = await open_bounty_for(node_id, topic, now)
    if not bounty:
        return None
    bounty_id = bounty.get("bounty-id", "")

    async with _lock_for(bounty_id):
        record = await read_bounty(bounty_id) or bounty
        claims = await list_bounty_claims(bounty_id)
        if any(c.get("aporte-tx") == aporte_tx for c in claims):
            return None  # ya pagado (idempotencia)
        user_paid = sum(1 for c in claims if c.get("author-uid") == author_hash)
        ok, _reason = can_pay(record, user_paid, now)
        if not ok:
            return None

        reward = _f(record.get("reward", 0))
        credited = await _identity().credit_synx(author_hash, reward)
        if credited is None:
            return None
        try:
            await write_bounty_claim(bounty_id, author_hash, aporte_tx, reward)
            paid = _i(record.get("paid-count", 0)) + 1
            new_status = "open"
            if remaining_pool({**record, "paid-count": str(paid)}) + 1e-9 < reward:
                new_status = "closed"  # pool agotado
            await write_bounty(
                bounty_id, record.get("node-id", node_id), record.get("topic", topic),
                record.get("sponsor", ""), _f(record.get("pool", 0)), reward,
                _i(record.get("per-user", PER_USER_DEFAULT)),
                _i(record.get("deadline", 0)), status=new_status, paid_count=paid,
            )
        except Exception as exc:
            logger.error("reward_aporte: crédito OK pero sello del bounty falló: %s", exc)
        logger.info("🎯 Bounty %s pagó %.2f SYNX a %s por %s",
                    bounty_id, reward, author_hash, aporte_tx[:12])
        return reward


async def expire_and_refund(bounty_id: str, now: Optional[int] = None) -> Optional[float]:
    """Cierra un bounty vencido y devuelve el sobrante al patrocinador."""
    from aisynergix.services.irys import read_bounty, write_bounty
    now = now if now is not None else int(datetime.now(timezone.utc).timestamp())
    async with _lock_for(bounty_id):
        record = await read_bounty(bounty_id)
        if not record or record.get("bounty-status") != "open":
            return None
        deadline = _i(record.get("deadline", 0))
        if deadline and now < deadline:
            return None  # aún vigente
        refund = refund_amount(record)
        if refund > 0:
            await _identity().credit_synx(record.get("sponsor", ""), refund)
        await write_bounty(
            bounty_id, record.get("node-id", ""), record.get("topic", ""),
            record.get("sponsor", ""), _f(record.get("pool", 0)),
            _f(record.get("reward", 0)), _i(record.get("per-user", PER_USER_DEFAULT)),
            deadline, status="expired", paid_count=_i(record.get("paid-count", 0)),
        )
        logger.info("🎯 Bounty %s expirado; %.2f SYNX devueltos al patrocinador",
                    bounty_id, refund)
        return refund


async def list_bounties(node_id: str) -> List[Dict[str, Any]]:
    """Bounties de un nodo con el pool restante calculado (para UI/API)."""
    from aisynergix.services.irys import list_node_bounties
    now = int(datetime.now(timezone.utc).timestamp())
    out: List[Dict[str, Any]] = []
    for b in await list_node_bounties(node_id):
        out.append({
            **b,
            "remaining": remaining_pool(b),
            "open": is_open(b, now),
        })
    return out


__all__ = [
    "BOUNTY_MIN_POOL", "REWARD_MIN", "DEFAULT_DURATION_DAYS", "PER_USER_DEFAULT",
    "generate_bounty_id", "is_open", "remaining_pool", "refund_amount",
    "can_pay", "validate_params", "max_recipients",
    "create_bounty", "open_bounty_for", "reward_aporte", "expire_and_refund",
    "list_bounties",
]
