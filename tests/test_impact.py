"""Pruebas de la Prueba de Impacto Real (§7) — sin red, E/S falsa."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aisynergix.services import impact as im


# ── Lógica pura ──────────────────────────────────────────────────────────

def test_royalty_blocks_due():
    assert im.royalty_blocks_due(0, 0) == 0
    assert im.royalty_blocks_due(99, 0) == 0
    assert im.royalty_blocks_due(100, 0) == 1
    assert im.royalty_blocks_due(199, 1) == 0
    assert im.royalty_blocks_due(200, 1) == 1
    assert im.royalty_blocks_due(500, 2) == 3
    assert im.royalty_blocks_due(50, 5) == 0   # nunca negativo


def test_is_reference_match():
    assert im.is_reference_match(0.75, same_author=False) is True
    assert im.is_reference_match(0.75, same_author=True) is False   # sin auto-cita
    assert im.is_reference_match(0.69, same_author=False) is False  # bajo umbral
    assert im.is_reference_match(im.REFERENCE_MIN_SCORE, False) is True


# ── Tracker con E/S falsa ────────────────────────────────────────────────

class FakeIO:
    """Sustituye los wrappers de módulo y registra todas las llamadas."""

    def __init__(self, irys_counter=None, credit_ok=True):
        self.irys_counter = irys_counter    # lo que "hay" en Irys
        self.credit_ok = credit_ok
        self.sealed = []                    # (tx, author, counts)
        self.royalties = []                 # (tx, author, impacts, synx)
        self.credits = []                   # (author, synx)

    def install(self):
        async def _read(tx):
            return self.irys_counter

        async def _write(tx, author, counts):
            self.sealed.append((tx, author, dict(counts)))

        async def _royalty(tx, author, impacts, synx):
            self.royalties.append((tx, author, impacts, synx))

        async def _credit(author, synx):
            self.credits.append((author, synx))
            return 100.0 if self.credit_ok else None

        im._irys_read = _read
        im._irys_write = _write
        im._irys_royalty = _royalty
        im._credit_author = _credit


def _run(coro):
    return asyncio.run(coro)


def test_useful_increments_and_seals():
    io = FakeIO(); io.install()
    tr = im.ImpactTracker()
    total = _run(tr.record_useful("tx1", "author1"))
    assert total == 1
    assert len(io.sealed) == 1
    _, _, counts = io.sealed[0]
    assert counts["useful"] == 1
    assert io.credits == []          # lejos del bloque de 100: sin regalía


def test_useful_crossing_100_pays_royalty():
    # Irys dice 99 útiles pagados 0 bloques → el impacto nº 100 paga 5 SYNX.
    io = FakeIO(irys_counter={"useful": "99", "views": "40",
                              "references": "2", "royalty-blocks-paid": "0",
                              "synx-paid": "0"})
    io.install()
    tr = im.ImpactTracker()
    total = _run(tr.record_useful("tx1", "author1"))
    assert total == 100
    assert io.credits == [("author1", 5.0)]
    assert len(io.royalties) == 1
    _, _, counts = io.sealed[0]
    assert counts["royalty_blocks_paid"] == 1
    assert counts["synx_paid"] == 5.0


def test_useful_multiple_blocks_due():
    # Contador atrasado (199 útiles, 0 bloques pagados) → al llegar a 200
    # se deben 2 bloques → 10 SYNX de una vez.
    io = FakeIO(irys_counter={"useful": "199", "royalty-blocks-paid": "0"})
    io.install()
    tr = im.ImpactTracker()
    _run(tr.record_useful("tx1", "author1"))
    assert io.credits == [("author1", 10.0)]
    assert io.sealed[0][2]["royalty_blocks_paid"] == 2


def test_failed_credit_does_not_mark_paid():
    # Si el pago al autor falla, el bloque queda pendiente para reintentarse.
    io = FakeIO(irys_counter={"useful": "99"}, credit_ok=False)
    io.install()
    tr = im.ImpactTracker()
    _run(tr.record_useful("tx1", "author1"))
    assert io.royalties == []
    assert io.sealed[0][2]["royalty_blocks_paid"] == 0


def test_view_batching():
    io = FakeIO(); io.install()
    tr = im.ImpactTracker()

    async def go():
        for _ in range(im.VIEW_FLUSH_THRESHOLD - 1):
            await tr.record_view("tx1", "author1")
        assert io.sealed == []                       # aún en RAM
        await tr.record_view("tx1", "author1")       # nº 10 → sella
    _run(go())
    assert len(io.sealed) == 1
    assert io.sealed[0][2]["views"] == im.VIEW_FLUSH_THRESHOLD


def test_pending_views_drain_on_useful():
    io = FakeIO(); io.install()
    tr = im.ImpactTracker()

    async def go():
        await tr.record_view("tx1", "author1")
        await tr.record_view("tx1", "author1")
        await tr.record_useful("tx1", "author1")
    _run(go())
    assert len(io.sealed) == 1
    counts = io.sealed[0][2]
    assert counts["views"] == 2 and counts["useful"] == 1


def test_ledger_beats_stale_irys_read():
    # Tras sellar useful=1, una lectura vieja de Irys (useful=0 por lag de
    # indexación) no puede hacer retroceder el contador.
    io = FakeIO(irys_counter=None); io.install()
    tr = im.ImpactTracker()

    async def go():
        await tr.record_useful("tx1", "author1")     # sella useful=1
        io.irys_counter = {"useful": "0"}            # Irys aún no indexó
        return await tr.record_useful("tx1", "author1")
    total = _run(go())
    assert total == 2


def test_local_and_empty_tx_ignored():
    io = FakeIO(); io.install()
    tr = im.ImpactTracker()

    async def go():
        assert await tr.record_useful("local:abc_123", "a") is None
        assert await tr.record_useful("", "a") is None
        await tr.record_view("local:abc_123", "a")
        await tr.record_reference("", "a")
    _run(go())
    assert io.sealed == [] and io.credits == []


def test_reference_increments():
    io = FakeIO(irys_counter={"references": "4"}); io.install()
    tr = im.ImpactTracker()
    _run(tr.record_reference("tx1", "author1"))
    assert io.sealed[0][2]["references"] == 5


def test_locales_have_pir_keys():
    import json
    base = Path(__file__).resolve().parent.parent / "aisynergix/bot/locales"
    for lang in ("es", "en"):
        d = json.loads((base / f"{lang}.json").read_text(encoding="utf-8"))
        for key in ("btn_useful", "useful_thanks", "useful_expired"):
            assert key in d, f"{lang}: falta {key}"


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
