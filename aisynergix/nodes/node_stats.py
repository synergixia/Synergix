"""node_stats.py — estadísticas vivas de un nodo (§4.3).

Calcula SYNX circulando, proyectos activos e impactos verificados de un nodo.
Son cálculos con varias lecturas a Irys, así que se cachean en RAM con TTL
corto: la vista de un nodo no cambia segundo a segundo.

Topes de escaneo (miembros/aportes) para acotar el coste en nodos grandes.
"""

import asyncio
import logging
import time
from typing import Any, Dict

logger = logging.getLogger(__name__)

_CACHE_TTL = 300           # 5 min
_MAX_MEMBERS_SCAN = 300    # SYNX circulando: hasta N miembros
_MAX_APORTES_SCAN = 500    # impactos verificados: hasta N aportes

# node_id -> (stats, cached_at)
_cache: Dict[str, Any] = {}


async def _synx_circulating(node_id: str) -> float:
    """Suma el saldo SYNX de los miembros del nodo (aprox., capado)."""
    from aisynergix.services.irys import list_node_members, read_user_tags
    members = await list_node_members(node_id, limit=_MAX_MEMBERS_SCAN)
    if not members:
        return 0.0
    hashes = [m.get("uid-hash", "") for m in members if m.get("uid-hash")]
    tags_list = await asyncio.gather(
        *[read_user_tags(h) for h in hashes], return_exceptions=True
    )
    total = 0.0
    for tags in tags_list:
        if isinstance(tags, dict):
            try:
                total += float(tags.get("synx_balance", 0) or 0)
            except (ValueError, TypeError):
                pass
    return round(total, 2)


async def _active_projects(node_id: str) -> int:
    from aisynergix.services.irys import list_node_projects
    records = await list_node_projects(node_id)
    return sum(1 for r in records
               if r.get("project-status", "active") in ("active", "voting"))


async def _verified_impacts(node_id: str) -> int:
    """Suma los impactos verificados (útiles) de los aportes del nodo (capado)."""
    from aisynergix.services.irys import list_node_aportes, read_impact_counter
    aportes = await list_node_aportes(node_id, limit=_MAX_APORTES_SCAN)
    txs = [a.get("id") or a.get("aporte-tx") or "" for a in aportes]
    txs = [t for t in txs if t]
    if not txs:
        # los aportes vienen como tags; el tx es la clave — usar 'id' si no está
        return 0
    counters = await asyncio.gather(
        *[read_impact_counter(t) for t in txs], return_exceptions=True
    )
    total = 0
    for c in counters:
        if isinstance(c, dict):
            try:
                total += int(c.get("useful", 0) or 0)
            except (ValueError, TypeError):
                pass
    return total


async def node_stats(node_id: str) -> Dict[str, Any]:
    """{synx_circulating, active_projects, verified_impacts} con caché de 5 min."""
    entry = _cache.get(node_id)
    if entry and time.time() - entry[1] < _CACHE_TTL:
        return entry[0]

    synx, projects, impacts = await asyncio.gather(
        _synx_circulating(node_id),
        _active_projects(node_id),
        _verified_impacts(node_id),
        return_exceptions=True,
    )
    stats = {
        "synx_circulating": synx if isinstance(synx, (int, float)) else 0.0,
        "active_projects": projects if isinstance(projects, int) else 0,
        "verified_impacts": impacts if isinstance(impacts, int) else 0,
    }
    _cache[node_id] = (stats, time.time())
    if len(_cache) > 1000:
        _cache.pop(next(iter(_cache)), None)
    return stats


__all__ = ["node_stats"]
