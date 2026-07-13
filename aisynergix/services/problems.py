"""problems.py — Atlas de Problemas y Soluciones (nodos territoriales).

Cada nodo tiene un registro cívico permanente de problemas, sellado en Irys:

    open ──(alguien propone solución)──► solving ──(la comunidad la
                                                    confirma)──► solved

  * Reportar: cualquier miembro describe un problema del territorio.
  * Confirmar: otros miembros validan que el problema es real (👍).
  * Solucionar: alguien propone la solución; cuando ``SOLVED_THRESHOLD``
    miembros distintos confirman que funciona, el problema queda RESUELTO,
    el solucionador cobra ``SOLVER_REWARD_SYNX`` y la solución se indexa en
    la memoria inmortal (RAG).

El Atlas translingüe: al reportar un problema, el RAG busca en TODA la red
—en los 10 idiomas— conocimiento y soluciones ya selladas para problemas
similares ("esto ya se resolvió en otro barrio"), y los autores mostrados
reciben impacto PIR: una solución escrita una vez sirve (y paga) para siempre.

La máquina de estados y las validaciones son puras y testeables sin red.
"""

import asyncio
import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

MIN_TEXT_LEN = 10
MAX_TEXT_LEN = 300
SOLVED_THRESHOLD = 3          # confirmaciones distintas para marcar resuelto
SOLVER_REWARD_SYNX = 10.0     # recompensa al autor de la solución verificada
ATLAS_MAX_MATCHES = 2         # coincidencias del atlas mostradas al reportar
ATLAS_MIN_SCORE = 0.55        # relevancia mínima para mostrar una coincidencia
REPORTS_DAILY_CAP = 5         # reportes por usuario y día (anti-spam de Irys)

# Contador diario de reportes en RAM: uid -> (fecha_iso, count).
_daily_reports: Dict[int, Tuple[str, int]] = {}

_locks: Dict[str, asyncio.Lock] = {}
# Estados resueltos por este proceso (anti doble pago con lag de Irys).
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


def _identity():
    from aisynergix.bot.identity import get_identity_manager
    return get_identity_manager()


# ── Lógica pura (testeable sin red) ──────────────────────────────────────

def generate_problem_id(node_id: str, reporter_hash: str, ts: int) -> str:
    raw = f"{node_id}:{reporter_hash}:{ts}"
    return "prb_" + hashlib.sha256(raw.encode()).hexdigest()[:16]


def validate_text(text: str) -> Optional[str]:
    """Texto normalizado de problema/solución, o None si es inválido."""
    clean = " ".join((text or "").split())
    if not (MIN_TEXT_LEN <= len(clean) <= MAX_TEXT_LEN):
        return None
    return clean


def status_of(record: Dict[str, str]) -> str:
    pid = record.get("problem-id", "")
    return _resolved.get(pid) or record.get("problem-status", "open")


def can_confirm(record: Dict[str, str], uid_hash: str) -> Tuple[bool, str]:
    """¿Puede este usuario confirmar el problema? (ok, motivo).

    Motivos: "closed" | "own_problem".
    """
    if status_of(record) == "solved":
        return False, "closed"
    if record.get("reporter", "") == uid_hash:
        return False, "own_problem"
    return True, "ok"


def can_confirm_solution(record: Dict[str, str], uid_hash: str) -> Tuple[bool, str]:
    """¿Puede confirmar que la solución funciona? (ok, motivo).

    Motivos: "not_solving" | "own_solution".
    """
    if status_of(record) != "solving":
        return False, "not_solving"
    if record.get("solver", "") == uid_hash:
        return False, "own_solution"
    return True, "ok"


def distinct_confirmers(confirms: List[Dict[str, str]]) -> int:
    """Nº de usuarios distintos que confirmaron (append-only → dedupe)."""
    return len({c.get("uid-hash", "") for c in confirms if c.get("uid-hash")})


def is_solved(confirm_count: int) -> bool:
    return confirm_count >= SOLVED_THRESHOLD


def atlas_index_text(problem_text: str, solution_text: str) -> str:
    """Texto que se indexa en el RAG para que futuros problemas lo encuentren."""
    return f"Problema: {problem_text}\nSolución verificada: {solution_text}"


def reports_today(uid: int, today: str) -> int:
    date, count = _daily_reports.get(uid, ("", 0))
    return count if date == today else 0


def _note_report(uid: int, today: str) -> None:
    _daily_reports[uid] = (today, reports_today(uid, today) + 1)
    if len(_daily_reports) > 10_000:
        _daily_reports.pop(next(iter(_daily_reports)), None)


