"""Pruebas del núcleo criptográfico de la wallet custodial (sin red)."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# La clave maestra se lee al importar el módulo: fijarla antes.
os.environ["SYNERGIX_WALLET_MASTER_KEY"] = "clave-maestra-de-prueba-no-usar-en-prod"

from aisynergix.services import wallet as w


def test_enabled_with_master_key():
    assert w.wallet_enabled() is True


def test_disabled_without_master_key():
    saved = w._MASTER_KEY
    try:
        w._MASTER_KEY = ""
        assert w.wallet_enabled() is False
    finally:
        w._MASTER_KEY = saved


def test_password_deterministic_and_per_user():
    p1 = w._derive_password("abc123def456")
    p2 = w._derive_password("abc123def456")
    p3 = w._derive_password("otrousuario1")
    assert p1 == p2                       # determinista (recuperable)
    assert p1 != p3                       # única por usuario
    assert len(p1) == 64                  # HMAC-SHA256 hex


def test_password_depends_on_master_key():
    p_before = w._derive_password("abc123def456")
    saved = w._MASTER_KEY
    try:
        w._MASTER_KEY = "otra-clave-maestra"
        assert w._derive_password("abc123def456") != p_before
    finally:
        w._MASTER_KEY = saved


def test_keystore_roundtrip_and_no_plaintext_leak():
    from eth_account import Account
    import json

    uid_hash = "abc123def456"
    password = w._derive_password(uid_hash)

    acct = Account.create()
    keystore = Account.encrypt(acct.key, password)

    # El keystore serializado NO debe contener la clave privada en claro.
    blob = json.dumps(keystore).lower()
    assert acct.key.hex().lower().removeprefix("0x") not in blob

    # Round-trip: descifra a la misma clave y dirección.
    recovered = Account.decrypt(keystore, password)
    assert bytes(recovered) == bytes(acct.key)
    assert Account.from_key(recovered).address == acct.address

    # Contraseña equivocada → falla, nunca devuelve una clave distinta.
    try:
        Account.decrypt(keystore, w._derive_password("otrousuario1"))
        raised = False
    except ValueError:
        raised = True
    assert raised


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
