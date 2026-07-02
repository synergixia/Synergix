"""projects.py — financiamiento colectivo de nodos (diseño §6.2, uso 3).

Los miembros de un nodo financian proyectos con SYNX.  Los fondos quedan en
escrow (debitados del saldo del financiador y registrados append-only en
Irys) hasta que los propios financiadores verifican que el proyecto se
completó: mayoría simple (>50 % de los votos emitidos, §6.2) → los fondos se
liberan al creador; en caso contrario (o si nadie vota en la ventana) se
devuelven a cada financiador.

Máquina de estados del proyecto (sellada en Irys, última versión gana):

    active ──(creador solicita verificación)──► voting ──► completed
                                                   └──────► refunded

Nota Fase 2: el escrow es contable (ledger SYNX en Irys), no un smart
contract.  El contrato EVM llega en Fase 3; la semántica es la misma.
"""

import asyncio
import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

GOAL_MIN_SYNX = 10.0
FUND_MIN_SYNX = 1.0
VOTING_WINDOW_H = 72          # §6.2 (gobernanza): 72 horas de ventana
MAX_TITLE_LEN = 80
MIN_TITLE_LEN = 3

# Locks por proyecto (en este proceso) para que financiar/votar/resolver no
# se pisen; el estado autoritativo vive sellado en Irys.
_locks: Dict[str, asyncio.Lock] = {}
# Estados resueltos por este proceso — evita una doble liberación si Irys aún
# no indexó el sellado de "completed"/"refunded".
_resolved: Dict[str, str] = {}


def _lock_for(project_id: str) -> asyncio.Lock:
    lock = _locks.get(project_id)
    if lock is None:
        lock = asyncio.Lock()
        _locks[project_id] = lock
    return lock


def _hash_uid(uid: int) -> str:
    from aisynergix.bot.identity import _hash_uid as h
    return h(uid)


def _identity():
    from aisynergix.bot.identity import get_identity_manager
    return get_identity_manager()


def generate_project_id(creator_hash: str, title: str) -> str:
    ts = int(datetime.now(timezone.utc).timestamp())
    raw = f"{creator_hash}:{title.strip().lower()}:{ts}"
    return "proj_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def resolve_outcome(votes: Dict[str, bool]) -> str:
    """Regla del 51 % (§6.2): mayoría estricta de los votos emitidos aprueba.

    'release' si aprobaciones > mitad de los votos; 'refund' en otro caso
    (incluye empate y cero votos — conservador: el dinero vuelve).
    """
    if not votes:
        return "refund"
    approvals = sum(1 for v in votes.values() if v)
    return "release" if approvals * 2 > len(votes) else "refund"


def funds_by_user(fund_records: List[Dict[str, str]]) -> Dict[str, float]:
    """Suma las contribuciones append-only por financiador."""
    totals: Dict[str, float] = {}
    for rec in fund_records:
        uid = rec.get("uid-hash", "")
        try:
            amount = float(rec.get("amount", 0) or 0)
        except (ValueError, TypeError):
            continue
        if uid and amount > 0:
            totals[uid] = round(totals.get(uid, 0.0) + amount, 2)
    return totals


def _status_of(record: Dict[str, str], project_id: str) -> str:
    """Status vigente: lo resuelto en este proceso pisa una lectura stale."""
    return _resolved.get(project_id) or record.get("project-status", "active")


async def create_project(
    uid: int, node_id: str, title: str, goal: float
) -> Optional[Dict[str, Any]]:
    """Crea un proyecto en el nodo. Retorna sus datos o None si es inválido."""
    from aisynergix.services.irys import write_project, get_node_member

    title = (title or "").strip()
    if not (MIN_TITLE_LEN <= len(title) <= MAX_TITLE_LEN) or goal < GOAL_MIN_SYNX:
        return None
    creator = _hash_uid(uid)
    member = await get_node_member(node_id, creator)
    if not member or member.get("member-status", "active") != "active":
        return None

    project_id = generate_project_id(creator, title)
    await write_project(project_id, node_id, creator, title, goal, status="active")
    logger.info("🏗️ Proyecto %s '%s' creado en %s (meta %.0f SYNX)",
                project_id, title, node_id, goal)
    return {"project_id": project_id, "title": title, "goal": goal,
            "creator": creator, "status": "active"}


async def get_project(project_id: str) -> Optional[Dict[str, Any]]:
    """Proyecto + total recaudado + financiadores."""
    from aisynergix.services.irys import read_project, list_project_funds
    record = await read_project(project_id)
    if not record:
        return None
    funds = funds_by_user(await list_project_funds(project_id))
    return {
        "project_id": project_id,
        "node_id": record.get("node-id", ""),
        "title": record.get("title", ""),
        "creator": record.get("creator", ""),
        "goal": float(record.get("goal", 0) or 0),
        "status": _status_of(record, project_id),
        "voting_until": int(record.get("voting-until", 0) or 0),
        "raised": round(sum(funds.values()), 2),
        "funders": funds,
    }


async def list_projects(node_id: str) -> List[Dict[str, str]]:
    from aisynergix.services.irys import list_node_projects
    records = await list_node_projects(node_id)
    for r in records:
        pid = r.get("project-id", "")
        if pid in _resolved:
            r["project-status"] = _resolved[pid]
    return records


