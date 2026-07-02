"""Pruebas de las bonificaciones SYNX (§5.3) — lógica pura, sin red."""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aisynergix.services import rewards as rw

TODAY = date(2026, 7, 2)


# ── Racha ────────────────────────────────────────────────────────────────

def test_streak_starts_at_one():
    assert rw.update_streak(None, 0, TODAY) == (1, "2026-07-02")


def test_streak_same_day_idempotent():
    assert rw.update_streak("2026-07-02", 4, TODAY) == (4, "2026-07-02")
    # Mínimo 1 aunque el contador guardado fuese 0.
    assert rw.update_streak("2026-07-02", 0, TODAY) == (1, "2026-07-02")


def test_streak_consecutive_day_increments():
    assert rw.update_streak("2026-07-01", 6, TODAY) == (7, "2026-07-02")


def test_streak_gap_resets():
    assert rw.update_streak("2026-06-30", 12, TODAY) == (1, "2026-07-02")


def test_streak_multiplier_threshold():
    assert rw.streak_multiplier_for(6) == 1.0
    assert rw.streak_multiplier_for(7) == 2.0
    assert rw.streak_multiplier_for(30) == 2.0


# ── Primer aporte del día / tema virgen ──────────────────────────────────

def _ts(d: date) -> str:
    from datetime import datetime, timezone
    return str(int(datetime(d.year, d.month, d.day, 12, tzinfo=timezone.utc).timestamp()))


def test_first_of_day():
    yesterday_only = [{"timestamp": _ts(date(2026, 7, 1))}]
    assert rw.is_first_of_day(yesterday_only, TODAY) is True
    with_today = yesterday_only + [{"timestamp": _ts(TODAY)}]
    assert rw.is_first_of_day(with_today, TODAY) is False
    assert rw.is_first_of_day([], TODAY) is True
    assert rw.is_first_of_day([{"timestamp": "corrupto"}], TODAY) is True


def test_virgin_topic():
    aportes = [{"topic": "salud"}, {"category": "empleo"}]
    assert rw.is_virgin_topic(aportes, "educacion") is True
    assert rw.is_virgin_topic(aportes, "salud") is False
    assert rw.is_virgin_topic(aportes, "EMPLEO") is False   # via category, case-insensitive
    assert rw.is_virgin_topic(aportes, "") is False          # sin tema → sin bonus


# ── Idioma poco representado ─────────────────────────────────────────────

def test_underrepresented_language():
    corpus = {"es": 80, "en": 15, "bn": 5}   # total 100
    assert rw.is_underrepresented("bn", corpus) is True     # 5% < 10%
    assert rw.is_underrepresented("en", corpus) is False    # 15%
    assert rw.is_underrepresented("ur", corpus) is True     # 0%
    # Corpus pequeño → sin señal, sin bonus.
    assert rw.is_underrepresented("bn", {"es": 10}) is False


# ── Fórmula completa ─────────────────────────────────────────────────────

def test_compute_synx_no_bonuses():
    b = rw.BonusBreakdown()
    assert rw.compute_synx(25.0, 1.0, b) == 25.0
    assert rw.compute_synx(25.0, 1.5, b) == 37.5


def test_compute_synx_full_stack():
    # base 25 × rango 1.2 × vacío 3 × racha 2 × (1 + 0.20+0.50+0.30) = 360
    b = rw.BonusBreakdown(
        gap_multiplier=3.0, streak_multiplier=2.0,
        first_of_day=True, virgin_topic=True, underrep_lang=True,
    )
    assert rw.compute_synx(25.0, 1.2, b) == 360.0


def test_compute_synx_perfect_score_is_flat_extra():
    b = rw.BonusBreakdown(perfect_score=True)
    # 150 × 1.0 + 100 extra (el extra NO se multiplica)
    assert rw.compute_synx(150.0, 1.0, b) == 250.0
    b2 = rw.BonusBreakdown(perfect_score=True, gap_multiplier=5.0)
    assert rw.compute_synx(150.0, 3.0, b2) == 150.0 * 3.0 * 5.0 + 100.0


def test_active_keys_order_and_content():
    b = rw.BonusBreakdown(
        gap_multiplier=5.0, streak_multiplier=2.0,
        first_of_day=True, virgin_topic=False, underrep_lang=True,
        perfect_score=True,
    )
    assert b.active_keys() == ["gap", "first_day", "underrep_lang", "streak", "perfect"]
    assert rw.BonusBreakdown().active_keys() == []


def test_bonus_keys_have_locales():
    import json
    base = Path(__file__).resolve().parent.parent / "aisynergix/bot/locales"
    keys = {"bonus_first_day", "bonus_virgin_topic", "bonus_underrep_lang",
            "bonus_streak", "bonus_perfect", "synx_bonus_detail", "streak_line"}
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
