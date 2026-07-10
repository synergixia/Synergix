"""oracle.py — Jurado de Oráculos con staking (diseño §5.4 Juez 2 + §6.2 uso 5).

Los aportes con score ≥ 8 del Judge IA pasan a revisión humana: los Oráculos
(usuarios de rango alto con stake) votan 👍/👎.  Con 3 votos la mayoría
decide; si se aprueba, la recompensa del aporte se multiplica ×3 (§5.2) —
como la base ya se pagó, el autor recibe +2× extra.

Economía del stake (§6.2, uso 5):
  * Stake mínimo: 500 SYNX (se debita del saldo; se devuelve al retirarlo).
  * Voto correcto (con la mayoría): +15 SYNX.
  * Sistemáticamente incorrecto (3 votos seguidos contra la mayoría):
    −50 SYNX y el contador se reinicia.

Elegibilidad: rango 🔥 Contribuidor o superior (≥ 2 000 puntos) + stake.
Las revisiones expiran a las 24 h: si hubo votos, decide la mayoría emitida;
sin votos, la revisión caduca sin efecto (la base ya está pagada).
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

STAKE_MIN_SYNX = 500.0
VOTE_REWARD_SYNX = 15.0
PENALTY_SYNX = 50.0
WRONG_STREAK_LIMIT = 3
REQUIRED_VOTES = 3
REVIEW_WINDOW_H = 24
REVIEW_MIN_SCORE = 8.0
# La tabla §5.2 paga ×3 con Oráculo; la base ya se acreditó → extra = +2×.
ORACLE_EXTRA_MULT = 2.0
ELIGIBLE_MIN_POINTS = 2000   # 🔥 Contribuidor+

_locks: Dict[str, asyncio.Lock] = {}
_resolved: Dict[str, str] = {}   # aporte_tx → status final (anti doble pago)

# Hook opcional para notificar a los Oráculos cuando se abre una revisión
# (§5.4). Lo registra la capa del bot en el arranque; el servicio no importa
# el bot para evitar dependencias circulares.
_review_notifier = None   # Optional[Callable[[str], Awaitable[None]]]


def set_review_notifier(fn) -> None:
    """Registra el callback ``async fn(aporte_tx)`` de notificación push."""
    global _review_notifier
    _review_notifier = fn


def _lock_for(tx: str) -> asyncio.Lock:
    lock = _locks.get(tx)
    if lock is None:
        lock = asyncio.Lock()
        _locks[tx] = lock
    return lock


def _hash_uid(uid: int) -> str:
    from aisynergix.bot.identity import _hash_uid as h
    return h(uid)


def _identity():
    from aisynergix.bot.identity import get_identity_manager
    return get_identity_manager()


# ── Lógica pura (testeable) ──────────────────────────────────────────────

def tally(votes: Dict[str, bool]) -> bool:
    """Mayoría estricta aprueba; empate o vacío rechaza (conservador)."""
    approvals = sum(1 for v in votes.values() if v)
    return approvals * 2 > len(votes) if votes else False


def wrong_streak_after(streak: int, was_wrong: bool) -> Tuple[int, bool]:
    """Avanza el contador de votos incorrectos consecutivos.

    Retorna (racha_nueva, multa_debida).  Al llegar a WRONG_STREAK_LIMIT la
    multa se dispara y el contador se reinicia; un voto correcto lo pone a 0.
    """
    if not was_wrong:
        return 0, False
    streak = max(0, streak) + 1
    if streak >= WRONG_STREAK_LIMIT:
        return 0, True
    return streak, False


# ── Stake ────────────────────────────────────────────────────────────────

async def get_stake(uid_hash: str) -> Optional[Dict[str, str]]:
    """Stake vigente (tags) si está activo, si no None."""
    from aisynergix.services.irys import read_oracle_stake
    stake = await read_oracle_stake(uid_hash)
    if stake and stake.get("stake-status") == "active":
        return stake
    return None


async def stake(uid: int) -> Optional[str]:
    """Convierte al usuario en Oráculo. Retorna clave de error o None.

    Errores: "not_eligible" | "already_staked" | "insufficient".
    """
    from aisynergix.services.irys import write_oracle_stake
    identity = _identity()
    profile = await identity.get_profile(uid)
    if profile.points < ELIGIBLE_MIN_POINTS:
        return "not_eligible"
    uid_hash = _hash_uid(uid)
    if await get_stake(uid_hash):
        return "already_staked"
    debited = await identity.debit_synx(uid_hash, STAKE_MIN_SYNX)
    if debited is None:
        return "insufficient"
    try:
        await write_oracle_stake(uid_hash, debited, status="active", wrong_streak=0)
    except Exception:
        await identity.credit_synx(uid_hash, debited)
        return "insufficient"
    logger.info("🔮 Nuevo Oráculo: %s (stake %.0f SYNX)", uid_hash, debited)
    return None


async def unstake(uid: int) -> Optional[str]:
    """Retira el stake y devuelve los SYNX. Error: "not_staked"."""
    from aisynergix.services.irys import write_oracle_stake
    uid_hash = _hash_uid(uid)
    current = await get_stake(uid_hash)
    if not current:
        return "not_staked"
    amount = float(current.get("amount", 0) or 0)
    await write_oracle_stake(
        uid_hash, amount, status="withdrawn", wrong_streak=0,
        votes_total=int(current.get("votes-total", 0) or 0),
        votes_correct=int(current.get("votes-correct", 0) or 0),
    )
    if amount > 0:
        await _identity().credit_synx(uid_hash, amount)
    logger.info("🔮 Oráculo retirado: %s (+%.0f SYNX devueltos)", uid_hash, amount)
    return None


# ── Revisiones ───────────────────────────────────────────────────────────

async def create_review(aporte_tx: str, author_hash: str, base_synx: float) -> None:
    """Abre la revisión de Oráculos para un aporte score ≥ 8 (desde manager)."""
    from aisynergix.services.irys import write_oracle_review, read_oracle_review
    if not aporte_tx or aporte_tx.startswith("local:"):
        return
    try:
        if await read_oracle_review(aporte_tx):
            return  # ya existe
        now = int(datetime.now(timezone.utc).timestamp())
        await write_oracle_review(
            aporte_tx, author_hash, base_synx, status="pending", created_ts=now
        )
        # Notificación push a los Oráculos (§5.4), en segundo plano para no
        # bloquear la respuesta del aporte.
        if _review_notifier is not None:
            try:
                asyncio.create_task(_review_notifier(aporte_tx))
            except Exception:
                pass
    except Exception as exc:
        logger.warning("create_review %s falló: %s", aporte_tx[:12], exc)


def _status_of(review: Dict[str, str]) -> str:
    return _resolved.get(review.get("aporte-tx", "")) or review.get("review-status", "pending")


async def pending_reviews(limit: int = 5) -> List[Dict[str, str]]:
    """Revisiones pendientes; resuelve perezosamente las expiradas (24 h)."""
    from aisynergix.services.irys import list_oracle_reviews, list_oracle_votes
    now = datetime.now(timezone.utc).timestamp()
    out: List[Dict[str, str]] = []
    for review in await list_oracle_reviews():
        if _status_of(review) != "pending":
            continue
        created = int(review.get("created-ts", 0) or 0)
        if created and now - created > REVIEW_WINDOW_H * 3600:
            tx = review.get("aporte-tx", "")
            votes = {
                v.get("uid-hash", ""): v.get("vote") == "yes"
                for v in await list_oracle_votes(tx)
            }
            await _resolve(review, votes if votes else None)
            continue
        out.append(review)
        if len(out) >= limit:
            break
    return out


async def vote(uid: int, aporte_tx: str, approve: bool) -> Optional[str]:
    """Voto 👍/👎 de un Oráculo. Retorna error, estado final o None.

    Errores: "not_oracle" | "own_aporte" | "not_pending".
    Si el voto alcanza REQUIRED_VOTES, resuelve y retorna "approved"/"rejected".
    """
    from aisynergix.services.irys import (
        read_oracle_review, write_oracle_vote, list_oracle_votes,
    )
    uid_hash = _hash_uid(uid)
    if not await get_stake(uid_hash):
        return "not_oracle"
    async with _lock_for(aporte_tx):
        review = await read_oracle_review(aporte_tx)
        if not review or _status_of(review) != "pending":
            return "not_pending"
        if review.get("author-uid") == uid_hash:
            return "own_aporte"
        await write_oracle_vote(aporte_tx, uid_hash, approve)
        votes = {
            v.get("uid-hash", ""): v.get("vote") == "yes"
            for v in await list_oracle_votes(aporte_tx)
        }
        votes[uid_hash] = approve  # por si Irys aún no indexó el nuestro
        if len(votes) >= REQUIRED_VOTES:
            return await _resolve(review, votes)
        return None


async def _resolve(
    review: Dict[str, str], votes: Optional[Dict[str, bool]]
) -> str:
    """Cierra la revisión: paga al autor si se aprueba y liquida a los votantes."""
    from aisynergix.services.irys import write_oracle_review, read_oracle_stake, write_oracle_stake
    aporte_tx = review.get("aporte-tx", "")
    author = review.get("author-uid", "")
    base_synx = float(review.get("base-synx", 0) or 0)
    identity = _identity()

    if not votes:
        status = "expired"
    else:
        approved = tally(votes)
        status = "approved" if approved else "rejected"

        # Autor: ×3 total (§5.2) → +2× extra sobre la base ya pagada.
        if approved and author and base_synx > 0:
            await identity.credit_synx(author, round(base_synx * ORACLE_EXTRA_MULT, 2))

        # Votantes: +15 con la mayoría; racha de errores para la minoría.
        # También se acumula la reputación como juez (tasa de acierto,
        # campo del Passport §10.2).
        for voter, v in votes.items():
            correct = (v == approved)
            if correct:
                await identity.credit_synx(voter, VOTE_REWARD_SYNX)
                new_streak, penalty = 0, False
            else:
                stake_tags = await read_oracle_stake(voter) or {}
                streak = int(stake_tags.get("wrong-streak", 0) or 0)
                new_streak, penalty = wrong_streak_after(streak, True)
                if penalty:
                    await identity.debit_synx(voter, PENALTY_SYNX, allow_partial=True)
            try:
                stake_tags = await read_oracle_stake(voter) or {}
                if stake_tags.get("stake-status") == "active":
                    await write_oracle_stake(
                        voter, float(stake_tags.get("amount", 0) or 0),
                        status="active", wrong_streak=new_streak,
                        votes_total=int(stake_tags.get("votes-total", 0) or 0) + 1,
                        votes_correct=(
                            int(stake_tags.get("votes-correct", 0) or 0)
                            + (1 if correct else 0)
                        ),
                    )
            except Exception:
                pass

    _resolved[aporte_tx] = status
    try:
        await write_oracle_review(
            aporte_tx, author, base_synx, status=status,
            created_ts=int(review.get("created-ts", 0) or 0),
            votes=len(votes or {}),
        )
    except Exception as exc:
        logger.error("sellado de review %s (%s) falló: %s", aporte_tx[:12], status, exc)
    logger.info("⚖️ Review %s → %s (%d votos)", aporte_tx[:12], status, len(votes or {}))
    return status


__all__ = [
    "STAKE_MIN_SYNX",
    "VOTE_REWARD_SYNX",
    "PENALTY_SYNX",
    "WRONG_STREAK_LIMIT",
    "REQUIRED_VOTES",
    "REVIEW_WINDOW_H",
    "REVIEW_MIN_SCORE",
    "ORACLE_EXTRA_MULT",
    "ELIGIBLE_MIN_POINTS",
    "tally",
    "wrong_streak_after",
    "set_review_notifier",
    "get_stake",
    "stake",
    "unstake",
    "create_review",
    "pending_reviews",
    "vote",
]
