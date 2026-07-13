"""Pruebas de la lógica de bonds de nodo (Fase A) — lógica pura, sin red."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aisynergix.services import bonds as bd

NOW = 1_000_000_000


def _bond(status, amount, until=0):
    return {"bond-status": status, "amount": str(amount), "unbond-until": str(until)}


def test_is_bond_active_states():
    assert bd.is_bond_active("locked", 0, NOW) is True
    assert bd.is_bond_active("slashed", 0, NOW) is True
    assert bd.is_bond_active("released", 0, NOW) is False
    # unbonding: bloqueado hasta que expira la ventana
    assert bd.is_bond_active("unbonding", NOW + 100, NOW) is True   # aún corriendo
    assert bd.is_bond_active("unbonding", NOW - 100, NOW) is False  # ya expiró


def test_sum_locked_counts_only_active():
    bonds = [
        _bond("locked", 200000),                    # cuenta
        _bond("unbonding", 200000, NOW + 500),      # cuenta (aún bloqueado)
        _bond("unbonding", 200000, NOW - 500),      # NO (ya liberado)
        _bond("released", 200000),                  # NO
        _bond("slashed", 200000),                   # cuenta (forfeit, bloqueado)
    ]
    assert bd.sum_locked(bonds, NOW) == 600000.0


def test_sum_locked_ignores_garbage():
    bonds = [_bond("locked", "corrupto"), {"bond-status": "locked"}, _bond("locked", 100)]
    assert bd.sum_locked(bonds, NOW) == 100.0


def test_sum_locked_empty():
    assert bd.sum_locked([], NOW) == 0.0


def test_bond_constants():
    assert bd.BOND_AMOUNT == 20000.0
    assert bd.UNBOND_DAYS == 7
    assert bd.UNBOND_SECONDS == 7 * 86400


def test_available_math_via_sum():
    # disponible = balance - bloqueado (la resta se hace en available_synergix,
    # pero validamos la parte pura: bloqueado correcto).
    bonds = [_bond("locked", 200000)]
    locked = bd.sum_locked(bonds, NOW)
    balance = 250000.0
    assert max(0.0, balance - locked) == 50000.0
    # Con dos nodos bloqueados no queda disponible:
    bonds2 = [_bond("locked", 200000), _bond("locked", 200000)]
    assert max(0.0, balance - bd.sum_locked(bonds2, NOW)) == 0.0


def test_node_bond_is_20k_anti_spam():
    """El bond anti-spam de nodo es 20 000 SYNERGIX y el flujo lo menciona."""
    assert bd.BOND_AMOUNT == 20000.0
    import json
    base = Path(__file__).resolve().parent.parent / "aisynergix/bot/locales"
    for lang in ("es", "en"):
        d = json.loads((base / f"{lang}.json").read_text(encoding="utf-8"))
        assert "{bond}" in d["node_created"]
        assert "{bond}" in d["node_create_ask_name"]


def test_coverage_stale_flag():
    """Un tema con aportes viejos (30+ días) se marca desactualizado."""
    from aisynergix.nodes import knowledge_map as km
    now = 1_000_000_000
    old = now - km.STALE_SECONDS - 100
    fresh = now - 100
    cov = km.coverage_from_aportes(
        ["salud", "empleo"],
        [{"topic": "salud", "timestamp": str(old)},
         {"topic": "empleo", "timestamp": str(fresh)}],
        now=now,
    )
    assert cov["salud"]["stale"] is True
    assert cov["empleo"]["stale"] is False
    # Sin aportes → no stale.
    cov2 = km.coverage_from_aportes(["salud"], [], now=now)
    assert cov2["salud"]["stale"] is False


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
