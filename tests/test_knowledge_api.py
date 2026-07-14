"""Pruebas de la API de Conocimiento que paga a humanos (③) — pura + stub."""

import asyncio
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aisynergix.services import knowledge_api as ka


# ── Contabilidad pura ────────────────────────────────────────────────────

def test_hash_key_stable_and_prefixed():
    a = ka.hash_api_key("secret")
    assert a == ka.hash_api_key("secret") and a.startswith("ak_")
    assert ka.hash_api_key("other") != a


def test_distribute_splits_author_share():
    protocol, per = ka.distribute(1.0, ["hA", "hB"])
    # 70% a autores → 0.70 repartido entre 2 = 0.35 c/u; 30% protocolo
    assert per == {"hA": 0.35, "hB": 0.35}
    assert protocol == 0.30
    assert round(protocol + sum(per.values()), 2) == 1.0


def test_distribute_dedupes_authors():
    _protocol, per = ka.distribute(1.0, ["hA", "hA", "hB"])
    assert set(per) == {"hA", "hB"}          # autor repetido cuenta una vez


def test_distribute_no_authors_all_protocol():
    protocol, per = ka.distribute(1.0, [])
    assert per == {} and protocol == 1.0


def test_distribute_conserves_total():
    # 3 autores: reparto no divisible exacto → el protocolo absorbe el resto.
    protocol, per = ka.distribute(1.0, ["a", "b", "c"])
    assert round(protocol + sum(per.values()), 2) == 1.0


def test_encode_decode_roundtrip():
    per = {"hA": 0.35, "hB": 0.35}
    enc = ka.encode_distribution(per)
    dec = ka.decode_distribution(enc)
    assert dec == {"hA": 0.35, "hB": 0.35}


def test_decode_ignores_garbage():
    assert ka.decode_distribution("hA:0.5,broken,:0.1,hB:x") == {"hA": 0.5, "hB": 0.0}


def test_price_flat():
    assert ka.price_for(5) == 1.0 and ka.price_for(0) == 1.0


# ── Cobro a la key (stub Irys) ───────────────────────────────────────────

def _install_key(store):
    irys = types.ModuleType("aisynergix.services.irys")

    async def read_api_key(kh):
        return store.get(kh)

    async def write_api_key(kh, owner, balance, status="active"):
        store[kh] = {"key-hash": kh, "owner": owner,
                     "balance": f"{balance:.4f}", "key-status": status}
        return "tx"

    irys.read_api_key = read_api_key
    irys.write_api_key = write_api_key
    sys.modules["aisynergix.services.irys"] = irys


def test_verify_and_charge_deducts():
    kh = ka.hash_api_key("k1")
    store = {kh: {"key-hash": kh, "owner": "o", "balance": "10.0000",
                  "key-status": "active"}}
    _install_key(store)
    rec, err = asyncio.run(ka.verify_and_charge("k1", 1.0))
    assert err is None
    assert float(store[kh]["balance"]) == 9.0


def test_verify_and_charge_insufficient():
    kh = ka.hash_api_key("k1")
    store = {kh: {"key-hash": kh, "owner": "o", "balance": "0.5000",
                  "key-status": "active"}}
    _install_key(store)
    rec, err = asyncio.run(ka.verify_and_charge("k1", 1.0))
    assert err == "insufficient_credit" and rec is None
    assert float(store[kh]["balance"]) == 0.5      # sin cargo


def test_verify_and_charge_bad_and_inactive():
    _install_key({})
    assert asyncio.run(ka.verify_and_charge("nope", 1.0))[1] == "bad_key"
    kh = ka.hash_api_key("k2")
    _install_key({kh: {"key-hash": kh, "balance": "10", "key-status": "revoked"}})
    assert asyncio.run(ka.verify_and_charge("k2", 1.0))[1] == "inactive"


# ── Liquidación idempotente (stub identidad + Irys) ──────────────────────

class _FakeIdentity:
    def __init__(self):
        self.bal = {}

    async def credit_synx(self, h, amt):
        self.bal[h] = round(self.bal.get(h, 0) + amt, 4)
        return amt


def _install_settle(identity, events):
    id_mod = types.ModuleType("aisynergix.bot.identity")
    id_mod.get_identity_manager = lambda: identity
    sys.modules["aisynergix.bot.identity"] = id_mod

    irys = types.ModuleType("aisynergix.services.irys")

    async def list_pending_api_usage(limit=200):
        return [e for e in events if e.get("usage-status") == "pending"]

    async def write_api_usage(uid, kh, price, authors, status="pending"):
        for e in events:
            if e.get("usage-id") == uid:
                e["usage-status"] = status
        return "tx"

    irys.list_pending_api_usage = list_pending_api_usage
    irys.write_api_usage = write_api_usage
    sys.modules["aisynergix.services.irys"] = irys


def test_settlement_credits_authors_and_marks_settled():
    ident = _FakeIdentity()
    events = [{
        "usage-id": "use_1", "key-hash": "ak_x", "price": "1.0000",
        "authors": "hA:0.3500,hB:0.3500", "usage-status": "pending",
    }]
    _install_settle(ident, events)
    res = asyncio.run(ka.settle_pending_usage())
    assert res["events"] == 1
    assert ident.bal == {"hA": 0.35, "hB": 0.35}
    assert events[0]["usage-status"] == "settled"


def test_settlement_idempotent():
    ident = _FakeIdentity()
    events = [{
        "usage-id": "use_1", "key-hash": "ak_x", "price": "1.0000",
        "authors": "hA:0.7000", "usage-status": "pending",
    }]
    _install_settle(ident, events)
    asyncio.run(ka.settle_pending_usage())
    # segunda pasada: ya está settled → no vuelve a pagar
    asyncio.run(ka.settle_pending_usage())
    assert ident.bal == {"hA": 0.7}


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
