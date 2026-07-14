"""Regresión: /start debe reconocer a un usuario que regresa.

Bug: is_new se calculaba con puntos/usos (que solo crecen al contribuir), así
que un usuario que solo hizo /start (o que nunca aportó) veía la bienvenida de
NUEVO en cada arranque.  El fix decide por EXISTENCIA en Irys (base de datos
única) vía IdentityManager.has_stored_profile → irys.profile_exists.

Estas pruebas inyectan un módulo irys falso (httpx no está en el entorno de
test) y verifican la delegación y la lógica welcome vs welcome_back.
"""

import asyncio
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aisynergix.bot import identity


def _install_fake_irys(exists_value):
    """Coloca un aisynergix.services.irys falso con profile_exists fijo."""
    mod = types.ModuleType("aisynergix.services.irys")

    async def profile_exists(uid_hash):  # noqa: ANN001
        mod.calls.append(uid_hash)
        return exists_value

    mod.calls = []
    mod.profile_exists = profile_exists
    sys.modules["aisynergix.services.irys"] = mod
    return mod


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_has_stored_profile_true_for_returning_user():
    fake = _install_fake_irys(True)
    mgr = identity.IdentityManager()
    assert _run(mgr.has_stored_profile(123456)) is True
    # se consultó Irys con el uid HASHEADO (nunca el uid crudo)
    assert fake.calls and fake.calls[0] == identity._hash_uid(123456)
    assert str(123456) not in fake.calls[0]


def test_has_stored_profile_false_for_new_user():
    _install_fake_irys(False)
    mgr = identity.IdentityManager()
    assert _run(mgr.has_stored_profile(987654)) is False


def test_welcome_decision_uses_existence_not_activity():
    # Simula la decisión de cmd_start: is_new = not has_stored_profile.
    # Un usuario SIN actividad (0 puntos/usos) pero YA existente en Irys
    # debe tratarse como que REGRESA (welcome_back), no como nuevo.
    _install_fake_irys(True)
    mgr = identity.IdentityManager()
    profile = identity.UserProfile(uid=555)
    assert profile.points == 0 and profile.total_uses_count == 0  # sin actividad
    is_new = not _run(mgr.has_stored_profile(555))
    assert is_new is False  # existe en Irys → welcome_back


def test_welcome_decision_new_user_is_new():
    _install_fake_irys(False)
    mgr = identity.IdentityManager()
    is_new = not _run(mgr.has_stored_profile(777))
    assert is_new is True  # no existe en Irys → welcome (nuevo)


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
