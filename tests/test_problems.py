"""Pruebas del Atlas de Problemas y Soluciones — pura + orquestación stub."""

import asyncio
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aisynergix.services import problems as pb


# ── Lógica pura ──────────────────────────────────────────────────────────

def test_problem_id_deterministic():
    a = pb.generate_problem_id("n1", "hA", 1000)
    assert a == pb.generate_problem_id("n1", "hA", 1000)
    assert a != pb.generate_problem_id("n1", "hA", 1001)
    assert a.startswith("prb_")


def test_validate_text():
    assert pb.validate_text("  no hay   agua potable ") == "no hay agua potable"
    assert pb.validate_text("corto") is None
    assert pb.validate_text("x" * 301) is None


def test_can_confirm_rules():
    rec = {"problem-id": "p1", "problem-status": "open", "reporter": "hR"}
    assert pb.can_confirm(rec, "hX") == (True, "ok")
    assert pb.can_confirm(rec, "hR") == (False, "own_problem")
    solved = {**rec, "problem-status": "solved"}
    assert pb.can_confirm(solved, "hX") == (False, "closed")


def test_can_confirm_solution_rules():
    rec = {"problem-id": "p1", "problem-status": "solving", "solver": "hS"}
    assert pb.can_confirm_solution(rec, "hX") == (True, "ok")
    assert pb.can_confirm_solution(rec, "hS") == (False, "own_solution")
    open_rec = {**rec, "problem-status": "open"}
    assert pb.can_confirm_solution(open_rec, "hX") == (False, "not_solving")


def test_distinct_confirmers_dedupes():
    confirms = [{"uid-hash": "a"}, {"uid-hash": "a"}, {"uid-hash": "b"}, {}]
    assert pb.distinct_confirmers(confirms) == 2


def test_is_solved_threshold():
    assert pb.is_solved(pb.SOLVED_THRESHOLD) is True
    assert pb.is_solved(pb.SOLVED_THRESHOLD - 1) is False


def test_atlas_index_text():
    txt = pb.atlas_index_text("no hay agua", "instalar filtro comunitario")
    assert "no hay agua" in txt and "instalar filtro comunitario" in txt


# ── Orquestación con stubs ───────────────────────────────────────────────

class _FakeIdentity:
    def __init__(self):
        self.bal = {}

    async def credit_synx(self, h, amt):
        self.bal[h] = round(self.bal.get(h, 0) + amt, 2)
        return amt


def _install(identity, problems, confirms, member=True):
    id_mod = types.ModuleType("aisynergix.bot.identity")
    id_mod.get_identity_manager = lambda: identity
    id_mod._hash_uid = lambda uid: f"hash{uid}"
    sys.modules["aisynergix.bot.identity"] = id_mod

    irys = types.ModuleType("aisynergix.services.irys")

    async def get_node_member(node_id, uid_hash):
        return {"member-status": "active"} if member else None

    async def write_problem(pid, node_id, reporter, text, status,
                            solver="", solution=""):
        problems[pid] = {
            "problem-id": pid, "node-id": node_id, "reporter": reporter,
            "problem-text": text, "problem-status": status,
            "solver": solver, "solution": solution,
        }
        return "tx_" + pid

    async def read_problem(pid):
        return problems.get(pid)

    async def list_node_problems(node_id, limit=200):
        return [p for p in problems.values() if p["node-id"] == node_id]

    async def write_problem_confirm(pid, uid_hash, kind):
        confirms.append({"problem-id": pid, "uid-hash": uid_hash,
                         "confirm-kind": kind})
        return "tx"

    async def list_problem_confirms(pid, kind, limit=1000):
        return [c for c in confirms
                if c["problem-id"] == pid and c["confirm-kind"] == kind]

    irys.get_node_member = get_node_member
    irys.write_problem = write_problem
    irys.read_problem = read_problem
    irys.list_node_problems = list_node_problems
    irys.write_problem_confirm = write_problem_confirm
    irys.list_problem_confirms = list_problem_confirms
    sys.modules["aisynergix.services.irys"] = irys

    # RAG stub para el indexado del atlas (no debe romper el flujo).
    rag_mod = types.ModuleType("aisynergix.services.rag_engine")

    class _Rag:
        async def add_contribution(self, **kw):
            return 1

        async def query(self, text, target_language="es", node_id=""):
            return "", []

    async def get_rag_engine():
        return _Rag()

    rag_mod.get_rag_engine = get_rag_engine
    sys.modules["aisynergix.services.rag_engine"] = rag_mod


TEXT = "no hay agua potable en el sector norte del barrio"
SOLUTION = "se instaló un filtro comunitario financiado entre vecinos"


def _run(coro):
    return asyncio.run(coro)


