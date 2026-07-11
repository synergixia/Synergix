"""knowledge_map.py — mapa de cobertura y vacíos de conocimiento de un nodo.

Cada nodo tiene hasta 5 temas prioritarios.  La cobertura de un tema mide
cuántos aportes de calidad existen para él respecto a un objetivo.  Cuando un
tema está poco cubierto se considera un "vacío", y llenarlo multiplica la
recompensa — el incentivo central del diseño:

  * Vacío activo  (< 60 % de cobertura)  → recompensa ×3
  * Vacío crítico (< 40 % de cobertura)  → recompensa ×5

La función de cobertura es deliberadamente simple y determinista para que sea
testeable sin red: cuenta aportes por tema contra ``TARGET_APORTES_PER_TOPIC``.
"""

import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Un tema con aportes pero cuyo más reciente tiene 30+ días se marca como
# desactualizado (§4.5: genera alerta al nodo).
STALE_DAYS = 30
STALE_SECONDS = STALE_DAYS * 86_400

# Aportes necesarios para considerar un tema 100 % cubierto.
TARGET_APORTES_PER_TOPIC = 20

# Umbrales de cobertura (%) → (nivel, multiplicador de recompensa).
GAP_CRITICAL_PCT = 40.0   # < 40 %  → crítico  ×5
GAP_ACTIVE_PCT = 60.0     # < 60 %  → activo   ×3
MULT_CRITICAL = 5.0
MULT_ACTIVE = 3.0
MULT_COVERED = 1.0


def _aporte_topic(tags: Dict[str, str]) -> str:
    """Tema de un aporte: tag ``topic`` si existe, si no la categoría del Judge."""
    return (tags.get("topic") or tags.get("category") or "").strip().lower()


def _level_for(percent: float) -> str:
    if percent < GAP_CRITICAL_PCT:
        return "critical"
    if percent < GAP_ACTIVE_PCT:
        return "active"
    return "covered"


def multiplier_for_level(level: str) -> float:
    return {"critical": MULT_CRITICAL, "active": MULT_ACTIVE}.get(level, MULT_COVERED)


def _aporte_ts(tags: Dict[str, str]) -> int:
    try:
        return int(tags.get("timestamp", 0) or 0)
    except (ValueError, TypeError):
        return 0


def coverage_from_aportes(
    topics: List[str], aporte_tags: List[Dict[str, str]], now: Optional[int] = None,
) -> Dict[str, Dict[str, Any]]:
    """Calcula la cobertura por tema (función pura, sin red).

    Devuelve ``{topic: {count, percent, level, multiplier, stale}}`` por tema.
    ``percent`` está acotado a [0, 100]; ``stale`` es True si el tema tiene
    aportes pero el más reciente tiene 30+ días.
    """
    now = now if now is not None else int(time.time())
    counts: Dict[str, int] = {t: 0 for t in topics}
    newest: Dict[str, int] = {t: 0 for t in topics}
    for tags in aporte_tags:
        topic = _aporte_topic(tags)
        if topic in counts:
            counts[topic] += 1
            newest[topic] = max(newest[topic], _aporte_ts(tags))

    result: Dict[str, Dict[str, Any]] = {}
    for topic in topics:
        n = counts.get(topic, 0)
        percent = min(100.0, (n / TARGET_APORTES_PER_TOPIC) * 100.0) if TARGET_APORTES_PER_TOPIC else 0.0
        level = _level_for(percent)
        stale = bool(n > 0 and newest[topic] > 0 and (now - newest[topic]) > STALE_SECONDS)
        result[topic] = {
            "count": n,
            "percent": round(percent, 1),
            "level": level,
            "multiplier": multiplier_for_level(level),
            "stale": stale,
        }
    return result


async def node_knowledge_map(node_id: str, topics: List[str]) -> Dict[str, Dict[str, Any]]:
    """Mapa de cobertura de un nodo, leyendo sus aportes desde Irys."""
    from aisynergix.services.irys import list_node_aportes
    try:
        aportes = await list_node_aportes(node_id)
    except Exception as exc:
        logger.warning("node_knowledge_map %s falló al leer aportes: %s", node_id, exc)
        aportes = []
    return coverage_from_aportes(topics, aportes)


async def topic_gap_multiplier(node_id: str, topics: List[str], topic: str) -> float:
    """Multiplicador de recompensa para un tema según su vacío de conocimiento."""
    topic = (topic or "").strip().lower()
    if topic not in topics:
        return MULT_COVERED
    cov = await node_knowledge_map(node_id, topics)
    return cov.get(topic, {}).get("multiplier", MULT_COVERED)


def render_map(coverage: Dict[str, Dict[str, Any]], topic_label) -> str:
    """Renderiza el mapa como barras de 10 celdas. ``topic_label(key)->str``."""
    lines: List[str] = []
    badge = {"critical": " ← CRÍTICO ×5", "active": " ← VACÍO ×3", "covered": ""}
    for topic, info in coverage.items():
        pct = info["percent"]
        filled = int(round(pct / 10.0))
        bar = "█" * filled + "░" * (10 - filled)
        tag = badge.get(info["level"], "")
        if info.get("stale"):
            tag += " ⏰ desactualizado"
        lines.append(f"{topic_label(topic)} {bar} {int(pct)}%{tag}")
    return "\n".join(lines)


__all__ = [
    "TARGET_APORTES_PER_TOPIC",
    "coverage_from_aportes",
    "node_knowledge_map",
    "topic_gap_multiplier",
    "multiplier_for_level",
    "render_map",
]