async def fund_project(uid: int, project_id: str, amount: float) -> Optional[str]:
    """Aporta SYNX al escrow. Retorna clave de error o None si fue bien.

    Errores: "bad_amount" | "not_active" | "insufficient".
    """
    from aisynergix.services.irys import read_project, write_project_fund
    if amount < FUND_MIN_SYNX:
        return "bad_amount"
    async with _lock_for(project_id):
        record = await read_project(project_id)
        if not record or _status_of(record, project_id) != "active":
            return "not_active"
        uid_hash = _hash_uid(uid)
        debited = await _identity().debit_synx(uid_hash, round(amount, 2))
        if debited is None:
            return "insufficient"
        try:
            await write_project_fund(project_id, uid_hash, debited)
        except Exception:
            # El débito ya ocurrió pero el registro falló: devolver.
            await _identity().credit_synx(uid_hash, debited)
            return "not_active"
        logger.info("💰 %s financió %s con %.2f SYNX", uid_hash, project_id, debited)
        return None


async def request_completion(uid: int, project_id: str) -> Optional[str]:
    """El creador declara el proyecto completado → abre votación de 72 h.

    Errores: "not_creator" | "not_active" | "no_funds".
    """
    from aisynergix.services.irys import read_project, list_project_funds, write_project
    async with _lock_for(project_id):
        record = await read_project(project_id)
        if not record or _status_of(record, project_id) != "active":
            return "not_active"
        if record.get("creator") != _hash_uid(uid):
            return "not_creator"
        funds = funds_by_user(await list_project_funds(project_id))
        if not funds:
            return "no_funds"
        voting_until = int(datetime.now(timezone.utc).timestamp()) + VOTING_WINDOW_H * 3600
        await write_project(
            project_id, record.get("node-id", ""), record.get("creator", ""),
            record.get("title", ""), float(record.get("goal", 0) or 0),
            status="voting", voting_until=voting_until,
        )
        return None


async def vote_project(uid: int, project_id: str, approve: bool) -> Optional[str]:
    """Voto de verificación de un financiador. Puede disparar la resolución.

    Errores: "not_voting" | "not_funder".  Éxito: None o el estado final
    ("completed"/"refunded") si el voto completó la votación.
    """
    from aisynergix.services.irys import (
        read_project, list_project_funds, list_project_votes, write_project_vote,
    )
    async with _lock_for(project_id):
        record = await read_project(project_id)
        if not record or _status_of(record, project_id) != "voting":
            return "not_voting"
        uid_hash = _hash_uid(uid)
        funders = funds_by_user(await list_project_funds(project_id))
        if uid_hash not in funders:
            return "not_funder"
        await write_project_vote(project_id, uid_hash, approve)

        votes = {
            v.get("uid-hash", ""): v.get("vote") == "yes"
            for v in await list_project_votes(project_id)
            if v.get("uid-hash") in funders
        }
        # Nuestro voto puede no estar indexado aún — inclúyelo localmente.
        votes[uid_hash] = approve
        if set(votes) >= set(funders):
            return await _resolve(record, project_id, funders, votes)
        return None


async def check_and_resolve(project_id: str) -> Optional[str]:
    """Resolución perezosa: si la ventana de votación expiró, resuelve.

    Retorna el estado final si resolvió, None si sigue en curso.
    """
    from aisynergix.services.irys import (
        read_project, list_project_funds, list_project_votes,
    )
    async with _lock_for(project_id):
        record = await read_project(project_id)
        if not record or _status_of(record, project_id) != "voting":
            return None
        voting_until = int(record.get("voting-until", 0) or 0)
        if voting_until and datetime.now(timezone.utc).timestamp() < voting_until:
            return None
        funders = funds_by_user(await list_project_funds(project_id))
        votes = {
            v.get("uid-hash", ""): v.get("vote") == "yes"
            for v in await list_project_votes(project_id)
            if v.get("uid-hash") in funders
        }
        return await _resolve(record, project_id, funders, votes)


async def _resolve(
    record: Dict[str, str], project_id: str,
    funders: Dict[str, float], votes: Dict[str, bool],
) -> str:
    """Libera al creador o reembolsa a los financiadores. Sella el estado."""
    from aisynergix.services.irys import write_project
    outcome = resolve_outcome(votes)
    total = round(sum(funders.values()), 2)
    identity = _identity()

    if outcome == "release":
        status = "completed"
        if total > 0:
            await identity.credit_synx(record.get("creator", ""), total)
        logger.info("✅ Proyecto %s COMPLETADO: %.2f SYNX → %s",
                    project_id, total, record.get("creator", "")[:8])
    else:
        status = "refunded"
        for uid_hash, amount in funders.items():
            if amount > 0:
                await identity.credit_synx(uid_hash, amount)
        logger.info("↩️ Proyecto %s REEMBOLSADO: %.2f SYNX a %d financiador(es)",
                    project_id, total, len(funders))

    # Marcar ANTES de sellar: si el sellado falla, este proceso ya no vuelve
    # a pagar (la re-resolución tras un restart es el riesgo residual asumido
    # hasta el smart contract de Fase 3).
    _resolved[project_id] = status
    try:
        await write_project(
            project_id, record.get("node-id", ""), record.get("creator", ""),
            record.get("title", ""), float(record.get("goal", 0) or 0),
            status=status,
        )
    except Exception as exc:
        logger.error("sellado de %s (%s) falló: %s", project_id, status, exc)
    return status


__all__ = [
    "GOAL_MIN_SYNX",
    "FUND_MIN_SYNX",
    "VOTING_WINDOW_H",
    "resolve_outcome",
    "funds_by_user",
    "generate_project_id",
    "create_project",
    "get_project",
    "list_projects",
    "fund_project",
    "request_completion",
    "vote_project",
    "check_and_resolve",
]
