"""Pruebas de Synergix Academy (learn-to-earn) — progresión pura + stub."""

import asyncio
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aisynergix.services import academy as ac


# ── Progresión pura ──────────────────────────────────────────────────────

def test_level_thresholds():
    assert ac.level_for(0) == 0
    assert ac.level_for(2) == 0
    assert ac.level_for(3) == 1
    assert ac.level_for(9) == 1
    assert ac.level_for(10) == 2
    assert ac.level_for(25) == 3
    assert ac.level_for(50) == 4
    assert ac.level_for(100) == 5
    assert ac.level_for(1000) == 5


def test_next_level_at():
    assert ac.next_level_at(0) == 3
    assert ac.next_level_at(3) == 10
    assert ac.next_level_at(99) == 100
    assert ac.next_level_at(100) is None


def test_is_passing():
    assert ac.is_passing(5.0) is True
    assert ac.is_passing(4.9) is False
    assert ac.is_passing(float("nan")) is False
    assert ac.is_passing("no-num") is False


def test_daily_cap_counting():
    ac._daily.clear()
    uid, today = 42, "2026-07-12"
    assert ac.lessons_today(uid, today) == 0
    assert ac.can_take_lesson(uid, today) is True
    for _ in range(ac.DAILY_LESSON_CAP):
        ac._note_lesson(uid, today)
    assert ac.lessons_today(uid, today) == ac.DAILY_LESSON_CAP
    assert ac.can_take_lesson(uid, today) is False
    # nuevo día → contador reinicia
    assert ac.lessons_today(uid, "2026-07-13") == 0
    assert ac.can_take_lesson(uid, "2026-07-13") is True
    ac._daily.clear()


# ── Orquestación con stubs ───────────────────────────────────────────────

class _FakeIdentity:
    def __init__(self):
        self.bal = {}

    async def credit_synx(self, h, amt):
        self.bal[h] = round(self.bal.get(h, 0) + amt, 2)
        return amt


class _FakeTracker:
    def __init__(self):
        self.useful = []

    async def record_useful(self, tx, author):
        self.useful.append((tx, author))
        return 1


def _install(identity, creds, results, tracker, score=8.0, feedback="bien"):
    id_mod = types.ModuleType("aisynergix.bot.identity")
    id_mod.get_identity_manager = lambda: identity
    id_mod._hash_uid = lambda uid: f"hash{uid}"
    sys.modules["aisynergix.bot.identity"] = id_mod

    irys = types.ModuleType("aisynergix.services.irys")

    async def read_credential(uid_hash, domain):
        return creds.get((uid_hash, domain))

    async def write_credential(uid_hash, domain, level, lessons_passed):
        creds[(uid_hash, domain)] = {
            "uid-hash": uid_hash, "domain": domain,
            "level": str(level), "lessons-passed": str(lessons_passed),
        }
        return "tx"

    async def write_lesson_result(uid_hash, domain, score, passed):
        results.append({"uid": uid_hash, "domain": domain,
                        "score": score, "passed": passed})
        return "tx"

    irys.read_credential = read_credential
    irys.write_credential = write_credential
    irys.write_lesson_result = write_lesson_result
    sys.modules["aisynergix.services.irys"] = irys

    local_ia = types.ModuleType("aisynergix.ai.local_ia")

    class _Judge:
        async def evaluate(self, text):
            return {"quality_score": score, "approved": score >= 5.0,
                    "constructive_feedback": feedback}

    local_ia.get_judge = lambda: _Judge()
    sys.modules["aisynergix.ai.local_ia"] = local_ia

    impact = types.ModuleType("aisynergix.services.impact")
    impact.get_impact_tracker = lambda: tracker
    sys.modules["aisynergix.services.impact"] = impact


ANSWER = "El agua limpia previene enfermedades y sostiene la vida de la comunidad."


def _run(coro):
    return asyncio.run(coro)


def test_submit_pass_rewards_and_seals():
    ac._daily.clear()
    ident, creds, results, tracker = _FakeIdentity(), {}, [], _FakeTracker()
    _install(ident, creds, results, tracker, score=8.0)
    out = _run(ac.submit_answer(1, "salud", ANSWER, [("hashA", "tx1")]))
    assert out["status"] == "pass" and out["score"] == 8.0
    assert ident.bal["hash1"] == ac.LESSON_REWARD_SYNX
    assert creds[("hash1", "salud")]["lessons-passed"] == "1"
    assert results and results[0]["passed"] is True
    ac._daily.clear()


def test_submit_fail_no_reward_counts_attempt():
    ac._daily.clear()
    ident, creds, results, tracker = _FakeIdentity(), {}, [], _FakeTracker()
    _install(ident, creds, results, tracker, score=3.0, feedback="profundiza")
    out = _run(ac.submit_answer(1, "salud", ANSWER))
    assert out["status"] == "fail" and out["feedback"] == "profundiza"
    assert ident.bal == {}                       # sin recompensa
    assert ("hash1", "salud") not in creds       # sin credencial
    assert ac.lessons_today(1, __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc).date().isoformat()) == 1
    ac._daily.clear()


def test_submit_too_short():
    ident, creds, results, tracker = _FakeIdentity(), {}, [], _FakeTracker()
    _install(ident, creds, results, tracker)
    out = _run(ac.submit_answer(1, "salud", "corto"))
    assert out["status"] == "too_short"


def test_submit_daily_cap_blocks():
    ac._daily.clear()
    ident, creds, results, tracker = _FakeIdentity(), {}, [], _FakeTracker()
    _install(ident, creds, results, tracker, score=9.0)
    today = __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc).date().isoformat()
    for _ in range(ac.DAILY_LESSON_CAP):
        ac._note_lesson(1, today)
    out = _run(ac.submit_answer(1, "salud", ANSWER))
    assert out["status"] == "daily_cap"
    assert ident.bal == {}
    ac._daily.clear()


def test_levelup_on_third_lesson():
    ac._daily.clear()
    ident, creds, results, tracker = _FakeIdentity(), {}, [], _FakeTracker()
    _install(ident, creds, results, tracker, score=7.0)
    # 2 lecciones previas ya selladas
    creds[("hash1", "salud")] = {"uid-hash": "hash1", "domain": "salud",
                                 "level": "0", "lessons-passed": "2"}
    out = _run(ac.submit_answer(1, "salud", ANSWER))
    assert out["lessons"] == 3 and out["level"] == 1 and out["leveled_up"] is True
    assert out["next_at"] == 10
    ac._daily.clear()


def test_pir_credits_cited_authors_not_self():
    ac._daily.clear()
    ident, creds, results, tracker = _FakeIdentity(), {}, [], _FakeTracker()
    _install(ident, creds, results, tracker, score=8.0)

    async def _go():
        out = await ac.submit_answer(
            1, "salud", ANSWER,
            [("hashA", "tx1"), ("hash1", "tx2"), ("hashB", "tx3")],
        )
        # dejar correr las tareas PIR en segundo plano
        await asyncio.sleep(0)
        return out

    out = _run(_go())
    assert out["status"] == "pass"
    # hash1 (el propio alumno) queda excluido del PIR
    assert ("tx1", "hashA") in tracker.useful
    assert ("tx3", "hashB") in tracker.useful
    assert all(a != "hash1" for _tx, a in tracker.useful)
    ac._daily.clear()


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