def test_report_and_confirm_flow():
    pb._resolved.clear()
    ident, problems, confirms = _FakeIdentity(), {}, []
    _install(ident, problems, confirms)
    pid, err = _run(pb.report_problem(1, "n1", TEXT))
    assert err is None and problems[pid]["problem-status"] == "open"
    # el reportero no puede confirmar su propio problema
    _n, err = _run(pb.confirm_problem(1, pid))
    assert err == "own_problem"
    count, err = _run(pb.confirm_problem(2, pid))
    assert err is None and count == 1


def test_report_requires_member_and_text():
    ident = _FakeIdentity()
    _install(ident, {}, [], member=False)
    assert _run(pb.report_problem(1, "n1", TEXT))[1] == "not_member"
    _install(ident, {}, [], member=True)
    assert _run(pb.report_problem(1, "n1", "x"))[1] == "bad_text"


def test_solution_lifecycle_to_solved():
    pb._resolved.clear()
    ident, problems, confirms = _FakeIdentity(), {}, []
    _install(ident, problems, confirms)
    pid, _ = _run(pb.report_problem(1, "n1", TEXT))

    assert _run(pb.propose_solution(2, pid, SOLUTION)) is None
    assert problems[pid]["problem-status"] == "solving"
    assert problems[pid]["solver"] == "hash2"

    async def _confirm_all():
        # el solucionador no puede autoconfirmar
        _s, err = await pb.confirm_solution(2, pid)
        assert err == "own_solution"
        s1, _ = await pb.confirm_solution(3, pid)
        s2, _ = await pb.confirm_solution(4, pid)
        s3, _ = await pb.confirm_solution(5, pid)
        await asyncio.sleep(0)  # deja correr el indexado del atlas
        return s1, s2, s3

    s1, s2, s3 = _run(_confirm_all())
    assert (s1, s2) == ("solving", "solving")
    assert s3 == "solved"
    assert problems[pid]["problem-status"] == "solved"
    # el solucionador cobró la recompensa
    assert ident.bal.get("hash2") == pb.SOLVER_REWARD_SYNX


def test_confirm_and_solve_require_membership():
    """Anti-Sybil: cuentas fuera del nodo no pueden confirmar ni resolver."""
    pb._resolved.clear()
    ident, problems, confirms = _FakeIdentity(), {}, []
    _install(ident, problems, confirms, member=True)
    pid, _ = _run(pb.report_problem(1, "n1", TEXT))
    # Mismo estado, pero ahora nadie es miembro:
    _install(ident, problems, confirms, member=False)
    _c, err = _run(pb.confirm_problem(2, pid))
    assert err == "not_member"
    assert _run(pb.propose_solution(2, pid, SOLUTION)) == "not_member"
    # Volver a miembro, proponer, y confirmar solución como no-miembro:
    _install(ident, problems, confirms, member=True)
    assert _run(pb.propose_solution(2, pid, SOLUTION)) is None
    _install(ident, problems, confirms, member=False)
    _s, err = _run(pb.confirm_solution(3, pid))
    assert err == "not_member"
    assert ident.bal == {}   # nadie cobró


def test_report_daily_cap():
    """Anti-spam: máximo REPORTS_DAILY_CAP reportes por usuario y día."""
    pb._resolved.clear(); pb._daily_reports.clear()
    ident, problems, confirms = _FakeIdentity(), {}, []
    _install(ident, problems, confirms)
    for _ in range(pb.REPORTS_DAILY_CAP):
        pid, err = _run(pb.report_problem(1, "n1", TEXT))
        assert err is None
    _pid, err = _run(pb.report_problem(1, "n1", TEXT))
    assert err == "daily_cap"
    pb._daily_reports.clear()


def test_confirm_idempotent_single_write():
    """Confirmar dos veces no escribe dos DataItems (ahorro de Irys)."""
    pb._resolved.clear()
    ident, problems, confirms = _FakeIdentity(), {}, []
    _install(ident, problems, confirms)
    pid, _ = _run(pb.report_problem(1, "n1", TEXT))
    c1, _ = _run(pb.confirm_problem(2, pid))
    c2, _ = _run(pb.confirm_problem(2, pid))
    assert c1 == 1 and c2 == 1
    writes = [c for c in confirms if c.get("confirm-kind") == "problem"]
    assert len(writes) == 1    # una sola escritura sellada


def test_solution_requires_open_problem():
    pb._resolved.clear()
    ident, problems, confirms = _FakeIdentity(), {}, []
    _install(ident, problems, confirms)
    assert _run(pb.propose_solution(2, "prb_nope", SOLUTION)) == "not_found"
    pid, _ = _run(pb.report_problem(1, "n1", TEXT))
    problems[pid]["problem-status"] = "solved"
    assert _run(pb.propose_solution(2, pid, SOLUTION)) == "already_solved"


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn(); print(f"PASS {fn.__name__}")
        except Exception:
            failed += 1; print(f"FAIL {fn.__name__}"); traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
