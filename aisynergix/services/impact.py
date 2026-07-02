"""impact.py — Prueba de Impacto Real (PIR, diseño §7).

Cada aporte tiene un contador de impacto vivo que crece para siempre:

  * vista      → la memoria inmortal usó el aporte en una respuesta
  * útil (✅)  → un usuario confirmó que la respuesta le sirvió
  * referencia → un aporte nuevo se construyó sobre este

Regalías perpetuas (§7.2): cada 100 impactos verificados (útiles), el autor
recibe 5 SYNX automáticos y el pago queda registrado públicamente en Irys
(``data-type=impact-royalty``).  Un aporte de 2026 puede seguir generando
SYNX en 2030.

Coste de escritura: las confirmaciones ✅ y las referencias sellan el
contador inmediatamente (son eventos raros y valiosos); las vistas se
acumulan en RAM y se sellan en lotes de ``VIEW_FLUSH_THRESHOLD`` para no
multiplicar los uploads a Irys (una vista sin sellar como mucho se pierde
en un restart — es la métrica menos crítica).

El acceso a Irys/identidad pasa por wrappers de módulo (``_irys_read`` …)
para que la lógica sea testeable sin red.
"""

import asyncio
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# §7.2 — parámetros de la regalía perpetua.
ROYALTY_BLOCK_SIZE = 100         # impactos verificados por bloque
ROYALTY_SYNX_PER_BLOCK = 5.0     # SYNX pagados por bloque

# §7.3 — regalía por referencia: % de la recompensa del aporte nuevo.
REFERENCE_ROYALTY_PCT = 0.10
# Similitud mínima para considerar que un aporte nuevo "cita" a uno
# existente (el techo lo pone NEAR_CLONE_THRESHOLD=0.92 del RAG: por encima
# es un duplicado, no una referencia).
REFERENCE_MIN_SCORE = 0.70

# Vistas acumuladas en RAM antes de sellar el contador en Irys.
VIEW_FLUSH_THRESHOLD = 10

_COUNTER_FIELDS = ("views", "useful", "references", "royalty_blocks_paid")


