"""Nodos de comunidad de Synergix.

Un nodo es una comunidad temática o geográfica con su propio grafo de
conocimiento dentro de Synergix.  Este paquete contiene la lógica de negocio:

  * ``node_manager``  — ciclo de vida y membresía de nodos sobre Irys.
  * ``knowledge_map`` — mapa de cobertura y detección de vacíos de conocimiento.
"""

from aisynergix.nodes.node_manager import (
    NODE_TYPES,
    TOPICS,
    FOUNDER_BONUS_SYNX,
    create_node,
    get_node,
    list_nodes,
    list_user_nodes,
    join_node,
    leave_node,
    is_member,
    set_active_node,
    generate_node_id,
    normalize_topics,
)
from aisynergix.nodes.knowledge_map import (
    node_knowledge_map,
    topic_gap_multiplier,
    coverage_from_aportes,
    render_map,
    TARGET_APORTES_PER_TOPIC,
)

__all__ = [
    "NODE_TYPES",
    "TOPICS",
    "FOUNDER_BONUS_SYNX",
    "create_node",
    "get_node",
    "list_nodes",
    "list_user_nodes",
    "join_node",
    "leave_node",
    "is_member",
    "set_active_node",
    "generate_node_id",
    "normalize_topics",
    "node_knowledge_map",
    "topic_gap_multiplier",
    "coverage_from_aportes",
    "render_map",
    "TARGET_APORTES_PER_TOPIC",
]