async def _is_active_member(node_id: str, uid_hash: str) -> bool:
    """Toda acción del Atlas exige membresía activa del nodo (anti-Sybil:
    las cuentas de paja no pueden confirmar ni resolver sin unirse al nodo,
    y cada acción queda ligada a un Ghost ID miembro)."""
    from aisynergix.services.irys import get_node_member
    member = await get_node_member(node_id, uid_hash)
    return bool(member and member.get("member-status", "active") == "active")


# ── Orquestación (con red) ───────────────────────────────────────────────

async def report_problem(uid: int, node_id: str, text: str) -> Tuple[Optional[str], Optional[str]]:
    """Reporta un problema del nodo. Devuelve (problem_id, error).

    Errores: "bad_text" | "not_member" | "daily_cap".
    """
    from aisynergix.services.irys import write_problem
    clean = validate_text(text)
    if not clean:
        return None, "bad_text"
    uid_hash = _hash_uid(uid)
    if not await _is_active_member(node_id, uid_hash):
        return None, "not_member"
    today = datetime.now(timezone.utc).date().isoformat()
    if reports_today(uid, today) >= REPORTS_DAILY_CAP:
        return None, "daily_cap"
    ts = int(datetime.now(timezone.utc).timestamp())
    pid = generate_problem_id(node_id, uid_hash, ts)
    await write_problem(
        pid, node_id, uid_hash, clean, status="open",
        solver="", solution="",
    )
    _note_report(uid, today)
    logger.info("🚩 Problema %s reportado en %s por %s", pid, node_id, uid_hash)
    return pid, None


async def confirm_problem(uid: int, problem_id: str) -> Tuple[Optional[int], Optional[str]]:
    """Confirma que un problema es real. Devuelve (nº confirmaciones, error).

    Solo miembros activos del nodo; idempotente (confirmar dos veces no
    escribe dos veces en Irys). Errores: "not_found" | "closed" |
    "own_problem" | "not_member".
    """
    from aisynergix.services.irys import (
        read_problem, write_problem_confirm, list_problem_confirms,
    )
    uid_hash = _hash_uid(uid)
    record = await read_problem(problem_id)
    if not record:
        return None, "not_found"
    ok, reason = can_confirm(record, uid_hash)
    if not ok:
        return None, reason
    if not await _is_active_member(record.get("node-id", ""), uid_hash):
        return None, "not_member"
    confirms = await list_problem_confirms(problem_id, kind="problem")
    if uid_hash not in {c.get("uid-hash") for c in confirms}:
        await write_problem_confirm(problem_id, uid_hash, kind="problem")
        confirms.append({"uid-hash": uid_hash})
    count = distinct_confirmers(confirms)
    return max(count, 1), None


async def propose_solution(uid: int, problem_id: str, text: str) -> Optional[str]:
    """Propone la solución de un problema (open → solving). Devuelve error o None.

    Solo miembros activos del nodo pueden proponer (la recompensa al resolver
    exige piel en el juego). Errores: "bad_text" | "not_found" |
    "already_solved" | "not_member".
    """
    from aisynergix.services.irys import read_problem, write_problem
    clean = validate_text(text)
    if not clean:
        return "bad_text"
    async with _lock_for(problem_id):
        record = await read_problem(problem_id)
        if not record:
            return "not_found"
        if status_of(record) == "solved":
            return "already_solved"
        uid_hash = _hash_uid(uid)
        if not await _is_active_member(record.get("node-id", ""), uid_hash):
            return "not_member"
        await write_problem(
            problem_id, record.get("node-id", ""), record.get("reporter", ""),
            record.get("problem-text", ""), status="solving",
            solver=uid_hash, solution=clean,
        )
        logger.info("💡 Solución propuesta para %s por %s", problem_id, uid_hash)
        return None


