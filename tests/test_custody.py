"""Pruebas de la validación de custodia on-chain (helpers puros, sin red)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# custody importa de trading, que importa web3 de forma perezosa (solo dentro
# de _get_web3). El import a nivel de módulo no toca web3, así que es seguro.
from aisynergix.services import custody as cu


def test_is_valid_address():
    assert cu.is_valid_address("0x" + "a" * 40) is True
    assert cu.is_valid_address("0x" + "0" * 40) is False   # dirección cero
    assert cu.is_valid_address("0x" + "a" * 39) is False   # corta
    assert cu.is_valid_address("0x" + "a" * 41) is False   # larga
    assert cu.is_valid_address("a" * 40) is False          # sin 0x
    assert cu.is_valid_address("0xZZ" + "a" * 38) is False  # no hex
    assert cu.is_valid_address("") is False
    assert cu.is_valid_address("  0x" + "A" * 40 + "  ") is True  # trim + mayúsculas


def test_max_withdrawable_native_reserves_gas():
    assert cu.max_withdrawable_native(1.0, 0.0001) == 0.9999
    # Si el gas supera el saldo, no hay nada retirable (nunca negativo).
    assert cu.max_withdrawable_native(0.00005, 0.0001) == 0.0


GOOD = "0x" + "b" * 40


def test_validate_native_ok():
    err = cu.validate_withdrawal(
        GOOD, 0.5, is_native=True,
        token_balance=0, bnb_balance=1.0, gas_cost_bnb=0.0001,
    )
    assert err is None


def test_validate_native_reserves_gas():
    # Intentar retirar todo el BNB debe fallar: el gas no cabe.
    err = cu.validate_withdrawal(
        GOOD, 1.0, is_native=True,
        token_balance=0, bnb_balance=1.0, gas_cost_bnb=0.0001,
    )
    assert err == "insufficient"


def test_validate_bad_address_and_amount():
    assert cu.validate_withdrawal(
        "0xnope", 1.0, is_native=True, token_balance=0, bnb_balance=5, gas_cost_bnb=0.0001,
    ) == "bad_address"
    for bad in (float("nan"), float("inf"), 0.0, -1.0):
        assert cu.validate_withdrawal(
            GOOD, bad, is_native=True, token_balance=0, bnb_balance=5, gas_cost_bnb=0.0001,
        ) == "bad_amount"


def test_validate_too_small():
    assert cu.validate_withdrawal(
        GOOD, cu.MIN_WITHDRAW_BNB / 2, is_native=True,
        token_balance=0, bnb_balance=5, gas_cost_bnb=0.0001,
    ) == "too_small"
    assert cu.validate_withdrawal(
        GOOD, cu.MIN_WITHDRAW_TOKEN / 2, is_native=False,
        token_balance=1000, bnb_balance=1, gas_cost_bnb=0.0002,
    ) == "too_small"


def test_validate_token_needs_bnb_for_gas():
    # Tiene tokens de sobra pero cero BNB para el gas → no_gas.
    assert cu.validate_withdrawal(
        GOOD, 100.0, is_native=False,
        token_balance=1000, bnb_balance=0.0, gas_cost_bnb=0.0002,
    ) == "no_gas"


def test_validate_token_ok_and_insufficient():
    assert cu.validate_withdrawal(
        GOOD, 100.0, is_native=False,
        token_balance=1000, bnb_balance=0.01, gas_cost_bnb=0.0002,
    ) is None
    assert cu.validate_withdrawal(
        GOOD, 2000.0, is_native=False,
        token_balance=1000, bnb_balance=0.01, gas_cost_bnb=0.0002,
    ) == "insufficient"


def test_locales_have_custody_keys():
    import json
    base = Path(__file__).resolve().parent.parent / "aisynergix/bot/locales"
    keys = {
        "btn_deposit", "btn_withdraw", "deposit_info", "withdraw_choose_asset",
        "withdraw_ask_address", "withdraw_confirm", "withdraw_sent",
        "withdraw_error_no_gas", "withdraw_error_insufficient", "withdraw_error_broadcast",
        # Reset / recuperación de wallet (master key cambiada)
        "withdraw_error_undecryptable", "btn_wallet_reset", "btn_wallet_reset_confirm",
        "wallet_unhealthy", "wallet_reset_confirm", "wallet_reset_done", "wallet_reset_failed",
    }
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
