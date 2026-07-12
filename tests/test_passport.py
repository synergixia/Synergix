"""Pruebas de la credencial Proof-of-Knowledge del Passport — lógica pura."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aisynergix.services import passport as pp


def _aporte(category, score=None, topic=None):
    tags = {"category": category}
    if topic is not None:
        tags["topic"] = topic
    if score is not None:
        tags["quality-score"] = str(score)
    return {"path": "tx", "tags": tags}


def test_domain_breakdown_counts_and_avg():
    aportes = [
        _aporte("salud", 8), _aporte("salud", 6), _aporte("empleo", 7),
    ]
    dom = pp.domain_breakdown(aportes)
    salud = next(d for d in dom if d["domain"] == "salud")
    empleo = next(d for d in dom if d["domain"] == "empleo")
    assert salud["contributions"] == 2 and salud["avg_score"] == 7.0
    assert empleo["contributions"] == 1 and empleo["avg_score"] == 7.0


def test_domain_breakdown_sorted_desc():
    aportes = [_aporte("a", 5), _aporte("b", 5), _aporte("b", 5), _aporte("b", 5)]
    dom = pp.domain_breakdown(aportes)
    assert [d["domain"] for d in dom] == ["b", "a"]   # b tiene más aportes


def test_domain_breakdown_uses_topic_fallback():
    # sin category, cae a topic
    a = {"tags": {"topic": "seguridad", "quality-score": "9"}}
    dom = pp.domain_breakdown([a])
    assert dom[0]["domain"] == "seguridad" and dom[0]["contributions"] == 1


def test_domain_breakdown_handles_missing_score():
    dom = pp.domain_breakdown([_aporte("salud"), _aporte("salud", 8)])
    salud = dom[0]
    assert salud["contributions"] == 2 and salud["avg_score"] == 8.0  # solo 1 con score


def test_domain_breakdown_ignores_empty_domain():
    assert pp.domain_breakdown([{"tags": {"quality-score": "8"}}]) == []


def test_bounties_summary():
    claims = [{"amount": "10.00"}, {"amount": "25.50"}, {"amount": "bad"}]
    s = pp.bounties_summary(claims)
    assert s["fulfilled"] == 3 and s["synx_earned"] == 35.5


def test_bounties_summary_empty():
    assert pp.bounties_summary([]) == {"fulfilled": 0, "synx_earned": 0.0}


def test_version_bumped():
    assert pp.PASSPORT_VERSION == "1.2"


def test_academy_credentials_filters_and_sorts():
    creds = [
        {"domain": "salud", "level": "1", "lessons-passed": "3"},
        {"domain": "empleo", "level": "2", "lessons-passed": "12"},
        {"domain": "economia", "level": "0", "lessons-passed": "1"},  # sin nivel → fuera
        {"domain": "", "level": "3", "lessons-passed": "30"},         # sin dominio → fuera
    ]
    out = pp.academy_credentials(creds)
    assert [c["domain"] for c in out] == ["empleo", "salud"]   # nivel desc
    assert out[0] == {"domain": "empleo", "level": 2, "lessons": 12}


def test_academy_credentials_ignores_garbage():
    assert pp.academy_credentials([{"domain": "x", "level": "abc"}]) == []
    assert pp.academy_credentials([]) == []


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
