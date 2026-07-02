"""governance.py — gobernanza descentralizada de nodos (diseño §6.2, uso 6).

Cualquier miembro propone cambios en su nodo; los miembros votan con peso
proporcional a su SYNX: 1 SYNX = 1 voto (peso = ⌊saldo⌋ en el momento de
votar).  Aprobación: ≥ 66 % del peso votado, ventana de 72 horas.

La propuesta y cada voto quedan sellados en Irys (auditables por terceros);
la resolución es perezosa: al consultar una propuesta expirada se computa y
sella el resultado.  Las propuestas aprobadas son registros vinculantes de
la voluntad del nodo — la aplicación automática de parámetros llega con los
contratos de gobernanza on-chain.
"""

import asyncio
import hashlib
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

APPROVAL_PCT = 0.66          # §6.2: aprobación con 66 %
VOTING_WINDOW_H = 72         # §6.2: en 72 horas
MIN_TEXT_LEN = 10
MAX_TEXT_LEN = 500

_locks: Dict[str, asyncio.Lock] = {}
_resolved: Dict[str, str] = {}


def _lock_for(pid: str) -> asyncio.Lock:
    lock = _locks.get(pid)
    if lock is None:
        lock = asyncio.Lock()
        _locks[pid] = lock
    return lock


def _hash_uid(uid: int) -> str:
    from aisynergix.bot.identity import _hash_uid as h
    return h(uid)


def generate_proposal_id(creator_hash: str, text: str) -> str:
    ts = int(datetime.now(timezone.utc).timestamp())
    raw = f"{creator_hash}:{text.strip().lower()[:80]}:{ts}"
    return "prop_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def vote_weight(synx_balance: float) -> int:
    """1 SYNX = 1 voto (§6.2). Sin SYNX no hay voto."""
    return max(0, int(synx_balance))


def tally_weighted(votes: List[Tuple[bool, int]]) -> Tuple[str, int, int]:
    """Computa una votación ponderada.

    Retorna (resultado, peso_sí, peso_total).  Aprobada si el peso a favor
    alcanza APPROVAL_PCT del peso total emitido; sin votos → rechazada.
    """
    total = sum(w for _, w in votes if w > 0)
    yes = sum(w for v, w in votes if v and w > 0)
    if total <= 0:
        return "rejected", 0, 0
    outcome = "approved" if yes / total >= APPROVAL_PCT else "rejected"
    return outcome, yes, total


def _status_of(record: Dict[str, str], pid: str) -> str:
    return _resolved.get(pid) or record.get("proposal-status", "open")


async def create_proposal(uid: int, node_id: str, text: str) -> Optional[Dict[str, str]]:
    """Crea una propuesta en el nodo (solo miembros). None si es inválida."""
    from aisynergix.services.irys import write_proposal, get_node_member
    text = (text or "").strip()
    if not (MIN_TEXT_LEN <= len(text) <= MAX_TEXT_LEN):
        return None
    creator = _hash_uid(uid)
    member = await get_node_member(node_id, creator)
    if not member or member.get("member-status", "active") != "active":
        return None
    pid = generate_proposal_id(creator, text)
    voting_until = int(datetime.now(timezone.utc).timestamp()) + VOTING_WINDOW_H * 3600
    await write_proposal(pid, node_id, creator, text, status="open",
                         voting_until=voting_until)
    return {"proposal_id": pid, "text": text, "voting_until": str(voting_until)}


async def list_proposals(node_id: str) -> List[Dict[str, str]]:
    from aisynergix.services.irys import list_node_proposals
    records = await list_node_proposals(node_id)
    for r in records:
        pid = r.get("proposal-id", "")
        if pid in _resolved:
            r["proposal-status"] = _resolved[pid]
    return records


async def get_proposal(pid: str) -> Optional[Dict[str, str]]:
    from aisynergix.services.irys import read_proposal
    record = await read_proposal(pid)
    if record:
        record["proposal-status"] = _status_of(record, pid)
    return record


async def vote_proposal(uid: int, pid: str, approve: bool) -> Optional[str]:
    """Voto ponderado de un miembro. Errores: "closed" | "not_member" | "no_weight"."""
    from aisynergix.bot.identity import get_identity_manager
    from aisynergix.services.irys import (
        read_proposal, get_node_member, write_proposal_vote,
    )
    async with _lock_for(pid):
        record = await read_proposal(pid)
        if not record or _status_of(record, pid) != "open":
            return "closed"
        now = datetime.now(timezone.utc).timestamp()
        if now > int(record.get("voting-until", 0) or 0):
            return "closed"
        uid_hash = _hash_uid(uid)
        member = await get_node_member(record.get("node-id", ""), uid_hash)
        if not member or member.get("member-status", "active") != "active":
            return "not_member"
        profile = await get_identity_manager().get_profile(uid)
        weight = vote_weight(profile.synx_balance)
        if weight <= 0:
            return "no_weight"
        await write_proposal_vote(pid, uid_hash, approve, weight)
        logger.info("🗳️ Voto en %s: %s peso=%d (%s)", pid, uid_hash,
                    weight, "sí" if approve else "no")
        return None


async def check_and_resolve(pid: str) -> Optional[str]:
    """Resolución perezosa al expirar la ventana. Retorna el estado final o None."""
    from aisynergix.services.irys import (
        read_proposal, list_proposal_votes, write_proposal,
    )
    async with _lock_for(pid):
        record = await read_proposal(pid)
        if not record or _status_of(record, pid) != "open":
            return None
        voting_until = int(record.get("voting-until", 0) or 0)
        if voting_until and datetime.now(timezone.utc).timestamp() < voting_until:
            return None

        vote_records = await list_proposal_votes(pid)
        votes: List[Tuple[bool, int]] = []
        for v in vote_records:
            try:
                votes.append((v.get("vote") == "yes", int(v.get("weight", 0) or 0)))
            except (ValueError, TypeError):
                continue
        outcome, yes, total = tally_weighted(votes)

        _resolved[pid] = outcome
        try:
            await write_proposal(
                pid, record.get("node-id", ""), record.get("creator", ""),
                record.get("text", ""), status=outcome, voting_until=voting_until,
            )
        except Exception as exc:
            logger.error("sellado de propuesta %s (%s) falló: %s", pid, outcome, exc)
        logger.info("🗳️ Propuesta %s → %s (sí=%d / total=%d)", pid, outcome, yes, total)
        return outcome


async def proposal_tallies(pid: str) -> Tuple[int, int]:
    """(peso_sí, peso_total) actuales de una propuesta."""
    from aisynergix.services.irys import list_proposal_votes
    votes: List[Tuple[bool, int]] = []
    for v in await list_proposal_votes(pid):
        try:
            votes.append((v.get("vote") == "yes", int(v.get("weight", 0) or 0)))
        except (ValueError, TypeError):
            continue
    _, yes, total = tally_weighted(votes)
    return yes, total


__all__ = [
    "APPROVAL_PCT",
    "VOTING_WINDOW_H",
    "vote_weight",
    "tally_weighted",
    "generate_proposal_id",
    "create_proposal",
    "list_proposals",
    "get_proposal",
    "vote_proposal",
    "check_and_resolve",
    "proposal_tallies",
]
