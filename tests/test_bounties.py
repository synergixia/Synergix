"""Pruebas de los Bounties de Conocimiento — lógica pura + orquestación stub."""

import asyncio
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aisynergix.services import bounties as bt

NOW = 1_000_000_000


def _rec(**over):
    base = {
        "bounty-id": "bty_x", "node-id": "n1", "topic": "salud",
        "sponsor": "hashS", "pool": "100.00", "reward": "10.00",
        "per-user": "3", "deadline": str(NOW + 86400),
        "bounty-status": "open", "paid-count": "0",
    }
    base.update({k: str(v) for k, v in over.items()})
    return base


# ── Lógica pura ──────────────────────────────────────────────────────────

def test_is_open_states():
    assert bt.is_open(_rec(), NOW) is True
    assert bt.is_open(_rec(**{"bounty-status": "closed"}), NOW) is False
    assert bt.is_open(_rec(deadline=NOW - 1), NOW) is False       # vencido
    assert bt.is_open(_rec(deadline=0), NOW) is True              # sin plazo


def test_remaining_pool_and_refund():
    r = _rec(pool=100, reward=10, **{"paid-count": 4})
    assert bt.remaining_pool(r) == 60.0
    assert bt.refund_amount(r) == 60.0
    # nunca negativo
    assert bt.remaining_pool(_rec(pool=10, reward=10, **{"paid-count": 5})) == 0.0


def test_max_recipients():
    assert bt.max_recipients(_rec(pool=100, reward=10)) == 10
    assert bt.max_recipients(_rec(pool=95, reward=10)) == 9
    assert bt.max_recipients(_rec(reward=0)) == 0


def test_can_pay_happy():
    ok, reason = bt.can_pay(_rec(), user_paid_count=0, now=NOW)
    assert (ok, reason) == (True, "ok")


def test_can_pay_closed_and_expired():
    assert bt.can_pay(_rec(**{"bounty-status": "closed"}), 0, NOW)[1] == "closed"
    assert bt.can_pay(_rec(deadline=NOW - 1), 0, NOW)[1] == "expired"


def test_can_pay_exhausted():
    # pool 100, reward 10, ya pagados 10 → 0 restante
    r = _rec(pool=100, reward=10, **{"paid-count": 10})
    assert bt.can_pay(r, 0, NOW) == (False, "exhausted")


def test_can_pay_user_cap():
    r = _rec(**{"per-user": 2})
    assert bt.can_pay(r, user_paid_count=2, now=NOW) == (False, "user_cap")
    assert bt.can_pay(r, user_paid_count=1, now=NOW)[0] is True
    # per-user 0 = sin tope
    assert bt.can_pay(_rec(**{"per-user": 0}), 999, NOW)[0] is True


def test_validate_params():
    assert bt.validate_params(100, 10, 30) is None
    assert bt.validate_params(10, 5, 30) == "pool_too_small"
    assert bt.validate_params(100, 1, 30) == "bad_reward"       # < REWARD_MIN
    assert bt.validate_params(100, 200, 30) == "bad_reward"     # reward > pool
    assert bt.validate_params(100, 10, 0) == "bad_duration"
    assert bt.validate_params(100, 10, 999) == "bad_duration"


def test_bounty_id_deterministic_and_prefixed():
    a = bt.generate_bounty_id("h", "n1", "salud", NOW)
    b = bt.generate_bounty_id("h", "n1", "salud", NOW)
    c = bt.generate_bounty_id("h", "n1", "salud", NOW + 1)
    assert a == b and a != c and a.startswith("bty_")


# ── Orquestación con identidad/Irys falsos ───────────────────────────────

class _FakeIdentity:
    def __init__(self, bal):
        self.bal = dict(bal)

    async def debit_synx(self, h, amt):
        if amt > self.bal.get(h, 0):
            return None
        self.bal[h] = round(self.bal.get(h, 0) - amt, 2)
        return round(amt, 2)

    async def credit_synx(self, h, amt):
        self.bal[h] = round(self.bal.get(h, 0) + amt, 2)
        return round(amt, 2)


def _install(identity, bounty_store, claim_store, member=True):
    id_mod = types.ModuleType("aisynergix.bot.identity")
    id_mod.get_identity_manager = lambda: identity
    id_mod._hash_uid = lambda uid: f"hash{uid}"
    sys.modules["aisynergix.bot.identity"] = id_mod

    irys = types.ModuleType("aisynergix.services.irys")

    async def get_node_member(node_id, uid_hash):
        return {"member-status": "active"} if member else None

    async def write_bounty(bid, node_id, topic, sponsor, pool, reward, per_user,
                           deadline, status, paid_count=0):
        bounty_store[bid] = {
            "bounty-id": bid, "node-id": node_id, "topic": topic,
            "sponsor": sponsor, "pool": f"{pool:.2f}", "reward": f"{reward:.2f}",
            "per-user": str(per_user), "deadline": str(deadline),
            "bounty-status": status, "paid-count": str(paid_count),
        }
        return "tx_" + bid

    async def read_bounty(bid):
        return bounty_store.get(bid)

    async def list_node_bounties(node_id, limit=200):
        return [b for b in bounty_store.values() if b["node-id"] == node_id]

    async def write_bounty_claim(bid, author, tx, amount):
        claim_store.append({"bounty-id": bid, "author-uid": author,
                            "aporte-tx": tx, "amount": f"{amount:.2f}"})
        return "claim"

    async def list_bounty_claims(bid, limit=2000):
        return [c for c in claim_store if c["bounty-id"] == bid]

    irys.get_node_member = get_node_member
    irys.write_bounty = write_bounty
    irys.read_bounty = read_bounty
    irys.list_node_bounties = list_node_bounties
    irys.write_bounty_claim = write_bounty_claim
    irys.list_bounty_claims = list_bounty_claims
    sys.modules["aisynergix.services.irys"] = irys