def royalty_blocks_due(useful_count: int, paid_blocks: int) -> int:
    """Bloques de 100 impactos verificados aún no pagados."""
    return max(0, useful_count // ROYALTY_BLOCK_SIZE - max(0, paid_blocks))


def is_reference_match(score: float, same_author: bool) -> bool:
    """True si un resultado RAG cuenta como 'cita' de un aporte existente.

    El propio autor no se auto-referencia (anti-gaming básico).
    """
    return (not same_author) and score >= REFERENCE_MIN_SCORE


# ── Wrappers de E/S (monkeypatcheables en tests) ─────────────────────────

async def _irys_read(aporte_tx: str) -> Optional[Dict[str, str]]:
    from aisynergix.services.irys import read_impact_counter
    return await read_impact_counter(aporte_tx)


async def _irys_write(aporte_tx: str, author_hash: str, counts: Dict[str, Any]) -> None:
    from aisynergix.services.irys import write_impact_counter
    await write_impact_counter(aporte_tx, author_hash, counts)


async def _irys_royalty(aporte_tx: str, author_hash: str, impacts: int, synx: float) -> None:
    from aisynergix.services.irys import write_impact_royalty
    await write_impact_royalty(aporte_tx, author_hash, impacts, synx)


async def _credit_author(author_hash: str, synx: float) -> Optional[float]:
    from aisynergix.bot.identity import get_identity_manager
    return await get_identity_manager().credit_synx(author_hash, synx)


class ImpactTracker:
    """Contadores de impacto por aporte, con ledger RAM anti-lag de Irys."""

    def __init__(self):
        self._locks: Dict[str, asyncio.Lock] = {}
        # Última versión sellada (o leída) por aporte — evita que el lag de
        # indexación de Irys (5-30 s) haga retroceder un contador.
        self._ledger: Dict[str, Dict[str, Any]] = {}
        # Vistas aún no selladas.
        self._pending_views: Dict[str, int] = {}

    def _lock_for(self, tx: str) -> asyncio.Lock:
        lock = self._locks.get(tx)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[tx] = lock
        return lock

    async def _load(self, aporte_tx: str) -> Dict[str, Any]:
        """Contador vigente: máximo campo a campo entre Irys y el ledger."""
        counts: Dict[str, Any] = {f: 0 for f in _COUNTER_FIELDS}
        counts["synx_paid"] = 0.0
        try:
            tags = await _irys_read(aporte_tx)
        except Exception:
            tags = None
        if tags:
            for f in _COUNTER_FIELDS:
                try:
                    counts[f] = int(tags.get(f.replace("_", "-"), 0) or 0)
                except (ValueError, TypeError):
                    pass
            try:
                counts["synx_paid"] = float(tags.get("synx-paid", 0) or 0)
            except (ValueError, TypeError):
                pass
        led = self._ledger.get(aporte_tx)
        if led:
            for f in _COUNTER_FIELDS:
                counts[f] = max(counts[f], led.get(f, 0))
            counts["synx_paid"] = max(counts["synx_paid"], led.get("synx_paid", 0.0))
        return counts

    async def _seal(self, aporte_tx: str, author_hash: str, counts: Dict[str, Any]) -> None:
        await _irys_write(aporte_tx, author_hash, counts)
        self._ledger[aporte_tx] = dict(counts)
        if len(self._ledger) > 5000:
            self._ledger.pop(next(iter(self._ledger)), None)

    def _drain_pending_views(self, aporte_tx: str, counts: Dict[str, Any]) -> None:
        pending = self._pending_views.pop(aporte_tx, 0)
        if pending:
            counts["views"] = counts.get("views", 0) + pending

    async def record_view(self, aporte_tx: str, author_hash: str) -> None:
        """+1 vista. Se acumula en RAM y se sella cada VIEW_FLUSH_THRESHOLD."""
        if not aporte_tx or aporte_tx.startswith("local:"):
            return
        async with self._lock_for(aporte_tx):
            self._pending_views[aporte_tx] = self._pending_views.get(aporte_tx, 0) + 1
            if self._pending_views[aporte_tx] < VIEW_FLUSH_THRESHOLD:
                return
            try:
                counts = await self._load(aporte_tx)
                self._drain_pending_views(aporte_tx, counts)
                await self._seal(aporte_tx, author_hash, counts)
            except Exception as exc:
                logger.warning("record_view seal %s falló: %s", aporte_tx[:12], exc)

    async def record_useful(self, aporte_tx: str, author_hash: str) -> Optional[int]:
        """+1 impacto verificado. Sella siempre y paga regalías al cruzar 100.

        Retorna el total de impactos verificados, o None si falla.
        """
        if not aporte_tx or aporte_tx.startswith("local:"):
            return None
        async with self._lock_for(aporte_tx):
            try:
                counts = await self._load(aporte_tx)
                counts["useful"] = counts.get("useful", 0) + 1
                self._drain_pending_views(aporte_tx, counts)

                due = royalty_blocks_due(counts["useful"], counts["royalty_blocks_paid"])
                if due > 0 and author_hash:
                    synx = round(due * ROYALTY_SYNX_PER_BLOCK, 2)
                    paid = await _credit_author(author_hash, synx)
                    if paid is not None:
                        counts["royalty_blocks_paid"] += due
                        counts["synx_paid"] = round(counts.get("synx_paid", 0.0) + synx, 2)
                        try:
                            await _irys_royalty(aporte_tx, author_hash, counts["useful"], synx)
                        except Exception as exc:
                            # El pago ya está en el perfil; el registro público
                            # es best-effort y se loguea si falla.
                            logger.warning("registro de regalía %s falló: %s", aporte_tx[:12], exc)

                await self._seal(aporte_tx, author_hash, counts)
                return counts["useful"]
            except Exception as exc:
                logger.warning("record_useful %s falló: %s", aporte_tx[:12], exc)
                return None

    async def record_reference(self, aporte_tx: str, author_hash: str) -> None:
        """+1 referencia (otro aporte se construyó sobre este). Sella siempre."""
        if not aporte_tx or aporte_tx.startswith("local:"):
            return
        async with self._lock_for(aporte_tx):
            try:
                counts = await self._load(aporte_tx)
                counts["references"] = counts.get("references", 0) + 1
                self._drain_pending_views(aporte_tx, counts)
                await self._seal(aporte_tx, author_hash, counts)
            except Exception as exc:
                logger.warning("record_reference %s falló: %s", aporte_tx[:12], exc)


_tracker: Optional[ImpactTracker] = None


def get_impact_tracker() -> ImpactTracker:
    global _tracker
    if _tracker is None:
        _tracker = ImpactTracker()
    return _tracker


__all__ = [
    "ImpactTracker",
    "get_impact_tracker",
    "royalty_blocks_due",
    "is_reference_match",
    "ROYALTY_BLOCK_SIZE",
    "ROYALTY_SYNX_PER_BLOCK",
    "REFERENCE_ROYALTY_PCT",
    "REFERENCE_MIN_SCORE",
    "VIEW_FLUSH_THRESHOLD",
]
