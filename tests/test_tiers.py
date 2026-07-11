"""Pruebas de la membresía por tenencia de SYNERGIX (utilidad Capa 1) — lógica pura."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aisynergix.services import tiers as tr


def test_base_tier_below_bronze():
    assert tr.tier_for(0)["key"] == "base"
    assert tr.tier_for(999.99)["key"] == "base"
    assert tr.daily_bonus_for(500) == 0


def test_bronze_boundary_inclusive():
    assert tr.tier_for(1_000)["key"] == "bronce"
    assert tr.daily_bonus_for(1_000) == 3
    assert tr.tier_for(9_999)["key"] == "bronce"


def test_silver_gold_diamond():
    assert tr.tier_for(10_000)["key"] == "plata"
    assert tr.tier_for(49_999)["key"] == "plata"
    assert tr.tier_for(50_000)["key"] == "oro"
    assert tr.tier_for(199_999)["key"] == "oro"
    assert tr.tier_for(200_000)["key"] == "diamante"
    assert tr.tier_for(10_000_000)["key"] == "diamante"


def test_daily_bonus_monotonic():
    bonuses = [tr.daily_bonus_for(b) for b in (0, 1_000, 10_000, 50_000, 200_000)]
    assert bonuses == [0, 3, 7, 15, 30]
    assert bonuses == sorted(bonuses)


def test_tier_for_handles_garbage():
    assert tr.tier_for(float("nan"))["key"] == "base"
    assert tr.tier_for(float("inf"))["key"] == "base"
    assert tr.tier_for("no-numero")["key"] == "base"
    assert tr.tier_for(None)["key"] == "base"


def test_next_tier_progression():
    assert tr.next_tier(0)["key"] == "bronce"
    assert tr.next_tier(1_000)["key"] == "plata"
    assert tr.next_tier(50_000)["key"] == "diamante"
    assert tr.next_tier(200_000) is None    # ya en el máximo
    assert tr.next_tier(500_000) is None


def test_tiers_sorted_desc_and_complete():
    mins = [t["min"] for t in tr.TIERS]
    assert mins == sorted(mins, reverse=True)   # de mayor a menor
    for t in tr.TIERS:
        assert {"key", "name", "min", "daily_bonus", "priority"} <= set(t)


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
