"""Pruebas de la Economía Viva (Fase 2): proveedores, proyectos y oráculos.

Solo lógica pura — la E/S de Irys se prueba con los patrones ya cubiertos
en test_impact.py (misma familia de wrappers).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aisynergix.services import projects as pj
from aisynergix.services import oracle as oc
from aisynergix.services import providers as pv


# ── Financiamiento colectivo ─────────────────────────────────────────────

def test_resolve_outcome_majority():
    assert pj.resolve_outcome({"a": True, "b": True, "c": False}) == "release"
    assert pj.resolve_outcome({"a": False, "b": False, "c": True}) == "refund"
    assert pj.resolve_outcome({"a": True}) == "release"


def test_resolve_outcome_tie_and_empty_refund():
    # Empate 50/50 no supera el 51 % → el dinero vuelve (conservador).
    assert pj.resolve_outcome({"a": True, "b": False}) == "refund"
    assert pj.resolve_outcome({}) == "refund"


def test_funds_by_user_sums_and_ignores_garbage():
    records = [
        {"uid-hash": "u1", "amount": "10.00"},
        {"uid-hash": "u1", "amount": "5.50"},
        {"uid-hash": "u2", "amount": "3"},
        {"uid-hash": "u3", "amount": "corrupto"},   # ignorado
        {"uid-hash": "",   "amount": "99"},          # sin uid → ignorado
        {"uid-hash": "u4", "amount": "-5"},          # negativo → ignorado
    ]
    totals = pj.funds_by_user(records)
    assert totals == {"u1": 15.5, "u2": 3.0}


def test_project_id_shape():
    pid = pj.generate_project_id("abc123", "Escuela del barrio")
    assert pid.startswith("proj_") and len(pid) == len("proj_") + 12


def test_validate_evidence_mandatory():
    # verificación obligatoria: la evidencia debe ser sustancial
    assert pj.validate_evidence("  Se   instaló el filtro y hay fotos ") == \
        "Se instaló el filtro y hay fotos"
    assert pj.validate_evidence("hecho") is None            # muy corta
    assert pj.validate_evidence("x" * (pj.MAX_EVIDENCE_LEN + 1)) is None
    assert pj.validate_evidence("") is None
    assert pj.validate_evidence(None) is None


def test_project_constants_sane():
    assert pj.GOAL_MIN_SYNX > 0
    assert pj.FUND_MIN_SYNX > 0
    assert pj.VOTING_WINDOW_H == 72   # §6.2: ventana de 72 horas


# ── Jueces Oráculos ──────────────────────────────────────────────────────

def test_tally_majority():
    assert oc.tally({"a": True, "b": True, "c": False}) is True
    assert oc.tally({"a": False, "b": False, "c": True}) is False


def test_tally_tie_and_empty_reject():
    assert oc.tally({"a": True, "b": False}) is False
    assert oc.tally({}) is False


def test_wrong_streak_progression():
    # Dos errores acumulan sin multa; el tercero multa y reinicia.
    s, penalty = oc.wrong_streak_after(0, was_wrong=True)
    assert (s, penalty) == (1, False)
    s, penalty = oc.wrong_streak_after(1, was_wrong=True)
    assert (s, penalty) == (2, False)
    s, penalty = oc.wrong_streak_after(2, was_wrong=True)
    assert (s, penalty) == (0, True)   # multa + reinicio


def test_wrong_streak_reset_on_correct_vote():
    assert oc.wrong_streak_after(2, was_wrong=False) == (0, False)


def test_oracle_constants_match_design():
    # §6.2 uso 5 + §5.4: stake 500, +15 correcto, −50 sistemático, 3 votos, 24 h.
    assert oc.STAKE_MIN_SYNX == 500.0
    assert oc.VOTE_REWARD_SYNX == 15.0
    assert oc.PENALTY_SYNX == 50.0
    assert oc.REQUIRED_VOTES == 3
    assert oc.REVIEW_WINDOW_H == 24
    assert oc.REVIEW_MIN_SCORE == 8.0
    # ×3 total (§5.2) = base ya pagada + 2× extra.
    assert oc.ORACLE_EXTRA_MULT == 2.0


# ── Proveedores ──────────────────────────────────────────────────────────

def test_provider_categories_sane():
    assert "otros" in pv.PROVIDER_CATEGORIES
    assert all(icon for icon in pv.PROVIDER_CATEGORIES.values())
    assert 0 < pv.MIN_DESC_LEN < pv.MAX_DESC_LEN


# ── Locales ──────────────────────────────────────────────────────────────

def test_locales_have_phase2_keys():
    import json
    base = Path(__file__).resolve().parent.parent / "aisynergix/bot/locales"
    keys = {
        "need_active_node", "providers_header", "btn_provider_register",
        "provider_registered", "projects_header", "btn_project_create",
        "project_view", "project_released", "project_refunded",
        "oracle_menu_intro", "oracle_stake_ok", "oracle_review_pending",
        "oracle_resolved_approved",
    }
    keys |= {f"prov_{k}" for k in pv.PROVIDER_CATEGORIES}
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
