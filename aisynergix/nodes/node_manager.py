"""node_manager.py — ciclo de vida y membresía de los nodos de comunidad.

Un nodo vive como DataItems inmutables en Irys (ver ``services/irys.py``).
Aquí está la lógica de negocio: crear, leer, listar, unirse, salir y marcar
el nodo activo de un usuario.  El estado mutable (nº de miembros, nº de
aportes) NO se guarda en el registro del nodo: se calcula al vuelo a partir
de las membresías y los aportes, igual que el leaderboard — así nunca hay
condiciones de carrera entre escrituras concurrentes.
"""

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Bono de bienvenida (SYNX contable) para quien funda un nodo.
FOUNDER_BONUS_SYNX: float = 100.0

# Tipos de nodo (clave interna → icono).  La etiqueta legible se traduce vía
# locales con la clave ``node_type_<key>``.
NODE_TYPES: Dict[str, str] = {
    "barrio":      "🏘️",
    "pais":        "🌍",
    "tematico":    "📚",
    "profesional": "💼",
    "global":      "🌐",
}

# Temas prioritarios que un nodo puede cubrir (clave interna → icono).  La
# etiqueta legible se traduce vía locales con la clave ``topic_<key>``.
TOPICS: Dict[str, str] = {
    "salud":      "🩺",
    "educacion":  "📖",
    "empleo":     "💼",
    "servicios":  "🔧",
    "seguridad":  "🛡️",
    "economia":   "💰",
}

MAX_TOPICS_PER_NODE = 5
MAX_NODE_NAME_LEN = 60
MIN_NODE_NAME_LEN = 3


def generate_node_id(creator_hash: str, name: str) -> str:
    """Genera un id de nodo determinista y resistente a colisiones.

    ``node_`` + SHA-256(creador : nombre : timestamp)[:12].  El timestamp
    evita que dos nodos con el mismo nombre del mismo creador colisionen.
    """
    ts = int(datetime.now(timezone.utc).timestamp())
    raw = f"{creator_hash}:{name.strip().lower()}:{ts}"
    return "node_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def normalize_topics(topics: List[str]) -> List[str]:
    """Filtra a temas válidos conocidos, sin duplicados, máximo MAX_TOPICS."""
    out: List[str] = []
    for t in topics:
        key = (t or "").strip().lower()
        if key in TOPICS and key not in out:
            out.append(key)
        if len(out) >= MAX_TOPICS_PER_NODE:
            break
    return out


@dataclass
class Node:
    node_id: str
    name: str
    node_type: str
    creator: str
    language: str
    topics: List[str] = field(default_factory=list)
    country: str = ""
    region: str = ""
    member_count: int = 0
    aporte_count: int = 0

    @property
    def icon(self) -> str:
        return NODE_TYPES.get(self.node_type, "🌐")

    @classmethod
    def from_tags(cls, tags: Dict[str, str]) -> "Node":
        raw_topics = tags.get("topics", "") or ""
        topics = [t for t in (x.strip() for x in raw_topics.split(",")) if t]
        return cls(
            node_id=tags.get("node-id", ""),
            name=tags.get("node-name", ""),
            node_type=tags.get("node-type", "global"),
            creator=tags.get("creator", ""),
            language=tags.get("language", "es"),
            topics=topics,
            country=tags.get("country", "") or "",
            region=tags.get("region", "") or "",
        )


def _identity():
    from aisynergix.bot.identity import get_identity_manager
    return get_identity_manager()


def _hash_uid(uid: int) -> str:
    from aisynergix.bot.identity import _hash_uid as h
    return h(uid)