def test_create_bounty_escrows_pool():
    ident = _FakeIdentity({"hash1": 500.0})
    store, claims = {}, []
    _install(ident, store, claims)
    bid, err = asyncio.run(bt.create_bounty(1, "n1", "Salud", 100.0, reward=10.0))
    assert err is None and bid
    assert ident.bal["hash1"] == 400.0        # pool en escrow
    assert store[bid]["topic"] == "salud"      # normalizado a minúsculas


def test_create_bounty_not_member():
    ident = _FakeIdentity({"hash1": 500.0})
    _install(ident, {}, [], member=False)
    bid, err = asyncio.run(bt.create_bounty(1, "n1", "salud", 100.0))
    assert err == "not_member" and bid is None
    assert ident.bal["hash1"] == 500.0        # sin débito


def test_create_bounty_insufficient():
    ident = _FakeIdentity({"hash1": 40.0})
    _install(ident, {}, [])
    bid, err = asyncio.run(bt.create_bounty(1, "n1", "salud", 100.0))
    assert err == "insufficient" and bid is None


def test_reward_flow_pays_and_exhausts():
    # pool 50, reward 25 → paga 2 aportes y se cierra.
    ident = _FakeIdentity({"hash9": 100.0})
    store, claims = {}, []
    _install(ident, store, claims)
    bid, err = asyncio.run(bt.create_bounty(9, "n1", "salud", 50.0, reward=25.0, per_user=5))
    assert err is None

    a = asyncio.run(bt.reward_aporte("n1", "salud", "hashA", "tx1", now=NOW))
    b = asyncio.run(bt.reward_aporte("n1", "salud", "hashB", "tx2", now=NOW))
    c = asyncio.run(bt.reward_aporte("n1", "salud", "hashC", "tx3", now=NOW))
    assert a == 25.0 and b == 25.0
    assert c is None                          # pool agotado
    assert ident.bal["hashA"] == 25.0 and ident.bal["hashB"] == 25.0
    assert store[bid]["bounty-status"] == "closed"


def test_reward_idempotent_per_aporte():
    ident = _FakeIdentity({"hash9": 100.0})
    store, claims = {}, []
    _install(ident, store, claims)
    asyncio.run(bt.create_bounty(9, "n1", "salud", 100.0, reward=10.0))
    first = asyncio.run(bt.reward_aporte("n1", "salud", "hashA", "tx1", now=NOW))
    dup = asyncio.run(bt.reward_aporte("n1", "salud", "hashA", "tx1", now=NOW))
    assert first == 10.0 and dup is None       # no doble pago
    assert ident.bal["hashA"] == 10.0


def test_reward_respects_user_cap():
    ident = _FakeIdentity({"hash9": 200.0})
    store, claims = {}, []
    _install(ident, store, claims)
    asyncio.run(bt.create_bounty(9, "n1", "salud", 100.0, reward=10.0, per_user=2))
    r1 = asyncio.run(bt.reward_aporte("n1", "salud", "hashA", "tx1", now=NOW))
    r2 = asyncio.run(bt.reward_aporte("n1", "salud", "hashA", "tx2", now=NOW))
    r3 = asyncio.run(bt.reward_aporte("n1", "salud", "hashA", "tx3", now=NOW))
    assert r1 == 10.0 and r2 == 10.0 and r3 is None   # tope 2 por usuario


def test_reward_no_bounty_for_topic():
    ident = _FakeIdentity({"hash9": 100.0})
    _install(ident, {}, [])
    asyncio.run(bt.create_bounty(9, "n1", "salud", 100.0, reward=10.0))
    # tema distinto → no paga
    assert asyncio.run(bt.reward_aporte("n1", "empleo", "hashA", "tx1", now=NOW)) is None
    # aporte local → no paga
    assert asyncio.run(bt.reward_aporte("n1", "salud", "hashA", "local:x", now=NOW)) is None


def test_list_bounties_lazy_expiration_refunds():
    """Listar bounties cierra los vencidos y reembolsa al patrocinador."""
    ident = _FakeIdentity({"hash9": 100.0})
    store, claims = {}, []
    _install(ident, store, claims)
    bid, _ = asyncio.run(bt.create_bounty(9, "n1", "salud", 100.0, reward=10.0, days=1))
    # Forzar el vencimiento en el registro sellado:
    store[bid]["deadline"] = str(NOW)  # muy en el pasado respecto a ahora
    items = asyncio.run(bt.list_bounties("n1"))
    assert store[bid]["bounty-status"] == "expired"
    assert ident.bal["hash9"] == 100.0        # 100 debitados, 100 reembolsados
    entry = next(i for i in items if i["bounty-id"] == bid)
    assert entry["remaining"] == 0.0 and entry["open"] is False


def test_expire_refunds_remaining():
    ident = _FakeIdentity({"hash9": 100.0})
    store, claims = {}, []
    _install(ident, store, claims)
    bid, _ = asyncio.run(bt.create_bounty(9, "n1", "salud", 100.0, reward=10.0, days=1))
    asyncio.run(bt.reward_aporte("n1", "salud", "hashA", "tx1", now=NOW))  # paga 10
    # vencido: justo después del deadline real que fijó create_bounty.
    later = int(store[bid]["deadline"]) + 10
    refund = asyncio.run(bt.expire_and_refund(bid, now=later))
    assert refund == 90.0
    assert ident.bal["hash9"] == 90.0         # 100 debitado, 90 devuelto
    assert store[bid]["bounty-status"] == "expired"


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
