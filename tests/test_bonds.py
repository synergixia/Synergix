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
    assert bd.BOND_AMOUNT == 200000.0
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


def test_locales_have_bond_keys():
    import json
    base = Path(__file__).resolve().parent.parent / "aisynergix/bot/locales"
    keys = {
        "node_bond_insufficient", "node_bond_status_locked", "node_bond_status_unbonding",
        "btn_node_unbond", "node_unbond_started", "node_created", "node_create_ask_name",
    }
    for lang in ("es", "en"):
        d = json.loads((base / f"{lang}.json").read_text(encoding="utf-8"))
        assert not (keys - set(d)), f"{lang}: faltan {keys - set(d)}"
        # node_created ya no menciona bonus, ahora bond:
        assert "{bond}" in d["node_created"]
        assert "{bond}" in d["node_create_ask_name"]


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