async def create_node(
    uid: int,
    name: str,
    node_type: str,
    language: str,
    topics: List[str],
    country: str = "",
    region: str = "",
) -> Optional[Node]:
    """Crea un nodo en Irys, registra al creador como fundador, le acredita el
    bono de bienvenida y lo marca como su nodo activo.

    Devuelve el ``Node`` creado, o None si la entrada es inválida.
    """
    from aisynergix.services.irys import write_node, write_node_member

    clean_name = (name or "").strip()
    if not (MIN_NODE_NAME_LEN <= len(clean_name) <= MAX_NODE_NAME_LEN):
        return None
    if node_type not in NODE_TYPES:
        node_type = "global"
    topics = normalize_topics(topics)

    creator_hash = _hash_uid(uid)
    node_id = generate_node_id(creator_hash, clean_name)

    # Bond en SYNERGIX real: crear un nodo exige bloquear BOND_AMOUNT (anti-Sybil).
    # Se bloquea ANTES de escribir el nodo; si no alcanza el saldo disponible,
    # no se crea nada.
    from aisynergix.services import bonds as bonds_svc
    if not await bonds_svc.lock_node_bond(uid, node_id):
        logger.info("create_node: %s sin bond suficiente (%.0f SYNERGIX)",
                    creator_hash, bonds_svc.BOND_AMOUNT)
        return None

    from aisynergix.nodes.geo import needs_location
    if not needs_location(node_type):
        country, region = "", ""
    await write_node(
        node_id=node_id,
        name=clean_name,
        node_type=node_type,
        creator_hash=creator_hash,
        language=language,
        topics=topics,
        country=country,
        region=region,
    )
    await write_node_member(node_id, creator_hash, role="founder", status="active")

    # Crear un nodo ahora CUESTA un bond (ya no hay bono de fundador gratis).
    # Solo se marca el nodo como activo del usuario.
    try:
        await _identity().apply_deltas(uid, active_node=node_id)
    except Exception as exc:
        logger.warning("create_node: no se pudo marcar nodo activo para %s: %s",
                       creator_hash, exc)

    logger.info("🏘️ Nodo creado %s '%s' por %s (bond %.0f SYNERGIX)",
                node_id, clean_name, creator_hash, bonds_svc.BOND_AMOUNT)
    return Node(
        node_id=node_id, name=clean_name, node_type=node_type,
        creator=creator_hash, language=language, topics=topics,
        country=country, region=region,
        member_count=1, aporte_count=0,
    )


async def get_node(node_id: str) -> Optional[Node]:
    """Lee un nodo con sus estadísticas vivas (miembros y aportes)."""
    from aisynergix.services.irys import (
        get_node_record, list_node_members, count_node_aportes,
    )
    record = await get_node_record(node_id)
    if not record:
        return None
    node = Node.from_tags(record)
    try:
        node.member_count = len(await list_node_members(node_id))
    except Exception:
        node.member_count = 0
    try:
        node.aporte_count = await count_node_aportes(node_id)
    except Exception:
        node.aporte_count = 0
    return node


async def list_nodes(limit: int = 50) -> List[Node]:
    """Lista nodos existentes (sin estadísticas pesadas, para el explorador)."""
    from aisynergix.services.irys import list_node_records
    records = await list_node_records(limit=limit)
    return [Node.from_tags(r) for r in records]


async def list_user_nodes(uid: int) -> List[Node]:
    """Nodos a los que pertenece el usuario."""
    from aisynergix.services.irys import list_user_memberships, get_node_record
    uid_hash = _hash_uid(uid)
    memberships = await list_user_memberships(uid_hash)
    nodes: List[Node] = []
    for m in memberships:
        node_id = m.get("node-id", "")
        if not node_id:
            continue
        record = await get_node_record(node_id)
        if record:
            nodes.append(Node.from_tags(record))
    return nodes


async def is_member(node_id: str, uid: int) -> bool:
    from aisynergix.services.irys import get_node_member
    member = await get_node_member(node_id, _hash_uid(uid))
    return bool(member and member.get("member-status", "active") == "active")


async def join_node(uid: int, node_id: str) -> bool:
    """Une a un usuario a un nodo y lo marca como su nodo activo."""
    from aisynergix.services.irys import get_node_record, write_node_member
    if not await get_node_record(node_id):
        return False
    await write_node_member(node_id, _hash_uid(uid), role="member", status="active")
    try:
        await _identity().apply_deltas(uid, active_node=node_id)
    except Exception:
        pass
    return True


async def leave_node(uid: int, node_id: str) -> bool:
    """Marca la membresía como inactiva; si era el nodo activo, lo limpia."""
    from aisynergix.services.irys import write_node_member
    await write_node_member(node_id, _hash_uid(uid), role="member", status="left")
    try:
        profile = await _identity().get_profile(uid)
        if profile.active_node == node_id:
            # active_node="" limpia el nodo activo en apply_deltas.
            await _identity().apply_deltas(uid, active_node="")
    except Exception:
        pass
    return True


async def set_active_node(uid: int, node_id: str) -> None:
    """Marca un nodo como el nodo activo del usuario (sus aportes irán allí)."""
    await _identity().apply_deltas(uid, active_node=node_id)


__all__ = [
    "NODE_TYPES",
    "TOPICS",
    "FOUNDER_BONUS_SYNX",
    "MAX_TOPICS_PER_NODE",
    "Node",
    "generate_node_id",
    "normalize_topics",
    "create_node",
    "get_node",
    "list_nodes",
    "list_user_nodes",
    "is_member",
    "join_node",
    "leave_node",
    "set_active_node",
]
