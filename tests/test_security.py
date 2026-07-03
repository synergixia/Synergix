"""Pruebas de los fixes de seguridad (montos maliciosos, límites, anti-farming)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aisynergix.services import rewards as rw


# ── #1: parseo seguro de montos (nan / inf / overflow) ───────────────────

def test_parse_amount_rejects_nan_inf():
    for bad in ("nan", "NaN", "inf", "-inf", "Infinity", "1e400", "-inf"):
        assert rw.parse_amount(bad) is None, bad


def test_parse_amount_rejects_nonpositive_and_junk():
    for bad in ("0", "-5", "-0.01", "", "  ", "abc", "10abc", None):
        assert rw.parse_amount(bad) is None, bad


def test_parse_amount_rejects_over_ceiling():
    assert rw.parse_amount("1e15") is not None       # exactamente el techo
    assert rw.parse_amount("1e16") is None            # por encima


def test_parse_amount_accepts_valid():
    assert rw.parse_amount("10") == 10.0
    assert rw.parse_amount("25.5") == 25.5
    assert rw.parse_amount("3,5") == 3.5             # coma decimal
    assert rw.parse_amount("  100  ") == 100.0       # espacios


def test_is_valid_amount():
    assert rw.is_valid_amount(10.0) is True
    assert rw.is_valid_amount(float("nan")) is False
    assert rw.is_valid_amount(float("inf")) is False
    assert rw.is_valid_amount(0.0) is False
    assert rw.is_valid_amount(-1.0) is False
    assert rw.is_valid_amount(rw.MAX_AMOUNT + 1) is False


def test_nan_would_have_bypassed_balance_check():
    # Documenta el bug original: nan rompe la comparación de saldo.
    # 'base < nan' es False → sin el guard, un débito de nan pasaría.
    base = 50.0
    assert (base < float("nan")) is False
    # El guard lo corta:
    assert rw.is_valid_amount(float("nan")) is False


# ── #5: tope de longitud del aporte ──────────────────────────────────────

def test_contribution_length_bounds():
    # Se lee del fuente para no importar manager (arrastra faiss/torch).
    import re
    src = (Path(__file__).resolve().parent.parent / "aisynergix/ai/manager.py").read_text()
    mn = int(re.search(r"MIN_CONTRIBUTION_LENGTH\s*=\s*(\d+)", src).group(1))
    mx = int(re.search(r"MAX_CONTRIBUTION_LENGTH\s*=\s*(\d+)", src).group(1))
    assert 0 < mn < mx <= 50000
    # El flujo debe cortar el aporte demasiado largo con estado 'too_long'.
    assert '"status": "too_long"' in src or "'status': 'too_long'" in src


# ── #2: bono de fundador cobrable una sola vez ───────────────────────────

def test_founder_bonus_flag_roundtrip():
    from aisynergix.bot.identity import UserProfile
    p = UserProfile(uid=123)
    assert p.founder_bonus_claimed is False
    p.founder_bonus_claimed = True
    tags = p.to_tags()
    assert tags["founder_bonus_claimed"] == "true"
    p2 = UserProfile.from_tags(123, tags)
    assert p2.founder_bonus_claimed is True


def test_founder_bonus_default_false_from_tags():
    from aisynergix.bot.identity import UserProfile
    p = UserProfile.from_tags(123, {"points": "0"})
    assert p.founder_bonus_claimed is False


# ── #10: escape de HTML en locales de aporte (regresión de formato) ──────

def test_too_long_locale_present():
    import json
    base = Path(__file__).resolve().parent.parent / "aisynergix/bot/locales"
    for lang in ("es", "en"):
        d = json.loads((base / f"{lang}.json").read_text(encoding="utf-8"))
        assert "contribution_too_long" in d
        assert "{max_length}" in d["contribution_too_long"]


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
