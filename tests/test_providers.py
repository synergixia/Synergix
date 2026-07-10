"""Pruebas de pay_provider (§6.2): pago SYNX entre usuarios.

Sin red: se inyectan módulos falsos de identidad e Irys en sys.modules para
verificar la orquestación débito→crédito→registro y la reversión.
"""

import asyncio
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class _FakeIdentity:
    """Contabilidad SYNX en memoria con débito acotado a saldo disponible."""

    def __init__(self, balances):
        self.bal = dict(balances)
        self.fail_credit_for = None   # uid_hash cuyo crédito debe fallar

    async def debit_synx(self, uid_hash, amount):
        cur = self.bal.get(uid_hash, 0.0)
        if amount > cur:
            return None
        self.bal[uid_hash] = round(cur - amount, 2)
        return round(amount, 2)

    async def credit_synx(self, uid_hash, amount):
        if uid_hash == self.fail_credit_for:
            return None
        self.bal[uid_hash] = round(self.bal.get(uid_hash, 0.0) + amount, 2)
        return round(amount, 2)


def _install_fakes(identity, providers_list, write_calls, write_raises=False):
    """Inyecta aisynergix.bot.identity y aisynergix.services.irys falsos."""
    id_mod = types.ModuleType("aisynergix.bot.identity")
    id_mod.get_identity_manager = lambda: identity
    id_mod._hash_uid = lambda uid: f"hash{uid}"
    sys.modules["aisynergix.bot.identity"] = id_mod

    irys_mod = types.ModuleType("aisynergix.services.irys")

    async def _get_node_member(node_id, uid_hash):
        return {"member-status": "active"}

    async def _list_node_providers(node_id, limit=500):
        return providers_list

    async def _write_payment(node_id, from_hash, to_hash, amount, memo=""):
        if write_raises:
            raise RuntimeError("sello falló")
        write_calls.append((from_hash, to_hash, amount))
        return "tx123"

    irys_mod.get_node_member = _get_node_member
    irys_mod.list_node_providers = _list_node_providers
    irys_mod.write_payment = _write_payment
    sys.modules["aisynergix.services.irys"] = irys_mod


from aisynergix.services import providers as pv  # noqa: E402

PROVIDER = [{"uid-hash": "hash2", "provider-status": "active"}]


def test_pay_happy_path_moves_synx_and_records():
    ident = _FakeIdentity({"hash1": 500.0, "hash2": 0.0})
    writes = []
    _install_fakes(ident, PROVIDER, writes)
    err = asyncio.run(pv.pay_provider(1, "node", "hash2", 100.0))
    assert err is None
    assert ident.bal["hash1"] == 400.0
    assert ident.bal["hash2"] == 100.0
    assert writes == [("hash1", "hash2", 100.0)]


def test_pay_below_min_amount():
    ident = _FakeIdentity({"hash1": 500.0})
    _install_fakes(ident, PROVIDER, [])
    err = asyncio.run(pv.pay_provider(1, "node", "hash2", 0.5))
    assert err == "bad_amount"


def test_pay_self_rejected():
    ident = _FakeIdentity({"hash1": 500.0})
    _install_fakes(ident, PROVIDER, [])
    err = asyncio.run(pv.pay_provider(1, "node", "hash1", 100.0))
    assert err == "self_payment"


def test_pay_not_a_provider():
    ident = _FakeIdentity({"hash1": 500.0})
    _install_fakes(ident, [], [])   # sin proveedores
    err = asyncio.run(pv.pay_provider(1, "node", "hash2", 100.0))
    assert err == "not_provider"


def test_pay_insufficient_balance():
    ident = _FakeIdentity({"hash1": 50.0, "hash2": 0.0})
    _install_fakes(ident, PROVIDER, [])
    err = asyncio.run(pv.pay_provider(1, "node", "hash2", 100.0))
    assert err == "insufficient"
    assert ident.bal["hash1"] == 50.0   # nada se movió


def test_pay_credit_failure_reverts_payer():
    ident = _FakeIdentity({"hash1": 500.0, "hash2": 0.0})
    ident.fail_credit_for = "hash2"
    _install_fakes(ident, PROVIDER, [])
    err = asyncio.run(pv.pay_provider(1, "node", "hash2", 100.0))
    assert err == "insufficient"
    assert ident.bal["hash1"] == 500.0   # devuelto
    assert ident.bal["hash2"] == 0.0


def test_pay_record_failure_keeps_balances():
    # El sello de auditoría falla, pero los saldos ya se movieron y sellaron.
    ident = _FakeIdentity({"hash1": 500.0, "hash2": 0.0})
    _install_fakes(ident, PROVIDER, [], write_raises=True)
    err = asyncio.run(pv.pay_provider(1, "node", "hash2", 100.0))
    assert err is None
    assert ident.bal["hash1"] == 400.0
    assert ident.bal["hash2"] == 100.0


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