async def confirm_solution(uid: int, problem_id: str) -> Tuple[Optional[str], Optional[str]]:
    """Confirma que la solución funciona. Devuelve (estado, error).

    Al llegar a SOLVED_THRESHOLD confirmaciones distintas: problema RESUELTO,
    el solucionador cobra y la solución se indexa en la memoria inmortal.
    Errores: "not_found" | "not_solving" | "own_solution".
    """
    from aisynergix.services.irys import (
        read_problem, write_problem, write_problem_confirm, list_problem_confirms,
    )
    uid_hash = _hash_uid(uid)
    async with _lock_for(problem_id):
        record = await read_problem(problem_id)
        if not record:
            return None, "not_found"
        ok, reason = can_confirm_solution(record, uid_hash)
        if not ok:
            return None, reason
        if not await _is_active_member(record.get("node-id", ""), uid_hash):
            return None, "not_member"
        confirms = await list_problem_confirms(problem_id, kind="solution")
        if uid_hash not in {c.get("uid-hash") for c in confirms}:
            await write_problem_confirm(problem_id, uid_hash, kind="solution")
            confirms.append({"uid-hash": uid_hash})
        count = distinct_confirmers(confirms)
        if not is_solved(count):
            return "solving", None

        # RESUELTO: sellar, pagar al solucionador e indexar en el atlas.
        _resolved[problem_id] = "solved"
        solver = record.get("solver", "")
        tx = await write_problem(
            problem_id, record.get("node-id", ""), record.get("reporter", ""),
            record.get("problem-text", ""), status="solved",
            solver=solver, solution=record.get("solution", ""),
        )
        if solver:
            try:
                await _identity().credit_synx(solver, SOLVER_REWARD_SYNX)
            except Exception as exc:
                logger.warning("confirm_solution: pago a %s falló: %s", solver, exc)
        asyncio.create_task(_index_solution(record, solver, tx))
        logger.info("✅ Problema %s RESUELTO (solución de %s)", problem_id, solver)
        return "solved", None


async def _index_solution(record: Dict[str, str], solver: str, seal_tx: str) -> None:
    """Indexa la solución verificada en el RAG: el atlas translingüe la
    encontrará desde cualquier idioma y el PIR pagará a su autor al viajar."""
    try:
        from aisynergix.services.rag_engine import get_rag_engine
        rag = await get_rag_engine()
        await rag.add_contribution(
            text=atlas_index_text(record.get("problem-text", ""),
                                  record.get("solution", "")),
            author_uid=solver,
            language="",  # el matching es translingüe; sin sesgo de idioma
            quality_score=8.0,
            object_name=seal_tx,
            category="servicios",
            node_id=record.get("node-id", ""),
        )
    except Exception as exc:
        logger.warning("atlas: indexado de solución falló: %s", exc)


async def find_similar(text: str, lang: str) -> List[Dict[str, Any]]:
    """Atlas translingüe: conocimiento/soluciones de TODA la red para un
    problema recién reportado. Acredita vistas PIR a los autores mostrados."""
    try:
        from aisynergix.services.rag_engine import get_rag_engine
        rag = await get_rag_engine()
        _ctx, results = await rag.query(text, target_language=lang)
    except Exception as exc:
        logger.warning("atlas: búsqueda falló: %s", exc)
        return []
    matches: List[Dict[str, Any]] = []
    for r in results or []:
        score = float(r.get("score", 0) or 0)
        if score < ATLAS_MIN_SCORE:
            continue
        meta = r.get("metadata", {})
        matches.append({
            "excerpt": (r.get("text", "") or "")[:240],
            "score": round(score, 2),
            "author": meta.get("author_uid", ""),
            "tx": meta.get("object_name", ""),
        })
        if len(matches) >= ATLAS_MAX_MATCHES:
            break
    # PIR: mostrar una solución de otro territorio es una vista del aporte.
    for m in matches:
        if m["author"] and m["tx"] and not m["tx"].startswith("local:"):
            try:
                from aisynergix.services.impact import get_impact_tracker
                asyncio.create_task(
                    get_impact_tracker().record_view(m["tx"], m["author"]))
            except Exception:
                pass
    return matches


async def list_problems(node_id: str, limit: int = 50) -> List[Dict[str, str]]:
    """Problemas del nodo (última versión por problema), con estado local."""
    from aisynergix.services.irys import list_node_problems
    records = await list_node_problems(node_id, limit=limit)
    for r in records:
        pid = r.get("problem-id", "")
        if pid in _resolved:
            r["problem-status"] = _resolved[pid]
    return records


__all__ = [
    "MIN_TEXT_LEN", "MAX_TEXT_LEN", "SOLVED_THRESHOLD", "SOLVER_REWARD_SYNX",
    "ATLAS_MAX_MATCHES", "ATLAS_MIN_SCORE", "REPORTS_DAILY_CAP",
    "reports_today",
    "generate_problem_id", "validate_text", "status_of",
    "can_confirm", "can_confirm_solution", "distinct_confirmers", "is_solved",
    "atlas_index_text",
    "report_problem", "confirm_problem", "propose_solution", "confirm_solution",
    "find_similar", "list_problems",
]
