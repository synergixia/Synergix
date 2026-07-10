"""Pruebas del gate anti-Sybil del canje (Fase B) — lógica pura, sin red."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aisynergix.services import redemption as rd


# Contexto base que PASA el gate (para mutarlo en cada test).
def _ctx(**over):
    base = dict(
        verified=True,
        contribution_count=rd.REDEEM_MIN_CONTRIBUTIONS,
        synx_balance=10_000.0,
        user_window_used=0.0,
        address_window_used=0.0,
        address_user_count=0,
        is_new_address_user=True,
    )
    base.update(over)
    return base


def test_happy_path():
    ok, reason, mx = rd.evaluate(rd.REDEEM_MIN_AMOUNT, **_ctx())
    assert ok is True and reason == "ok" and mx > 0


def test_requires_verified_wallet():
    ok, reason, _ = rd.evaluate(rd.REDEEM_MIN_AMOUNT, **_ctx(verified=False))
    assert (ok, reason) == (False, "not_verified")


def test_requires_reputation():
    ok, reason, _ = rd.evaluate(
        rd.REDEEM_MIN_AMOUNT, **_ctx(contribution_count=rd.REDEEM_MIN_CONTRIBUTIONS - 1)
    )
    assert (ok, reason) == (False, "low_reputation")


def test_below_min_and_bad_amounts():
    assert rd.evaluate(rd.REDEEM_MIN_AMOUNT - 1, **_ctx())[1] == "below_min"
    assert rd.evaluate(float("nan"), **_ctx())[1] == "below_min"
    assert rd.evaluate(float("inf"), **_ctx())[1] == "below_min"
    assert rd.evaluate(0, **_ctx())[1] == "below_min"


def test_insufficient_balance():
    amt = rd.REDEEM_MIN_AMOUNT + 1
    ok, reason, _ = rd.evaluate(amt, **_ctx(synx_balance=amt - 0.5))
    assert (ok, reason) == (False, "insufficient_balance")


def test_user_window_cap():
    ok, reason, _ = rd.evaluate(
        rd.REDEEM_MIN_AMOUNT, **_ctx(user_window_used=rd.REDEEM_USER_WINDOW_CAP)
    )
    assert (ok, reason) == (False, "user_cap")


def test_address_window_cap():
    ok, reason, _ = rd.evaluate(
        rd.REDEEM_MIN_AMOUNT, **_ctx(address_window_used=rd.REDEEM_ADDRESS_WINDOW_CAP)
    )
    assert (ok, reason) == (False, "address_cap")


def test_address_shared_sybil_defense():
    # Ya hay el máximo de usuarios distintos usando esa dirección y yo soy nuevo.
    ok, reason, _ = rd.evaluate(
        rd.REDEEM_MIN_AMOUNT,
        **_ctx(address_user_count=rd.REDEEM_ADDRESS_MAX_USERS, is_new_address_user=True),
    )
    assert (ok, reason) == (False, "address_shared")
    # Pero si YA soy uno de los usuarios de esa dirección, no soy nuevo → pasa.
    ok2, _, _ = rd.evaluate(
        rd.REDEEM_MIN_AMOUNT,
        **_ctx(address_user_count=rd.REDEEM_ADDRESS_MAX_USERS - 1, is_new_address_user=False),
    )
    assert ok2 is True


def test_max_allowed_is_min_of_caps():
    # El máximo permitido es el mínimo entre saldo y cupos restantes.
    ok, _, mx = rd.evaluate(
        rd.REDEEM_MIN_AMOUNT,
        **_ctx(synx_balance=100_000, user_window_used=rd.REDEEM_USER_WINDOW_CAP - 300),
    )
    assert ok is True
    assert mx == 300.0    # el cupo de usuario restante manda


def test_window_used_only_counts_recent_and_counted_states():
    now = 1_000_000_000
    recs = [
        {"redeem-status": "paid", "amount": "100", "ts": str(now - 10)},          # cuenta
        {"redeem-status": "requested", "amount": "50", "ts": str(now - 10)},      # cuenta
        {"redeem-status": "rejected", "amount": "999", "ts": str(now - 10)},      # NO
        {"redeem-status": "paid", "amount": "999", "ts": str(now - rd.REDEEM_WINDOW_SECONDS - 1)},  # fuera de ventana
    ]
    assert rd._window_used(recs, now) == 150.0


def test_distinct_address_users():
    recs = [
        {"uid-hash": "a", "redeem-status": "paid"},
        {"uid-hash": "a", "redeem-status": "requested"},
        {"uid-hash": "b", "redeem-status": "paid"},
        {"uid-hash": "c", "redeem-status": "rejected"},   # rechazado no cuenta
        {"uid-hash": "", "redeem-status": "paid"},         # sin uid no cuenta
    ]
    assert rd._distinct_address_users(recs) == {"a", "b"}


def test_redeem_disabled_by_default():
    # Fases B/C: el interruptor viene apagado (no se paga nada).
    assert rd.REDEEM_ENABLED is False


# ── Fase C: tasa y presupuesto de emisión ────────────────────────────────

def test_synergix_for_rate():
    assert rd.synergix_for(1000) == round(1000 * rd.REDEEM_RATE, 4)
    assert rd.synergix_for(0) == 0.0
    assert rd.synergix_for(-5) == 0.0


def test_spent_today_only_paid_and_today():
    day = 1_000_000_000
    recs = [
        {"redeem-status": "paid", "synergix": "300", "ts": str(day + 10)},        # cuenta
        {"redeem-status": "paid", "synergix": "200", "ts": str(day + 20)},        # cuenta
        {"redeem-status": "requested", "synergix": "999", "ts": str(day + 30)},   # NO (no pagado)
        {"redeem-status": "rejected", "synergix": "999", "ts": str(day + 40)},    # NO
        {"redeem-status": "paid", "synergix": "999", "ts": str(day - 10)},        # NO (ayer)
        {"redeem-status": "paid", "synergix": "malo", "ts": str(day + 50)},       # ignora basura
    ]
    assert rd.spent_today(recs, day) == 500.0


def test_budget_bounds_daily_loss():
    # El presupuesto es el techo del gasto diario (propiedad clave del soft-launch).
    day = 1_000_000_000
    at_budget = [{"redeem-status": "paid", "synergix": str(rd.REDEEM_DAILY_BUDGET), "ts": str(day + 1)}]
    assert rd.spent_today(at_budget, day) == rd.REDEEM_DAILY_BUDGET
    # remaining sería 0 → un nuevo canje se rechazaría con 'budget'.


def test_locales_have_redeem_keys():
    import json
    base = Path(__file__).resolve().parent.parent / "aisynergix/bot/locales"
    keys = {"redeem_header", "redeem_soon", "redeem_need_verify",
            "redeem_need_reputation", "redeem_address_shared", "btn_redeem_claim"}
    for lang in ("es", "en"):
        d = json.loads((base / f"{lang}.json").read_text(encoding="utf-8"))
        assert not (keys - set(d)), f"{lang}: faltan {keys - set(d)}"


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
