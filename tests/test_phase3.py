"""Pruebas de la Fase 3: Passport, Agente y gobernanza (lógica pura)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aisynergix.services import agent as ag
from aisynergix.services import governance as gv
from aisynergix.services import passport as pp


# ── Agente: Nivel 2 (proveedores) ────────────────────────────────────────

def test_provider_intent_detects_with_seeking_verb():
    assert ag.detect_provider_intent("necesito un electricista de confianza") == "electricidad"
    assert ag.detect_provider_intent("¿conocen algún plomero por aquí?") == "plomeria"
    assert ag.detect_provider_intent("I need a good mechanic") == "mecanica"
    assert ag.detect_provider_intent("looking for a teacher for my kids") == "educacion"


def test_provider_intent_requires_seeking_context():
    # Mencionar la profesión sin buscar no dispara al agente.
    assert ag.detect_provider_intent("mi tío es electricista") is None
    assert ag.detect_provider_intent("los médicos trabajan mucho") is None
    assert ag.detect_provider_intent("") is None
    # Buscar sin profesión conocida tampoco.
    assert ag.detect_provider_intent("necesito unas vacaciones") is None


# ── Agente: Nivel 3 (donaciones) ─────────────────────────────────────────

def test_donation_intent_parses_amount():
    assert ag.detect_donation_intent("dona 10 SYNX al proyecto de la escuela") == 10.0
    assert ag.detect_donation_intent("quiero donar 25.5 synx") == 25.5
    assert ag.detect_donation_intent("donate 100 SYNX to the school project") == 100.0
    assert ag.detect_donation_intent("aporta 3,5 synx al pozo") == 3.5


def test_donation_intent_rejects_non_donations():
    assert ag.detect_donation_intent("¿cuánto vale un SYNX?") is None
    assert ag.detect_donation_intent("gané 50 SYNX ayer") is None
    assert ag.detect_donation_intent("dona cero synx") is None


def test_match_project_single_active_wins():
    projects = [
        {"project-id": "p1", "title": "Escuela del barrio", "project-status": "active"},
        {"project-id": "p2", "title": "Pozo de agua", "project-status": "completed"},
    ]
    assert ag.match_project(projects, "dona 10 synx")["project-id"] == "p1"


def test_match_project_by_title_tokens():
    projects = [
        {"project-id": "p1", "title": "Escuela del barrio", "project-status": "active"},
        {"project-id": "p2", "title": "Pozo de agua", "project-status": "active"},
    ]
    m = ag.match_project(projects, "dona 10 synx al proyecto de la escuela")
    assert m and m["project-id"] == "p1"
    m = ag.match_project(projects, "donate 5 synx to the agua well")
    assert m and m["project-id"] == "p2"


def test_match_project_ambiguous_returns_none():
    projects = [
        {"project-id": "p1", "title": "Escuela norte", "project-status": "active"},
        {"project-id": "p2", "title": "Escuela sur", "project-status": "active"},
    ]
    # "escuela" matchea ambos con el mismo peso → ambiguo, no adivinar.
    assert ag.match_project(projects, "dona 10 synx a la escuela") is None
    # Sin mención y con 2+ activos → None.
    assert ag.match_project(projects, "dona 10 synx") is None


def test_gap_worthy():
    assert ag.is_gap_worthy("¿Dónde consigo agua potable en el barrio?") is True
    assert ag.is_gap_worthy("hola") is False
    assert ag.is_gap_worthy("   ") is False


# ── Gobernanza ───────────────────────────────────────────────────────────

def test_vote_weight_floor():
    assert gv.vote_weight(0.0) == 0
    assert gv.vote_weight(0.9) == 0     # menos de 1 SYNX → sin voto
    assert gv.vote_weight(1.0) == 1
    assert gv.vote_weight(150.7) == 150


def test_tally_weighted_66_pct():
    # 200 sí / 100 no → 66.7 % ≥ 66 % → aprobada.
    outcome, yes, total = gv.tally_weighted([(True, 200), (False, 100)])
    assert (outcome, yes, total) == ("approved", 200, 300)
    # 65 % → rechazada.
    outcome, _, _ = gv.tally_weighted([(True, 65), (False, 35)])
    assert outcome == "rejected"
    # Exactamente 66 % → aprobada (umbral inclusivo).
    outcome, _, _ = gv.tally_weighted([(True, 66), (False, 34)])
    assert outcome == "approved"


def test_tally_weighted_empty_and_zero_weight():
    assert gv.tally_weighted([]) == ("rejected", 0, 0)
    assert gv.tally_weighted([(True, 0)]) == ("rejected", 0, 0)


def test_proposal_id_shape():
    pid = gv.generate_proposal_id("abc123", "Subir el umbral de calidad a 7")
    assert pid.startswith("prop_") and len(pid) == len("prop_") + 12


# ── Passport ─────────────────────────────────────────────────────────────

def test_aggregate_impacts():
    counters = [
        {"views": "10", "useful": "3", "references": "1", "synx-paid": "5.00"},
        {"views": "7", "useful": "120", "references": "0", "synx-paid": "10.5"},
        {"views": "corrupto"},
    ]
    total = pp.aggregate_impacts(counters)
    assert total == {"views": 17, "useful": 123, "references": 1, "royalties_synx": 15.5}


def test_oracle_accuracy():
    assert pp.oracle_accuracy({"votes-total": "10", "votes-correct": "8"}) == 0.8
    assert pp.oracle_accuracy({"votes-total": "0", "votes-correct": "0"}) is None
    assert pp.oracle_accuracy(None) is None
    # Nunca por encima de 1 aunque los datos vengan raros.
    assert pp.oracle_accuracy({"votes-total": "2", "votes-correct": "5"}) == 1.0


def test_average_score():
    aportes = [
        {"tags": {"quality-score": "8.0"}},
        {"tags": {"quality-score": "6.5"}},
        {"tags": {}},
    ]
    assert pp.average_score(aportes) == 7.25
    assert pp.average_score([]) is None


def test_locales_have_phase3_keys():
    import json
    base = Path(__file__).resolve().parent.parent / "aisynergix/bot/locales"
    keys = {
        "passport_view", "passport_building", "proposals_header",
        "btn_proposal_create", "proposal_created", "proposal_view",
        "btn_vote_yes", "agent_providers_found", "agent_donation_confirm",
        "agent_donation_done",
    }
    for lang in ("es", "en"):
        d = json.loads((base / f"{lang}.json").read_text(encoding="utf-8"))
        missing = keys - set(d)
        assert not missing, f"{lang}: faltan {missing}"


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
