"""Pruebas del Juez 3 anti-gaming (§5.4) — lógica pura + detector en RAM."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aisynergix.ai import anti_gaming as ag


def test_jaccard_bounds():
    assert ag.jaccard(set(), {"a"}) == 0.0
    assert ag.jaccard({"a", "b"}, {"a", "b"}) == 1.0
    assert ag.jaccard({"a", "b"}, {"b", "c"}) == 1 / 3


def test_repetition_ratio():
    assert ag.repetition_ratio("uno") == 0.0                 # <2 tokens
    assert ag.repetition_ratio("uno dos tres cuatro") == 0.0  # todos únicos
    # 6 tokens, 2 únicos → 1 - 2/6
    assert abs(ag.repetition_ratio("spam spam spam spam ham ham") - (1 - 2 / 6)) < 1e-9


def test_signals_template_flagged():
    flagged, reason = ag.evaluate_signals(1000, 0.9, 0.0)
    assert flagged and reason == "template"


def test_signals_filler_flagged():
    flagged, reason = ag.evaluate_signals(1000, 0.0, 0.7)
    assert flagged and reason == "filler"


def test_signals_burst_needs_similarity():
    # Rápido pero contenido distinto → NO se marca.
    assert ag.evaluate_signals(5, 0.1, 0.0) == (False, "ok")
    # Rápido Y parecido → ráfaga.
    flagged, reason = ag.evaluate_signals(5, 0.7, 0.0)
    assert flagged and reason == "burst"


def test_signals_no_previous_aporte_not_burst():
    assert ag.evaluate_signals(-1.0, 0.5, 0.0) == (False, "ok")


def test_signals_clean_passes():
    assert ag.evaluate_signals(120, 0.2, 0.1) == (False, "ok")


def test_judge_first_aporte_ok():
    j = ag.AntiGamingJudge()
    flagged, _ = j.check("u1", "una reflexión original sobre el agua y la vida", now=1000)
    assert flagged is False


def test_judge_detects_template_repeat():
    j = ag.AntiGamingJudge()
    txt = "el agua limpia es esencial para la salud de la comunidad rural"
    j.check("u1", txt, now=1000)
    # Mismo molde minutos después (no ráfaga) → plantilla.
    flagged, reason = j.check("u1", txt + " hoy", now=5000)
    assert flagged and reason == "template"


def test_judge_burst_flagged():
    j = ag.AntiGamingJudge()
    a = "ideas sobre energia solar para el nodo comunitario del sur"
    b = "ideas sobre energia solar para el nodo comunitario del norte"
    j.check("u1", a, now=1000)
    flagged, reason = j.check("u1", b, now=1005)   # 5 s después y parecido
    assert flagged and reason == "burst"


def test_judge_separate_users_independent():
    j = ag.AntiGamingJudge()
    txt = "reflexion sobre la importancia de compartir conocimiento libre"
    j.check("u1", txt, now=1000)
    # Otro usuario con el mismo texto: no tiene historial → no se marca.
    flagged, _ = j.check("u2", txt, now=1001)
    assert flagged is False


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
