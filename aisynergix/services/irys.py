"""
irys.py — Capa de almacenamiento permanente sobre Irys (Arweave).

Arquitectura:
  Upload  → HTTP POST al microservicio `irys-uploader` (Node.js, SDK oficial
            @irys/upload-ethereum). El bot NO firma DataItems en Python.
  Lectura → GraphQL para metadatos + gateway HTTP para contenido (sin firma).
  Auth    → la PRIVATE_KEY vive en el microservicio. Aquí solo se usa la
            dirección pública para filtros GraphQL.
"""

import asyncio
import base64
import gzip
import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

import httpx
from eth_account import Account
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════
# CONFIGURACIÓN
# ═══════════════════════════════════════════════════════════════════════

def _getenv(key: str, default: str = "") -> str:
    return os.getenv(f"SYNERGIX_{key}", os.getenv(key, default))


PRIVATE_KEY: str = os.getenv("PRIVATE_KEY", "")
IRYS_NODE_URL: str = _getenv("IRYS_NODE_URL", "https://uploader.irys.xyz")
IRYS_GATEWAY_URL: str = _getenv("IRYS_GATEWAY_URL", "https://gateway.irys.xyz")
IRYS_TOKEN: str = _getenv("IRYS_TOKEN", "bnb")
IRYS_UPLOADER_URL: str = _getenv("IRYS_UPLOADER_URL", "http://irys-uploader:8083")
TELEGRAM_TOKEN: str = _getenv("TELEGRAM_TOKEN", "")
THINKER_HOST: str = _getenv("THINKER_HOST", "http://thinker:8081")
JUDGE_HOST: str = _getenv("JUDGE_HOST", "http://judge:8080")
CACHE_TTL: str = _getenv("CACHE_TTL", "12")

_APP_NAME = "Synergix"
_GRAPHQL_URL = f"{IRYS_NODE_URL}/graphql"
_UPLOADER_UPLOAD_URL = f"{IRYS_UPLOADER_URL}/upload"
_UPLOADER_BALANCE_URL = f"{IRYS_UPLOADER_URL}/balance"
_UPLOADER_HEALTH_URL = f"{IRYS_UPLOADER_URL}/health"

BRAIN_CODES: List[str] = ["prog", "tech", "cien", "know"]


def _gw(tx_id: str) -> str:
    """Devuelve la URL pública del gateway para un txId dado."""
    return f"{IRYS_GATEWAY_URL}/{tx_id}"


# ═══════════════════════════════════════════════════════════════════════
# GHOST PROTOCOL — Ofuscación de UID
# ═══════════════════════════════════════════════════════════════════════

def _hash_uid(uid: int) -> str:
    """SHA-256("Synergix_" + uid) → hex[:12]. Ghost Protocol UID hash."""
    return hashlib.sha256(f"Synergix_{uid}".encode()).hexdigest()[:12]


# ═══════════════════════════════════════════════════════════════════════
# CLIENTE HTTP (singleton)
# ═══════════════════════════════════════════════════════════════════════

_http: Optional[httpx.AsyncClient] = None


async def _client() -> httpx.AsyncClient:
    global _http
    if _http is None or _http.is_closed:
        _http = httpx.AsyncClient(timeout=120.0, follow_redirects=True)
    return _http


# ═══════════════════════════════════════════════════════════════════════
# UPLOAD — delegado al microservicio Node.js (`irys-uploader`)
# ═══════════════════════════════════════════════════════════════════════

@retry(
    retry=retry_if_exception_type((httpx.TransportError, ConnectionError, TimeoutError, OSError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
async def _upload(data: bytes, tags: List[Dict[str, str]]) -> str:
    """
    Sube data a Irys vía el microservicio `irys-uploader`.
    El servicio Node.js firma el DataItem con el SDK oficial y retorna el txId.
    """
    all_tags = [{"name": "App-Name", "value": _APP_NAME}] + list(tags or [])
    payload = {
        "data": base64.b64encode(data).decode("ascii"),
        "tags": all_tags,
    }
    cli = await _client()
    resp = await cli.post(
        _UPLOADER_UPLOAD_URL,
        json=payload,
        headers={"Content-Type": "application/json"},
        timeout=120.0,
    )
    if not resp.is_success:
        logger.error(
            "irys-uploader upload %d — body: %s",
            resp.status_code, resp.text[:400],
        )
    resp.raise_for_status()
    body = resp.json() if isinstance(resp.json(), dict) else {}
    tx_id: str = body.get("id", "")
    logger.debug("Irys upload OK: tx=%s bytes=%d", tx_id, len(data))
    return tx_id


# ═══════════════════════════════════════════════════════════════════════
# FETCH (gateway)
# ═══════════════════════════════════════════════════════════════════════

@retry(
    retry=retry_if_exception_type((httpx.TransportError, ConnectionError, TimeoutError, OSError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
async def _fetch(tx_id: str) -> bytes:
    """Descarga contenido de Irys gateway por transaction ID."""
    cli = await _client()
    resp = await cli.get(f"{IRYS_GATEWAY_URL}/{tx_id}")
    resp.raise_for_status()
    return resp.content


# ═══════════════════════════════════════════════════════════════════════
# GRAPHQL
# ═══════════════════════════════════════════════════════════════════════

@retry(
    retry=retry_if_exception_type((httpx.TransportError, ConnectionError, TimeoutError, OSError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
async def _gql(query: str) -> Dict[str, Any]:
    cli = await _client()
    resp = await cli.post(
        _GRAPHQL_URL,
        json={"query": query},
        headers={"Content-Type": "application/json"},
    )
    resp.raise_for_status()
    result = resp.json()
    if not isinstance(result, dict):
        logger.warning("GraphQL respuesta inesperada (tipo %s): %s", type(result).__name__, str(result)[:200])
        return {}
    if result.get("errors"):
        # Log GraphQL errors at ERROR (not WARNING) so schema/syntax issues
        # are immediately visible.  In the past, returning the (incomplete)
        # result here let queries silently behave as "no data found",
        # producing zeros in user-facing fields.
        logger.error(
            "GraphQL errors for query (returning empty data to avoid stale reads): %s\nQuery: %s",
            str(result["errors"])[:600], query[:300],
        )
    return result


def _owner_filter() -> str:
    """Filtro owners para limitar queries al wallet propio y evitar timeouts."""
    if not PRIVATE_KEY:
        return ""
    address = Account.from_key(PRIVATE_KEY).address
    return f', owners: ["{address}"]'


def _gql_str(value: Any) -> str:
    """Serializa un valor como literal de string GraphQL SEGURO.

    json.dumps escapa comillas, backslashes y saltos de línea, produciendo un
    literal entrecomillado válido. Imprescindible: los valores de tag pueden
    venir de entrada del usuario (node_id/ghost_id/tx desde la API pública,
    nombres de nodo, etc.); sin escapar, una comilla permitiría inyectar
    GraphQL y, p. ej., anular el filtro `owners:` anti-forgery.
    """
    return json.dumps(str(value), ensure_ascii=False)


def _tag_filter(tags: List[Dict[str, str]]) -> str:
    """Convierte lista de tags a filtro GraphQL (valores escapados)."""
    parts = [
        f'{{name: {_gql_str(t["name"])}, values: [{_gql_str(t["value"])}]}}'
        for t in tags
    ]
    return ", ".join(parts)


async def _query_latest(tags: List[Dict[str, str]]) -> Optional[Dict[str, Any]]:
    """Retorna el nodo MÁS RECIENTE que coincida con los tags dados.

    Ordena explícitamente con ``order: DESC`` (Irys lo soporta) para no depender
    de un orden por defecto incierto: con muchas escrituras por usuario (cientos
    de tx), confiar en el default hacía que el tx más nuevo no entrara en la
    primera página y se devolviera uno VIEJO.  Si una versión del schema
    rechazara ``order`` (deja la query sin ``data``), reintenta sin él.  En ambos
    casos se reordena localmente por ``timestamp`` DESC como garantía final.
    """
    inner = f"tags: [{_tag_filter(tags)}]{_owner_filter()}, first: 100"
    for order_clause in (", order: DESC", ""):
        q = f"""
        {{
          transactions({inner}{order_clause}) {{
            edges {{ node {{ id tags {{ name value }} timestamp }} }}
          }}
        }}
        """
        try:
            data = await _gql(q)
            edges = (data.get("data") or {}).get("transactions", {}).get("edges", [])
            nodes = [e["node"] for e in edges if e.get("node")]
            if not nodes:
                continue
            if order_clause:
                # Server-side order: DESC is authoritative — edges[0] is newest.
                return nodes[0]
            # Fallback (no explicit order): best-effort local sort by timestamp.
            def _ts(n):
                try:
                    return int(n.get("timestamp") or 0)
                except (TypeError, ValueError):
                    return 0
            nodes.sort(key=_ts, reverse=True)
            return nodes[0]
        except Exception as exc:
            logger.warning("_query_latest (order=%r) falló: %s", order_clause or "default", exc)
    return None


# Irys GraphQL caps `first` at 1000 per page.  Anything larger needs
# cursor-based pagination via the `after` argument.
_IRYS_PAGE_MAX = 1000


async def _query_all(
    tags: List[Dict[str, str]], limit: int = 100
) -> List[Dict[str, Any]]:
    """Retorna todos los nodos (DESC por timestamp) que coincidan con los tags dados.

    Implementa paginación basada en cursor (`after`) para soportar
    ``limit`` mayores al máximo de Irys (1000 por página).  Irys devuelve
    transacciones en orden DESC por bloque por defecto; ordenamos
    localmente por timestamp DESC como respaldo.

    Añadir explícitamente ``sort/order`` al GraphQL falla en algunas
    versiones del schema y deja la query sin ``data``.
    """
    nodes: List[Dict[str, Any]] = []
    after_cursor: Optional[str] = None
    remaining = max(0, int(limit))

    while remaining > 0:
        page_size = min(remaining, _IRYS_PAGE_MAX)
        after_clause = f', after: {_gql_str(after_cursor)}' if after_cursor else ""
        q = f"""
        {{
          transactions(
            tags: [{_tag_filter(tags)}]{_owner_filter()},
            first: {page_size}{after_clause}
          ) {{
            pageInfo {{ hasNextPage }}
            edges {{
              cursor
              node {{ id tags {{ name value }} timestamp }}
            }}
          }}
        }}
        """
        try:
            data = await _gql(q)
        except Exception as exc:
            logger.warning("_query_all falló (página): %s", exc)
            break

        tx_block = (data.get("data") or {}).get("transactions", {}) or {}
        edges = tx_block.get("edges", []) or []
        if not edges:
            break

        for edge in edges:
            node = edge.get("node") if isinstance(edge, dict) else None
            if node:
                nodes.append(node)

        remaining -= len(edges)
        last_cursor = edges[-1].get("cursor") if isinstance(edges[-1], dict) else None
        has_next = (tx_block.get("pageInfo") or {}).get("hasNextPage", False)
        if not has_next or not last_cursor or len(edges) < page_size:
            break
        after_cursor = last_cursor

    nodes.sort(key=lambda n: n.get("timestamp", 0), reverse=True)
    return nodes


async def _query_by_id(tx_id: str) -> Optional[Dict[str, Any]]:
    """Obtiene tags de una transacción por su ID."""
    q = f"""
    {{
      transactions(ids: [{_gql_str(tx_id)}]{_owner_filter()}) {{
        edges {{ node {{ id tags {{ name value }} }} }}
      }}
    }}
    """
    try:
        data = await _gql(q)
        edges = (data.get("data") or {}).get("transactions", {}).get("edges", [])
        return edges[0]["node"] if edges else None
    except Exception:
        return None


def _node_tags(node: Dict[str, Any]) -> Dict[str, str]:
    """Convierte [{name, value}] → {name: value}."""
    return {t["name"]: t["value"] for t in node.get("tags", [])}


# ═══════════════════════════════════════════════════════════════════════
# EMERGENCY LOCK
# ═══════════════════════════════════════════════════════════════════════

_emergency_lock_active: bool = False


async def check_emergency_lock() -> bool:
    """Consulta Irys por el estado del lock de emergencia."""
    global _emergency_lock_active
    try:
        node = await _query_latest([{"name": "data-type", "value": "emergency-lock"}])
        if node:
            status = _node_tags(node).get("lock-status", "inactive")
            _emergency_lock_active = (status == "active")
        else:
            _emergency_lock_active = False
    except Exception as exc:
        logger.warning("check_emergency_lock falló: %s", exc)
    return _emergency_lock_active


def is_emergency_locked() -> bool:
    return _emergency_lock_active


async def create_emergency_lock() -> None:
    global _emergency_lock_active
    tx_id = await _upload(b"{}", [
        {"name": "data-type",    "value": "emergency-lock"},
        {"name": "lock-status",  "value": "active"},
        {"name": "Content-Type", "value": "application/json"},
    ])
    _emergency_lock_active = True
    logger.warning("🔒 Emergency lock ACTIVADO en Irys. Ver dato: %s", _gw(tx_id))


async def delete_emergency_lock() -> None:
    """No se puede borrar en Irys; se sube un tx con status=inactive."""
    global _emergency_lock_active
    tx_id = await _upload(b"{}", [
        {"name": "data-type",    "value": "emergency-lock"},
        {"name": "lock-status",  "value": "inactive"},
        {"name": "Content-Type", "value": "application/json"},
    ])
    _emergency_lock_active = False
    logger.warning("🔓 Emergency lock DESACTIVADO en Irys. Ver dato: %s", _gw(tx_id))


# ═══════════════════════════════════════════════════════════════════════
# AI GUARD
# ═══════════════════════════════════════════════════════════════════════

_ai_guard_patterns: List[str] = []

_DEFAULT_AI_GUARD = (
    "# Synergix AI Guard\n"
    "ignore previous instructions\n"
    "ignore all previous\n"
    "disregard your instructions\n"
    "forget your instructions\n"
    "you are now\n"
    "act as\n"
    "pretend you are\n"
    "roleplay as\n"
    "jailbreak\n"
    "DAN\n"
)


async def load_ai_guard(auto_create: bool = False) -> List[str]:
    global _ai_guard_patterns
    defaults = [
        ln.strip() for ln in _DEFAULT_AI_GUARD.splitlines()
        if ln.strip() and not ln.startswith("#")
    ]
    try:
        node = await _query_latest([{"name": "data-type", "value": "ai-guard"}])
        if node:
            raw = await _fetch(node["id"])
            patterns = [
                ln.strip() for ln in raw.decode("utf-8").splitlines()
                if ln.strip() and not ln.startswith("#")
            ]
            _ai_guard_patterns = patterns
            logger.info("🛡️ AI Guard cargado desde Irys: %d patrones", len(patterns))
            return patterns
    except Exception as exc:
        logger.warning("load_ai_guard falló: %s", exc)

    _ai_guard_patterns = defaults
    if auto_create:
        try:
            tx_id = await _upload(
                _DEFAULT_AI_GUARD.encode("utf-8"),
                [
                    {"name": "data-type",    "value": "ai-guard"},
                    {"name": "Content-Type", "value": "text/plain; charset=utf-8"},
                ],
            )
            logger.info("🛡️ ai-guard creado en Irys. Ver dato: %s", _gw(tx_id))
        except Exception as exc:
            logger.warning("No se pudo crear ai-guard en Irys: %s", exc)
    return defaults


def get_ai_guard_patterns() -> List[str]:
    return _ai_guard_patterns


def check_ai_guard(text: str) -> bool:
    lo = text.lower()
    return any(p.lower() in lo for p in _ai_guard_patterns)


# ═══════════════════════════════════════════════════════════════════════
# SYSTEM CONFIG
# ═══════════════════════════════════════════════════════════════════════

_DEFAULT_SYSTEM_CONFIG: Dict[str, Any] = {
    "quality_threshold":      5.0,
    "elite_threshold":        9.0,
    "legendary_threshold":    9.5,
    "trust_score_increment":  0.1,
    "trust_score_decrement":  0.2,
    "min_contribution_length": 20,
}
_system_config: Dict[str, Any] = {}


async def load_system_config(auto_create: bool = False) -> Dict[str, Any]:
    global _system_config
    try:
        node = await _query_latest([{"name": "data-type", "value": "system-config"}])
        if node:
            raw = await _fetch(node["id"])
            data = json.loads(raw.decode("utf-8"))
            _system_config = {**_DEFAULT_SYSTEM_CONFIG, **data}
            logger.info("⚙️ system-config cargado desde Irys")
            return _system_config
    except Exception as exc:
        logger.warning("load_system_config falló: %s", exc)

    if auto_create:
        try:
            tx_id = await _upload(
                json.dumps(_DEFAULT_SYSTEM_CONFIG, indent=2).encode("utf-8"),
                [
                    {"name": "data-type",    "value": "system-config"},
                    {"name": "Content-Type", "value": "application/json"},
                ],
            )
            logger.info("⚙️ system-config creado en Irys. Ver dato: %s", _gw(tx_id))
        except Exception as exc:
            logger.warning("No se pudo crear system-config en Irys: %s", exc)

    _system_config = dict(_DEFAULT_SYSTEM_CONFIG)
    return _system_config


def get_system_config() -> Dict[str, Any]:
    return _system_config if _system_config else dict(_DEFAULT_SYSTEM_CONFIG)


# ═══════════════════════════════════════════════════════════════════════
# PERFILES DE USUARIO
# Sin límite de tags: almacenamos todos los campos directamente como tags.
# Patrón mutable: cada write sube nueva versión; read devuelve la más reciente.
# ═══════════════════════════════════════════════════════════════════════

_USER_TAG_DEFAULTS: Dict[str, str] = {
    "fsm_state":           "idle",
    "points":              "0",
    "rank":                "🌱 Iniciado",
    "contribution_count":  "0",
    "daily_aportes_count": "0",
    "total_uses_count":    "0",
    "language":            "es",
    "last_seen_ts":        "0",
}

# Mapeo campo interno → nombre de tag Irys
_PROFILE_TAG_MAP: Dict[str, str] = {
    "points":              "points",
    "rank":                "rank",
    "language":            "language",
    "trust_score":         "trust-score",
    "human_verified":      "human-verified",
    "daily_aportes_count": "daily-aportes-count",
    "contribution_count":  "contribution-count",
    "total_uses_count":    "total-uses-count",
    "last_seen_ts":        "last-seen-ts",
    "fsm_state":           "fsm-state",
    "wallet_address":      "wallet-address",
    # Economía SYNX (saldo contable en Irys) + nodo activo del usuario.
    "synx_balance":        "synx-balance",
    "active_node":         "active-node",
    # Wallet custodial generada por el bot (distinta de wallet-address, que
    # es la wallet propia del usuario verificada por firma).
    "custodial_address":   "custodial-address",
    # Racha de días consecutivos contribuyendo (§5.3).
    "streak_days":         "streak-days",
    "last_aporte_date":    "last-aporte-date",
    # SYNX ganados históricos (Passport §10.2) — solo crece.
    "synx_earned_total":   "synx-earned-total",
    # Anti-farming del bono de fundador (una sola vez por usuario).
    "founder_bonus_claimed": "founder-bonus-claimed",
}
_PROFILE_TAG_RMAP: Dict[str, str] = {v: k for k, v in _PROFILE_TAG_MAP.items()}


@retry(
    retry=retry_if_exception_type((httpx.TransportError, ConnectionError, TimeoutError, OSError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
async def read_user_pointer(uid_ofuscado: str) -> Optional[Dict[str, str]]:
    """Lee el puntero al último ``user-profile`` sellado para este usuario.

    Mirror del patrón ``brain-pointer``: el puntero es una tx pequeña con
    ``data-type=user-profile-pointer`` que registra el ``latest-tx`` del
    último perfil escrito.  Permite recuperar la versión vigente de forma
    determinista vía ``_query_by_id(latest_tx)`` sin depender de la
    ordenación por timestamp del GraphQL ni esperar a que la indexación
    de Irys se ponga al día tras un upload reciente (latencia 5-30 s).

    Devuelve los tags del puntero (incluye ``latest-tx`` y un snapshot de
    los contadores principales) o ``None`` si no existe puntero todavía
    (usuario nuevo o perfil escrito antes de que existiera este mecanismo).
    """
    try:
        node = await _query_latest([
            {"name": "data-type", "value": "user-profile-pointer"},
            {"name": "uid-hash",  "value": uid_ofuscado},
        ])
        if node:
            return _node_tags(node)
    except Exception as exc:
        logger.warning("read_user_pointer %s falló: %s", uid_ofuscado, exc)
    return None


@retry(
    retry=retry_if_exception_type((httpx.TransportError, ConnectionError, TimeoutError, OSError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
async def profile_exists(uid_ofuscado: str) -> bool:
    """True si ya existe un ``user-profile`` sellado en Irys para el usuario.

    Señal de existencia FIABLE e independiente de los contadores de actividad
    (puntos/usos): sirve para distinguir un usuario genuinamente nuevo de uno
    que regresa pero que todavía no ha contribuido.  Comprueba primero el
    puntero (camino rápido) y, como respaldo para perfiles antiguos escritos
    antes del mecanismo de puntero, una consulta directa a ``user-profile``.

    Devuelve ``False`` ante un fallo de lectura: es conservador (peor caso, se
    muestra la bienvenida de nuevo una vez), pero el guard anti-regresión de
    ``_do_update_profile`` impide que ese re-sellado pise el historial real.
    """
    try:
        if await read_user_pointer(uid_ofuscado):
            return True
        node = await _query_latest([
            {"name": "data-type", "value": "user-profile"},
            {"name": "uid-hash",  "value": uid_ofuscado},
        ])
        return node is not None
    except Exception as exc:
        logger.warning("profile_exists %s falló: %s", uid_ofuscado, exc)
        return False


@retry(
    retry=retry_if_exception_type((httpx.TransportError, ConnectionError, TimeoutError, OSError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
async def write_user_pointer(uid_ofuscado: str, tx_id: str, tags: Dict[str, str]) -> None:
    """Sube un puntero al ``tx_id`` del último ``user-profile`` sellado.

    Incluye un snapshot de los contadores principales (``points``,
    ``contribution-count``, ``total-uses-count``, ``last-seen-ts``) en los
    propios tags del puntero para diagnóstico rápido sin tener que resolver
    el tx referenciado.
    """
    pointer_tags = [
        {"name": "data-type", "value": "user-profile-pointer"},
        {"name": "uid-hash",  "value": uid_ofuscado},
        {"name": "latest-tx", "value": tx_id},
        {"name": "points",            "value": str(tags.get("points", "0"))},
        {"name": "contribution-count", "value": str(tags.get("contribution_count", "0"))},
        {"name": "total-uses-count",  "value": str(tags.get("total_uses_count", "0"))},
        {"name": "last-seen-ts",      "value": str(tags.get("last_seen_ts", "0"))},
        {"name": "Content-Type",      "value": "application/json"},
    ]
    await _upload(b"{}", pointer_tags)


@retry(
    retry=retry_if_exception_type((httpx.TransportError, ConnectionError, TimeoutError, OSError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
async def read_user_tags(uid_ofuscado: str) -> Dict[str, str]:
    """Lee el perfil de usuario más reciente desde Irys.

    Estrategia (en orden):
      1. ``user-profile-pointer`` → ``_query_by_id(latest-tx)``
         Lookup determinista, inmune a la latencia de indexación GraphQL
         para tx recientes y a posibles inconsistencias de ordenación
         por timestamp.
      2. Fallback ``_query_latest`` sobre ``user-profile``
         Compatibilidad con perfiles escritos antes de que existiera el
         puntero, o cuando el puntero apunta a un tx no resoluble.
      3. Defaults
         Solo si las dos rutas anteriores no devuelven nodo (usuario
         genuinamente nuevo) o ante un fallo total de lectura.

    El paso 1 resuelve el caso de "Ver estado" justo tras un sellado:
    antes, la lectura podía devolver la versión anterior por culpa de la
    latencia de indexación; ahora apunta directamente a la última tx.
    """
    try:
        node = None

        # 1) Lookup determinista vía puntero
        pointer_tags = await read_user_pointer(uid_ofuscado)
        if pointer_tags:
            latest_tx = pointer_tags.get("latest-tx")
            if latest_tx:
                node = await _query_by_id(latest_tx)
                if node is None:
                    logger.warning(
                        "user-profile-pointer apunta a tx=%s no resoluble "
                        "para uid_hash=%s — fallback a _query_latest.",
                        latest_tx, uid_ofuscado,
                    )

        # 2) Fallback: timestamp DESC sobre user-profile
        if node is None:
            node = await _query_latest([
                {"name": "data-type", "value": "user-profile"},
                {"name": "uid-hash",  "value": uid_ofuscado},
            ])

        if node:
            raw_tags = _node_tags(node)
            result: Dict[str, str] = {}
            for irys_key, internal_key in _PROFILE_TAG_RMAP.items():
                if irys_key in raw_tags:
                    result[internal_key] = raw_tags[irys_key]
            for k, v in _USER_TAG_DEFAULTS.items():
                result.setdefault(k, v)
            if result.get("wallet_address"):
                result.setdefault("human_verified", "true")
            return result
    except Exception as exc:
        logger.warning("read_user_tags %s falló: %s", uid_ofuscado, exc)
    return dict(_USER_TAG_DEFAULTS)


@retry(
    retry=retry_if_exception_type((httpx.TransportError, ConnectionError, TimeoutError, OSError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
async def write_user_tags(uid_ofuscado: str, tags: Dict[str, str]) -> None:
    """Sube nueva versión del perfil de usuario a Irys + puntero al tx-id.

    Tras subir el perfil, escribe un ``user-profile-pointer`` que apunta
    al tx recién creado.  Las lecturas posteriores (`read_user_tags`)
    consultarán este puntero primero, evitando la dependencia del
    ordenamiento por timestamp del GraphQL — que durante la ventana de
    indexación (~5-30 s) puede devolver la versión anterior y producir
    sobrescrituras con datos viejos.
    """
    irys_tags = [
        {"name": "data-type", "value": "user-profile"},
        {"name": "uid-hash",  "value": uid_ofuscado},
    ]
    for internal_key, irys_key in _PROFILE_TAG_MAP.items():
        val = tags.get(internal_key)
        if val is not None and val != "":
            irys_tags.append({"name": irys_key, "value": str(val)})

    irys_tags.append({"name": "Content-Type", "value": "application/json"})
    tx_id = await _upload(b"{}", irys_tags)
    logger.info("✅ Perfil %s actualizado en Irys. Ver dato: %s", uid_ofuscado, _gw(tx_id))

    # Best-effort: actualiza el puntero al último sellado.  Si falla, el
    # perfil sigue siendo recuperable vía el fallback _query_latest, así
    # que NO propagamos la excepción y el caller no se entera.
    try:
        await write_user_pointer(uid_ofuscado, tx_id, tags)
    except Exception as exc:
        logger.warning(
            "user-profile-pointer falló para %s tx=%s — perfil recuperable "
            "vía _query_latest: %s",
            uid_ofuscado, tx_id, exc,
        )


# ═══════════════════════════════════════════════════════════════════════
# APORTES
# ═══════════════════════════════════════════════════════════════════════

@retry(
    retry=retry_if_exception_type((httpx.TransportError, ConnectionError, TimeoutError, OSError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
async def write_aporte(
    uid_ofuscado: str,
    texto: str,
    tags: Dict[str, str],
    ts: Optional[int] = None,
) -> str:
    """Sube un aporte a Irys. Retorna el transaction ID."""
    ts = ts or int(datetime.now(timezone.utc).timestamp())
    irys_tags = [
        {"name": "data-type",     "value": "aporte"},
        {"name": "uid-hash",      "value": uid_ofuscado},
        {"name": "timestamp",     "value": str(ts)},
        {"name": "Content-Type",  "value": "text/plain; charset=utf-8"},
    ]
    for k, v in tags.items():
        if v is not None and str(v) != "":
            irys_tags.append({"name": k.replace("_", "-"), "value": str(v)})

    tx_id = await _upload(texto.encode("utf-8"), irys_tags)
    logger.info("✅ Aporte subido a Irys. Ver dato: %s  uid=%s", _gw(tx_id), uid_ofuscado)
    return tx_id


@retry(
    retry=retry_if_exception_type((httpx.TransportError, ConnectionError, TimeoutError, OSError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
async def read_aporte(tx_id: str) -> Tuple[str, Dict[str, str]]:
    """Lee contenido y tags de un aporte desde Irys."""
    raw_task = asyncio.create_task(_fetch(tx_id))
    node = await _query_by_id(tx_id)

    raw = await raw_task
    texto = raw.decode("utf-8")

    tags: Dict[str, str] = {}
    if node:
        rt = _node_tags(node)
        tags = {
            "category":     rt.get("category", "filosofia"),
            "author_uid":   rt.get("uid-hash", ""),
            "quality_score": rt.get("quality-score", "0"),
            "lang":         rt.get("language", "es"),
            # Judge-distilled summary stored at submission time.  Empty for
            # pre-PR2 aportes (those will be indexed using a truncated raw
            # text fallback in the brain-side code).
            "content_summary": rt.get("content-summary", ""),
            # Nodo del aporte (§4): permite el boost de memoria por nodo en el
            # RAG. Vacío para aportes fuera de nodo.
            "node_id": rt.get("node-id", ""),
        }
    return texto, tags


@retry(
    retry=retry_if_exception_type((httpx.TransportError, ConnectionError, TimeoutError, OSError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
async def list_aportes(uid_ofuscado: str, limit: int = 50) -> List[Dict[str, Any]]:
    """Lista aportes de un usuario desde Irys."""
    nodes = await _query_all([
        {"name": "data-type", "value": "aporte"},
        {"name": "uid-hash",  "value": uid_ofuscado},
    ], limit=limit)
    return [
        {
            "path": n["id"],
            "size": 0,
            "tags": _node_tags(n),
        }
        for n in nodes
    ]


# ═══════════════════════════════════════════════════════════════════════
# LEADERBOARD
# ═══════════════════════════════════════════════════════════════════════

_top10_cache: List[Dict[str, Any]] = []


def get_top10_cached() -> List[Dict[str, Any]]:
    return _top10_cache


async def compute_top10() -> List[Dict[str, Any]]:
    """Top10 by points, reading each user's authoritative latest profile.

    Profiles are append-only on Irys: every update creates a new transaction, so
    a naive ``_query_all(user-profile)`` scan is capped at the most recent N
    transactions and, as write volume grows, stops covering every user's latest
    state (the leaderboard "freezes"). Instead we enumerate users from their
    APORTE transactions (written once each, far fewer) and read each user's
    latest profile via its pointer — correct regardless of profile-write volume.
    """
    aporte_nodes = await _query_all([{"name": "data-type", "value": "aporte"}], limit=5000)

    aporte_counts: dict = {}
    for node in aporte_nodes:
        uid = _node_tags(node).get("uid-hash", "")
        if uid:
            aporte_counts[uid] = aporte_counts.get(uid, 0) + 1

    uids = list(aporte_counts.keys())
    # Read each user's latest profile concurrently (pointer-based, authoritative).
    profiles = await asyncio.gather(
        *[read_user_tags(uid) for uid in uids], return_exceptions=True
    )

    usuarios: List[Dict[str, Any]] = []
    for uid, tags in zip(uids, profiles):
        if not isinstance(tags, dict):
            continue
        try:
            stored_count = int(tags.get("contribution_count", "0"))
            usuarios.append({
                "uid":                uid,
                "points":             int(tags.get("points", "0")),
                "rank":               tags.get("rank", "🌱 Iniciado"),
                "contribution_count": max(aporte_counts.get(uid, 0), stored_count),
                "total_uses_count":   int(tags.get("total_uses_count", "0")),
            })
        except (ValueError, TypeError):
            continue

    usuarios.sort(key=lambda u: u["points"], reverse=True)
    logger.info(
        "compute_top10: %d contributing users; top=%s",
        len(usuarios),
        {"pts": usuarios[0]["points"], "uses": usuarios[0]["total_uses_count"]} if usuarios else None,
    )
    return usuarios[:10]


async def rebuild_top10() -> List[Dict[str, Any]]:
    global _top10_cache
    top10 = await compute_top10()
    _top10_cache = top10
    logger.info("🏆 Leaderboard reconstruido: %d usuarios en top10", len(top10))
    return top10


# ═══════════════════════════════════════════════════════════════════════
# LISTADO DE TODOS LOS USUARIOS
# ═══════════════════════════════════════════════════════════════════════

async def get_all_user_uids() -> List[str]:
    """Retorna todos los uid-hash únicos registrados en Irys."""
    nodes = await _query_all(
        [{"name": "data-type", "value": "user-profile"}], limit=1000
    )
    seen: Set[str] = set()
    result: List[str] = []
    for node in nodes:
        uid = _node_tags(node).get("uid-hash", "")
        if uid and uid not in seen:
            seen.add(uid)
            result.append(uid)
    return result


# ═══════════════════════════════════════════════════════════════════════
# RESET DIARIO DE daily_aportes_count (CRON)
# ═══════════════════════════════════════════════════════════════════════

async def reset_all_daily_counts() -> int:
    """Pone daily_aportes_count=0 para todos los usuarios."""
    uids = await get_all_user_uids()
    count = 0
    for uid in uids:
        try:
            current = await read_user_tags(uid)
            current["daily_aportes_count"] = "0"
            await write_user_tags(uid, current)
            count += 1
        except Exception:
            continue
    logger.info("📅 Reset diario: %d usuarios actualizados en Irys", count)
    return count


# ═══════════════════════════════════════════════════════════════════════
# BRAIN POINTERS (índices FAISS)
# ═══════════════════════════════════════════════════════════════════════

async def get_all_brain_pointers() -> Dict[str, str]:
    """Lee la versión activa de cada cerebro desde Irys."""
    result: Dict[str, str] = {code: f"{code}_v0" for code in BRAIN_CODES}
    for code in BRAIN_CODES:
        try:
            node = await _query_latest([
                {"name": "data-type",  "value": "brain-pointer"},
                {"name": "brain-code", "value": code},
            ])
            if node:
                version = _node_tags(node).get("brain-version", f"{code}_v0")
                result[code] = version
        except Exception:
            pass
    return result


async def update_brain_pointer_tag(code: str, version_name: str) -> None:
    """Sube un nuevo brain-pointer para un código específico."""
    tx_id = await _upload(b"{}", [
        {"name": "data-type",     "value": "brain-pointer"},
        {"name": "brain-code",    "value": code},
        {"name": "brain-version", "value": version_name},
        {"name": "Content-Type",  "value": "application/json"},
    ])
    logger.info("🧠 Brain pointer [%s] → %s. Ver dato: %s", code, version_name, _gw(tx_id))


async def upload_brain_index(code: str, version_name: str, binary: bytes) -> None:
    """Sube el binario FAISS a Irys."""
    tx_id = await _upload(binary, [
        {"name": "data-type",     "value": "brain-index"},
        {"name": "brain-code",    "value": code},
        {"name": "brain-version", "value": version_name},
        {"name": "Content-Type",  "value": "application/octet-stream"},
    ])
    logger.info("🧠 Brain index [%s] %s subido a Irys. Ver dato: %s", code, version_name, _gw(tx_id))


async def download_brain_index(code: str, version_name: str) -> Optional[bytes]:
    """Descarga el binario FAISS desde Irys."""
    try:
        node = await _query_latest([
            {"name": "data-type",     "value": "brain-index"},
            {"name": "brain-code",    "value": code},
            {"name": "brain-version", "value": version_name},
        ])
        if not node:
            return None
        return await _fetch(node["id"])
    except Exception as exc:
        logger.warning("download_brain_index [%s] %s falló: %s", code, version_name, exc)
        return None


async def upload_brain_meta(code: str, version_name: str, meta: Dict[str, Any]) -> None:
    """Sube los metadatos del cerebro a Irys."""
    content = json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8")
    tx_id = await _upload(content, [
        {"name": "data-type",     "value": "brain-meta"},
        {"name": "brain-code",    "value": code},
        {"name": "brain-version", "value": version_name},
        {"name": "Content-Type",  "value": "application/json"},
    ])
    logger.info("🧠 Brain meta [%s] %s subido a Irys. Ver dato: %s", code, version_name, _gw(tx_id))


async def download_brain_meta(code: str, version_name: str) -> Dict[str, Any]:
    """Descarga los metadatos del cerebro desde Irys."""
    try:
        node = await _query_latest([
            {"name": "data-type",     "value": "brain-meta"},
            {"name": "brain-code",    "value": code},
            {"name": "brain-version", "value": version_name},
        ])
        if not node:
            return {}
        raw = await _fetch(node["id"])
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {}


# Puntero único de versión (para fusion_brain.py)
_single_brain_pointer: str = "v0.0.0"


async def get_brain_pointer() -> str:
    """Lee el puntero global del cerebro desde Irys."""
    try:
        node = await _query_latest([{"name": "data-type", "value": "brain-pointer-global"}])
        if node:
            return _node_tags(node).get("brain-version", "v0.0.0")
    except Exception:
        pass
    return _single_brain_pointer


async def set_brain_pointer(version: str) -> None:
    """Sube un nuevo puntero global del cerebro a Irys."""
    global _single_brain_pointer
    tx_id = await _upload(b"{}", [
        {"name": "data-type",     "value": "brain-pointer-global"},
        {"name": "brain-version", "value": version},
        {"name": "Content-Type",  "value": "application/json"},
    ])
    _single_brain_pointer = version
    logger.info("🧠 Brain pointer global → %s. Ver dato: %s", version, _gw(tx_id))


# ═══════════════════════════════════════════════════════════════════════
# CHALLENGES SEMANALES
# ═══════════════════════════════════════════════════════════════════════

_challenge_cache: Optional[Dict[str, Any]] = None


async def load_current_challenge_from_irys() -> Optional[Dict[str, Any]]:
    """Carga el challenge activo desde Irys."""
    global _challenge_cache
    try:
        node = await _query_latest([{"name": "data-type", "value": "challenge"}])
        if node:
            raw = await _fetch(node["id"])
            data = json.loads(raw.decode("utf-8"))
            _challenge_cache = data
            logger.info("🎯 Challenge restaurado desde Irys: %s", data.get("id"))
    except Exception as exc:
        logger.warning("No se pudo cargar challenge desde Irys: %s", exc)
    return _challenge_cache


# Alias para compatibilidad con sync_brain.py
load_current_challenge_from_greenfield = load_current_challenge_from_irys


async def get_current_challenge() -> Optional[Dict[str, Any]]:
    return _challenge_cache


async def save_challenge(challenge: Dict[str, Any]) -> None:
    global _challenge_cache
    _challenge_cache = challenge
    try:
        content = json.dumps(challenge, ensure_ascii=False, indent=2).encode("utf-8")
        tx_id = await _upload(content, [
            {"name": "data-type",    "value": "challenge"},
            {"name": "challenge-id", "value": str(challenge.get("id", ""))},
            {"name": "Content-Type", "value": "application/json"},
        ])
        logger.info("🎯 Challenge guardado en Irys. Ver dato: %s", _gw(tx_id))
    except Exception as exc:
        logger.warning("Challenge en RAM pero no persistido en Irys: %s", exc)


# ═══════════════════════════════════════════════════════════════════════
# LOGS
# ═══════════════════════════════════════════════════════════════════════

async def upload_log(date_str: str, log_content: str) -> None:
    """Sube log diario comprimido a Irys."""
    compressed = gzip.compress(log_content.encode("utf-8"))
    tx_id = await _upload(compressed, [
        {"name": "data-type",    "value": "log"},
        {"name": "log-date",     "value": date_str},
        {"name": "Content-Type", "value": "application/gzip"},
    ])
    logger.info("📄 Log %s subido a Irys. Ver dato: %s", date_str, _gw(tx_id))


# ═══════════════════════════════════════════════════════════════════════
# PRUEBA DE IMPACTO REAL (PIR) — Módulo 5 (§7)
#
# Cada aporte tiene un contador de impacto vivo:
#   data-type=impact-counter  → contador acumulado por aporte (última
#                               versión gana, mismo patrón que user-profile).
#   data-type=impact-royalty  → registro PÚBLICO de cada regalía pagada
#                               {aporte_txId, impactos, SYNX_pagado} (§7.2).
# ═══════════════════════════════════════════════════════════════════════

@retry(
    retry=retry_if_exception_type((httpx.TransportError, ConnectionError, TimeoutError, OSError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
async def write_impact_counter(
    aporte_tx: str, author_hash: str, counts: Dict[str, Any]
) -> str:
    """Sella la versión vigente del contador de impacto de un aporte."""
    tags = [
        {"name": "data-type",  "value": "impact-counter"},
        {"name": "aporte-tx",  "value": aporte_tx},
        {"name": "author-uid", "value": author_hash},
        {"name": "views",      "value": str(int(counts.get("views", 0)))},
        {"name": "useful",     "value": str(int(counts.get("useful", 0)))},
        {"name": "references", "value": str(int(counts.get("references", 0)))},
        {"name": "royalty-blocks-paid", "value": str(int(counts.get("royalty_blocks_paid", 0)))},
        {"name": "synx-paid",  "value": f"{float(counts.get('synx_paid', 0.0)):.2f}"},
        {"name": "Content-Type", "value": "application/json"},
    ]
    tx_id = await _upload(b"{}", tags)
    logger.debug("📈 Impact counter %s sellado (tx=%s)", aporte_tx[:12], tx_id)
    return tx_id


async def read_impact_counter(aporte_tx: str) -> Optional[Dict[str, str]]:
    """Lee el contador de impacto vigente de un aporte (tags) o None."""
    try:
        node = await _query_latest([
            {"name": "data-type", "value": "impact-counter"},
            {"name": "aporte-tx", "value": aporte_tx},
        ])
        if node:
            return _node_tags(node)
    except Exception as exc:
        logger.warning("read_impact_counter %s falló: %s", aporte_tx[:12], exc)
    return None


@retry(
    retry=retry_if_exception_type((httpx.TransportError, ConnectionError, TimeoutError, OSError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
async def write_impact_royalty(
    aporte_tx: str, author_hash: str, impacts: int, synx: float
) -> str:
    """Registro público e inmutable de una regalía pagada (§7.2/§7.4)."""
    body = {
        "aporte_txId": aporte_tx,
        "author": author_hash,
        "impactos": impacts,
        "SYNX_pagado": synx,
        "paid_at": int(datetime.now(timezone.utc).timestamp()),
    }
    tags = [
        {"name": "data-type",  "value": "impact-royalty"},
        {"name": "aporte-tx",  "value": aporte_tx},
        {"name": "author-uid", "value": author_hash},
        {"name": "synx",       "value": f"{synx:.2f}"},
        {"name": "Content-Type", "value": "application/json"},
    ]
    tx_id = await _upload(json.dumps(body).encode("utf-8"), tags)
    logger.info(
        "💫 Regalía PIR: aporte=%s autor=%s +%.2f SYNX. Ver dato: %s",
        aporte_tx[:12], author_hash, synx, _gw(tx_id),
    )
    return tx_id


# ═══════════════════════════════════════════════════════════════════════
# WALLET CUSTODIAL — keystore V3 cifrado
#
# El keystore se cifra en services/wallet.py ANTES de llegar aquí (Irys es
# público y permanente: jamás debe subirse una clave en claro).  El patrón
# es el habitual: última versión por uid-hash gana.
# ═══════════════════════════════════════════════════════════════════════

@retry(
    retry=retry_if_exception_type((httpx.TransportError, ConnectionError, TimeoutError, OSError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
async def write_custodial_wallet(
    uid_ofuscado: str, address: str, keystore: Dict[str, Any]
) -> str:
    """Sella el keystore V3 CIFRADO de la wallet custodial de un usuario."""
    content = json.dumps(keystore, ensure_ascii=False).encode("utf-8")
    tags = [
        {"name": "data-type",      "value": "custodial-wallet"},
        {"name": "uid-hash",       "value": uid_ofuscado},
        {"name": "wallet-address", "value": address},
        {"name": "Content-Type",   "value": "application/json"},
    ]
    tx_id = await _upload(content, tags)
    logger.info(
        "👛 Keystore custodial de %s sellado en Irys (addr=%s). Ver dato: %s",
        uid_ofuscado, address, _gw(tx_id),
    )
    return tx_id


async def read_custodial_wallet(uid_ofuscado: str) -> Optional[Dict[str, Any]]:
    """Lee la wallet custodial vigente: {"address": str, "keystore": dict} o None."""
    try:
        node = await _query_latest([
            {"name": "data-type", "value": "custodial-wallet"},
            {"name": "uid-hash",  "value": uid_ofuscado},
        ])
        if not node:
            return None
        address = _node_tags(node).get("wallet-address", "")
        raw = await _fetch(node["id"])
        keystore = json.loads(raw.decode("utf-8"))
        return {"address": address, "keystore": keystore}
    except Exception as exc:
        logger.warning("read_custodial_wallet %s falló: %s", uid_ofuscado, exc)
        return None


# ═══════════════════════════════════════════════════════════════════════
# NODOS DE COMUNIDAD
#
# Un nodo es una comunidad temática/geográfica con su propio grafo de
# conocimiento dentro de Synergix.  Se modela con dos DataItems inmutables:
#
#   data-type=node         → registro del nodo (id, nombre, tipo, idioma,
#                            temas, creador).  Mutable por patrón "última
#                            versión gana" (mismo node-id, nuevo timestamp).
#   data-type=node-member  → membresía (node-id, uid-hash, rol, estado).
#                            Última versión por (node-id, uid-hash) gana, así
#                            "unirse"/"salir" se expresan re-escribiendo el rol.
#
# Los aportes hechos dentro de un nodo llevan además el tag `node-id` y
# `topic`, lo que permite calcular la cobertura de conocimiento del nodo.
# ═══════════════════════════════════════════════════════════════════════

def _dedupe_latest(nodes: List[Dict[str, Any]], key_tag: str) -> List[Dict[str, str]]:
    """Devuelve los tags de la versión más reciente por cada valor de ``key_tag``.

    ``nodes`` ya viene ordenado DESC por timestamp (cortesía de _query_all),
    así que la primera aparición de cada clave es la vigente.
    """
    seen: Set[str] = set()
    out: List[Dict[str, str]] = []
    for node in nodes:
        tags = _node_tags(node)
        key = tags.get(key_tag, "")
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(tags)
    return out


@retry(
    retry=retry_if_exception_type((httpx.TransportError, ConnectionError, TimeoutError, OSError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
async def write_node(
    node_id: str,
    name: str,
    node_type: str,
    creator_hash: str,
    language: str,
    topics: List[str],
    country: str = "",
    region: str = "",
    geo_scope: str = "",
    extra: Optional[Dict[str, Any]] = None,
) -> str:
    """Sube (o re-versiona) el registro de un nodo a Irys. Retorna el txId."""
    body = {
        "node_id": node_id,
        "name": name,
        "node_type": node_type,
        "creator": creator_hash,
        "language": language,
        "topics": topics,
        "country": country,
        "region": region,
        "geo_scope": geo_scope,
        "created_at": int(datetime.now(timezone.utc).timestamp()),
        **(extra or {}),
    }
    tags = [
        {"name": "data-type",    "value": "node"},
        {"name": "node-id",      "value": node_id},
        {"name": "node-name",    "value": name[:120]},
        {"name": "node-type",    "value": node_type},
        {"name": "creator",      "value": creator_hash},
        {"name": "language",     "value": language},
        {"name": "topics",       "value": ",".join(topics)[:300]},
        # Ubicación territorial (país → granularidad → lugar); vacía en nodos
        # a-geográficos.
        {"name": "country",      "value": (country or "")[:10]},
        {"name": "region",       "value": (region or "")[:80]},
        {"name": "geo-scope",    "value": (geo_scope or "")[:20]},
        {"name": "Content-Type", "value": "application/json"},
    ]
    content = json.dumps(body, ensure_ascii=False).encode("utf-8")
    tx_id = await _upload(content, tags)
    logger.info("🏘️ Nodo %s guardado en Irys. Ver dato: %s", node_id, _gw(tx_id))
    return tx_id


async def get_node_record(node_id: str) -> Optional[Dict[str, str]]:
    """Lee el registro vigente de un nodo por su id. None si no existe."""
    try:
        node = await _query_latest([
            {"name": "data-type", "value": "node"},
            {"name": "node-id",   "value": node_id},
        ])
        if node:
            return _node_tags(node)
    except Exception as exc:
        logger.warning("get_node_record %s falló: %s", node_id, exc)
    return None


async def list_node_records(limit: int = 200) -> List[Dict[str, str]]:
    """Lista todos los nodos (versión vigente de cada uno), más nuevos primero."""
    try:
        nodes = await _query_all([{"name": "data-type", "value": "node"}], limit=limit)
        return _dedupe_latest(nodes, "node-id")
    except Exception as exc:
        logger.warning("list_node_records falló: %s", exc)
        return []


@retry(
    retry=retry_if_exception_type((httpx.TransportError, ConnectionError, TimeoutError, OSError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
async def write_node_member(
    node_id: str, uid_ofuscado: str, role: str = "member", status: str = "active"
) -> str:
    """Escribe una membresía (o la re-versiona para salir/cambiar rol)."""
    tags = [
        {"name": "data-type",    "value": "node-member"},
        {"name": "node-id",      "value": node_id},
        {"name": "uid-hash",     "value": uid_ofuscado},
        {"name": "role",         "value": role},
        {"name": "member-status", "value": status},
        {"name": "joined-at",    "value": str(int(datetime.now(timezone.utc).timestamp()))},
        {"name": "Content-Type", "value": "application/json"},
    ]
    tx_id = await _upload(b"{}", tags)
    logger.info("👥 Membresía %s@%s (%s/%s) en Irys.", uid_ofuscado, node_id, role, status)
    return tx_id


async def get_node_member(node_id: str, uid_ofuscado: str) -> Optional[Dict[str, str]]:
    """Lee la membresía vigente de un usuario en un nodo (o None)."""
    try:
        node = await _query_latest([
            {"name": "data-type", "value": "node-member"},
            {"name": "node-id",   "value": node_id},
            {"name": "uid-hash",  "value": uid_ofuscado},
        ])
        if node:
            return _node_tags(node)
    except Exception as exc:
        logger.warning("get_node_member %s@%s falló: %s", uid_ofuscado, node_id, exc)
    return None


async def list_node_members(node_id: str, limit: int = 1000) -> List[Dict[str, str]]:
    """Miembros activos de un nodo (última membresía por usuario con estado active)."""
    try:
        nodes = await _query_all([
            {"name": "data-type", "value": "node-member"},
            {"name": "node-id",   "value": node_id},
        ], limit=limit)
        latest = _dedupe_latest(nodes, "uid-hash")
        return [m for m in latest if m.get("member-status", "active") == "active"]
    except Exception as exc:
        logger.warning("list_node_members %s falló: %s", node_id, exc)
        return []


async def list_user_memberships(uid_ofuscado: str, limit: int = 500) -> List[Dict[str, str]]:
    """Nodos a los que pertenece un usuario (membresías activas, última por nodo)."""
    try:
        nodes = await _query_all([
            {"name": "data-type", "value": "node-member"},
            {"name": "uid-hash",  "value": uid_ofuscado},
        ], limit=limit)
        latest = _dedupe_latest(nodes, "node-id")
        return [m for m in latest if m.get("member-status", "active") == "active"]
    except Exception as exc:
        logger.warning("list_user_memberships %s falló: %s", uid_ofuscado, exc)
        return []


async def list_node_aportes(node_id: str, limit: int = 2000) -> List[Dict[str, str]]:
    """Lista los aportes asociados a un nodo (tags de cada aporte)."""
    try:
        nodes = await _query_all([
            {"name": "data-type", "value": "aporte"},
            {"name": "node-id",   "value": node_id},
        ], limit=limit)
        # Incluye el tx id del aporte (clave del contador de impacto).
        return [{**_node_tags(n), "id": n.get("id", "")} for n in nodes]
    except Exception as exc:
        logger.warning("list_node_aportes %s falló: %s", node_id, exc)
        return []


async def count_node_aportes(node_id: str) -> int:
    """Número de aportes asociados a un nodo."""
    return len(await list_node_aportes(node_id))


# ── Bonds de nodo (SYNERGIX real bloqueado — Fase A) ──────────────────────
# Crear un nodo exige bloquear un bond de SYNERGIX real en la wallet custodial
# del creador.  El token NO se mueve: se marca como bloqueado con un DataItem
# node-bond (última versión por node-id gana).  Retiro y venta descuentan el
# bond bloqueado del saldo disponible.

@retry(
    retry=retry_if_exception_type((httpx.TransportError, ConnectionError, TimeoutError, OSError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
async def write_node_bond(
    node_id: str, uid_ofuscado: str, amount: float, status: str,
    unbond_until: int = 0,
) -> str:
    """Sella (o re-versiona) el bond de un nodo. status: locked|unbonding|released|slashed."""
    tags = [
        {"name": "data-type",    "value": "node-bond"},
        {"name": "node-id",      "value": node_id},
        {"name": "uid-hash",     "value": uid_ofuscado},
        {"name": "amount",       "value": f"{amount:.4f}"},
        {"name": "bond-status",  "value": status},
        {"name": "unbond-until", "value": str(int(unbond_until))},
        {"name": "created-ts",   "value": str(int(datetime.now(timezone.utc).timestamp()))},
        {"name": "Content-Type", "value": "application/json"},
    ]
    tx_id = await _upload(b"{}", tags)
    logger.info("🔒 Bond de nodo %s (%s, %.0f SYNERGIX) → %s en Irys.",
                node_id, status, amount, uid_ofuscado)
    return tx_id


async def get_node_bond(node_id: str) -> Optional[Dict[str, str]]:
    """Bond vigente de un nodo (tags) o None."""
    try:
        node = await _query_latest([
            {"name": "data-type", "value": "node-bond"},
            {"name": "node-id",   "value": node_id},
        ])
        if node:
            return _node_tags(node)
    except Exception as exc:
        logger.warning("get_node_bond %s falló: %s", node_id, exc)
    return None


async def list_user_bonds(uid_ofuscado: str, limit: int = 500) -> List[Dict[str, str]]:
    """Bonds del usuario (última versión por nodo)."""
    try:
        nodes = await _query_all([
            {"name": "data-type", "value": "node-bond"},
            {"name": "uid-hash",  "value": uid_ofuscado},
        ], limit=limit)
        return _dedupe_latest(nodes, "node-id")
    except Exception as exc:
        logger.warning("list_user_bonds %s falló: %s", uid_ofuscado, exc)
        return []


# ── Canjes / redenciones (SYNX contable → SYNERGIX real) — Fase B/C ───────
# Registro APPEND-ONLY de cada solicitud de canje.  Sirve de (1) auditoría
# pública, (2) fuente para los límites por-usuario y por-dirección del gate
# anti-Sybil, y (3) ledger de idempotencia para el pago (Fase C).

@retry(
    retry=retry_if_exception_type((httpx.TransportError, ConnectionError, TimeoutError, OSError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
async def write_redemption(
    redemption_id: str, uid_ofuscado: str, address: str, amount: float,
    status: str, tx: str = "", synergix: float = 0.0,
) -> str:
    """Sella una redención. status: requested|paid|rejected. tx: hash on-chain (al pagar).

    ``amount`` = SYNX canjeado; ``synergix`` = SYNERGIX real pagado (para el
    presupuesto de emisión).
    """
    tags = [
        {"name": "data-type",      "value": "redemption"},
        {"name": "redemption-id",  "value": redemption_id},
        {"name": "uid-hash",       "value": uid_ofuscado},
        {"name": "address",        "value": address.lower()},
        {"name": "amount",         "value": f"{amount:.4f}"},
        {"name": "synergix",       "value": f"{synergix:.4f}"},
        {"name": "redeem-status",  "value": status},
        {"name": "ts",             "value": str(int(datetime.now(timezone.utc).timestamp()))},
        {"name": "tx",             "value": tx},
        {"name": "Content-Type",   "value": "application/json"},
    ]
    tx_id = await _upload(b"{}", tags)
    logger.info("💱 Redención %s (%s, %.2f SYNX → %s) sellada.",
                redemption_id, status, amount, address)
    return tx_id


async def get_redemption(redemption_id: str) -> Optional[Dict[str, str]]:
    """Última versión de una redención (para idempotencia del pago)."""
    try:
        node = await _query_latest([
            {"name": "data-type",     "value": "redemption"},
            {"name": "redemption-id", "value": redemption_id},
        ])
        if node:
            return _node_tags(node)
    except Exception as exc:
        logger.warning("get_redemption %s falló: %s", redemption_id, exc)
    return None


async def list_user_redemptions(uid_ofuscado: str, limit: int = 500) -> List[Dict[str, str]]:
    """Todas las redenciones de un usuario (append-only; el caller filtra por ventana)."""
    try:
        nodes = await _query_all([
            {"name": "data-type", "value": "redemption"},
            {"name": "uid-hash",  "value": uid_ofuscado},
        ], limit=limit)
        return [_node_tags(n) for n in nodes]
    except Exception as exc:
        logger.warning("list_user_redemptions %s falló: %s", uid_ofuscado, exc)
        return []


async def list_address_redemptions(address: str, limit: int = 1000) -> List[Dict[str, str]]:
    """Todas las redenciones hacia una dirección de pago (defensa Sybil por dirección)."""
    try:
        nodes = await _query_all([
            {"name": "data-type", "value": "redemption"},
            {"name": "address",   "value": address.lower()},
        ], limit=limit)
        return [_node_tags(n) for n in nodes]
    except Exception as exc:
        logger.warning("list_address_redemptions %s falló: %s", address, exc)
        return []


async def list_all_redemptions(limit: int = 5000) -> List[Dict[str, str]]:
    """Última versión de cada redención (dedupe por id) — para el presupuesto diario."""
    try:
        nodes = await _query_all([{"name": "data-type", "value": "redemption"}], limit=limit)
        return _dedupe_latest(nodes, "redemption-id")
    except Exception as exc:
        logger.warning("list_all_redemptions falló: %s", exc)
        return []


# ═══════════════════════════════════════════════════════════════════════
# ECONOMÍA VIVA (Fase 2): PROVEEDORES · PROYECTOS · ORÁCULOS
#
#   data-type=provider       → profesional verificado de un nodo (última
#                              versión por uid gana; status active|inactive).
#   data-type=project        → proyecto de financiamiento colectivo (última
#                              versión por project-id gana; el status es la
#                              máquina de estados: active→voting→completed|refunded).
#   data-type=project-fund   → contribución SYNX a un proyecto (append-only).
#   data-type=project-vote   → voto de un financiador (última por uid gana).
#   data-type=oracle-stake   → stake de un Juez Oráculo (última por uid gana).
#   data-type=oracle-review  → revisión de un aporte score≥8 (keyed por
#                              aporte-tx; status pending→approved|rejected|expired).
#   data-type=oracle-vote    → voto 👍/👎 de un oráculo (última por uid gana).
# ═══════════════════════════════════════════════════════════════════════

@retry(
    retry=retry_if_exception_type((httpx.TransportError, ConnectionError, TimeoutError, OSError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
async def write_provider(
    node_id: str, uid_hash: str, category: str, description: str,
    status: str = "active",
) -> str:
    tags = [
        {"name": "data-type",       "value": "provider"},
        {"name": "node-id",         "value": node_id},
        {"name": "uid-hash",        "value": uid_hash},
        {"name": "category",        "value": category},
        {"name": "description",     "value": (description or "")[:200]},
        {"name": "provider-status", "value": status},
        {"name": "Content-Type",    "value": "application/json"},
    ]
    tx_id = await _upload(b"{}", tags)
    logger.info("💼 Proveedor %s@%s (%s) sellado en Irys.", uid_hash, node_id, category)
    return tx_id


@retry(
    retry=retry_if_exception_type((httpx.TransportError, ConnectionError, TimeoutError, OSError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
async def write_payment(
    node_id: str, from_hash: str, to_hash: str, amount: float, memo: str = "",
) -> str:
    """Registro append-only de un pago SYNX entre dos usuarios (§6.2, uso 2)."""
    tags = [
        {"name": "data-type",  "value": "synx-payment"},
        {"name": "node-id",    "value": node_id},
        {"name": "from-hash",  "value": from_hash},
        {"name": "to-hash",    "value": to_hash},
        {"name": "amount",     "value": f"{amount:.2f}"},
        {"name": "memo",       "value": (memo or "")[:120]},
        {"name": "Content-Type", "value": "application/json"},
    ]
    tx_id = await _upload(b"{}", tags)
    logger.info("💸 Pago SYNX %.2f: %s → %s (%s)", amount, from_hash, to_hash, node_id)
    return tx_id


async def list_node_providers(node_id: str, limit: int = 500) -> List[Dict[str, str]]:
    """Proveedores activos de un nodo (última versión por usuario)."""
    try:
        nodes = await _query_all([
            {"name": "data-type", "value": "provider"},
            {"name": "node-id",   "value": node_id},
        ], limit=limit)
        latest = _dedupe_latest(nodes, "uid-hash")
        return [p for p in latest if p.get("provider-status", "active") == "active"]
    except Exception as exc:
        logger.warning("list_node_providers %s falló: %s", node_id, exc)
        return []


@retry(
    retry=retry_if_exception_type((httpx.TransportError, ConnectionError, TimeoutError, OSError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
async def write_project(
    project_id: str, node_id: str, creator_hash: str, title: str,
    goal: float, status: str, voting_until: int = 0, evidence: str = "",
) -> str:
    tags = [
        {"name": "data-type",      "value": "project"},
        {"name": "project-id",     "value": project_id},
        {"name": "node-id",        "value": node_id},
        {"name": "creator",        "value": creator_hash},
        {"name": "title",          "value": (title or "")[:120]},
        {"name": "goal",           "value": f"{goal:.2f}"},
        {"name": "project-status", "value": status},
        {"name": "voting-until",   "value": str(int(voting_until))},
        # Evidencia de cumplimiento (hitos/documentación) que el creador debe
        # aportar antes de que se libere el escrow (verificación obligatoria).
        {"name": "evidence",       "value": (evidence or "")[:400]},
        {"name": "Content-Type",   "value": "application/json"},
    ]
    tx_id = await _upload(b"{}", tags)
    logger.info("🏗️ Proyecto %s (%s) → %s en Irys.", project_id, status, _gw(tx_id))
    return tx_id


async def read_project(project_id: str) -> Optional[Dict[str, str]]:
    try:
        node = await _query_latest([
            {"name": "data-type",  "value": "project"},
            {"name": "project-id", "value": project_id},
        ])
        if node:
            return _node_tags(node)
    except Exception as exc:
        logger.warning("read_project %s falló: %s", project_id, exc)
    return None


async def list_node_projects(node_id: str, limit: int = 200) -> List[Dict[str, str]]:
    try:
        nodes = await _query_all([
            {"name": "data-type", "value": "project"},
            {"name": "node-id",   "value": node_id},
        ], limit=limit)
        return _dedupe_latest(nodes, "project-id")
    except Exception as exc:
        logger.warning("list_node_projects %s falló: %s", node_id, exc)
        return []


@retry(
    retry=retry_if_exception_type((httpx.TransportError, ConnectionError, TimeoutError, OSError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
async def write_project_fund(project_id: str, uid_hash: str, amount: float) -> str:
    """Registro append-only de una contribución al escrow de un proyecto."""
    tags = [
        {"name": "data-type",  "value": "project-fund"},
        {"name": "project-id", "value": project_id},
        {"name": "uid-hash",   "value": uid_hash},
        {"name": "amount",     "value": f"{amount:.2f}"},
        {"name": "Content-Type", "value": "application/json"},
    ]
    return await _upload(b"{}", tags)


async def list_project_funds(project_id: str, limit: int = 1000) -> List[Dict[str, str]]:
    """TODAS las contribuciones de un proyecto (append-only, sin dedupe)."""
    try:
        nodes = await _query_all([
            {"name": "data-type",  "value": "project-fund"},
            {"name": "project-id", "value": project_id},
        ], limit=limit)
        return [_node_tags(n) for n in nodes]
    except Exception as exc:
        logger.warning("list_project_funds %s falló: %s", project_id, exc)
        return []


@retry(
    retry=retry_if_exception_type((httpx.TransportError, ConnectionError, TimeoutError, OSError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
async def write_project_vote(project_id: str, uid_hash: str, vote: bool) -> str:
    tags = [
        {"name": "data-type",  "value": "project-vote"},
        {"name": "project-id", "value": project_id},
        {"name": "uid-hash",   "value": uid_hash},
        {"name": "vote",       "value": "yes" if vote else "no"},
        {"name": "Content-Type", "value": "application/json"},
    ]
    return await _upload(b"{}", tags)


async def list_project_votes(project_id: str, limit: int = 1000) -> List[Dict[str, str]]:
    """Voto vigente de cada financiador (última versión por usuario)."""
    try:
        nodes = await _query_all([
            {"name": "data-type",  "value": "project-vote"},
            {"name": "project-id", "value": project_id},
        ], limit=limit)
        return _dedupe_latest(nodes, "uid-hash")
    except Exception as exc:
        logger.warning("list_project_votes %s falló: %s", project_id, exc)
        return []


# ── Bounties de conocimiento (Proof-of-Knowledge, §7.4 ampliado) ──────────
# Un patrocinador financia un pool para llenar un vacío (nodo+tema). Cada
# aporte verificado a ese tema paga una recompensa fija hasta agotar el pool.
# El registro `bounty` es versionado (última versión gana: estado + pagados);
# `bounty-claim` es append-only (un pago por aporte, idempotente por tx).

@retry(
    retry=retry_if_exception_type((httpx.TransportError, ConnectionError, TimeoutError, OSError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
async def write_bounty(
    bounty_id: str, node_id: str, topic: str, sponsor_hash: str,
    pool: float, reward: float, per_user: int, deadline: int,
    status: str, paid_count: int = 0,
) -> str:
    tags = [
        {"name": "data-type",     "value": "bounty"},
        {"name": "bounty-id",     "value": bounty_id},
        {"name": "node-id",       "value": node_id},
        {"name": "topic",         "value": (topic or "")[:60]},
        {"name": "sponsor",       "value": sponsor_hash},
        {"name": "pool",          "value": f"{pool:.2f}"},
        {"name": "reward",        "value": f"{reward:.2f}"},
        {"name": "per-user",      "value": str(int(per_user))},
        {"name": "deadline",      "value": str(int(deadline))},
        {"name": "bounty-status", "value": status},
        {"name": "paid-count",    "value": str(int(paid_count))},
        {"name": "Content-Type",  "value": "application/json"},
    ]
    tx_id = await _upload(b"{}", tags)
    logger.info("🎯 Bounty %s (%s, pool %.0f) sellado en Irys.", bounty_id, status, pool)
    return tx_id


async def read_bounty(bounty_id: str) -> Optional[Dict[str, str]]:
    try:
        node = await _query_latest([
            {"name": "data-type", "value": "bounty"},
            {"name": "bounty-id", "value": bounty_id},
        ])
        if node:
            return _node_tags(node)
    except Exception as exc:
        logger.warning("read_bounty %s falló: %s", bounty_id, exc)
    return None


async def list_node_bounties(node_id: str, limit: int = 200) -> List[Dict[str, str]]:
    try:
        nodes = await _query_all([
            {"name": "data-type", "value": "bounty"},
            {"name": "node-id",   "value": node_id},
        ], limit=limit)
        return _dedupe_latest(nodes, "bounty-id")
    except Exception as exc:
        logger.warning("list_node_bounties %s falló: %s", node_id, exc)
        return []


@retry(
    retry=retry_if_exception_type((httpx.TransportError, ConnectionError, TimeoutError, OSError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
async def write_bounty_claim(
    bounty_id: str, author_hash: str, aporte_tx: str, amount: float,
) -> str:
    """Pago append-only de un aporte verificado contra un bounty."""
    tags = [
        {"name": "data-type",  "value": "bounty-claim"},
        {"name": "bounty-id",  "value": bounty_id},
        {"name": "author-uid", "value": author_hash},
        {"name": "aporte-tx",  "value": aporte_tx},
        {"name": "amount",     "value": f"{amount:.2f}"},
        {"name": "Content-Type", "value": "application/json"},
    ]
    return await _upload(b"{}", tags)


async def list_bounty_claims(bounty_id: str, limit: int = 2000) -> List[Dict[str, str]]:
    """TODOS los pagos de un bounty (append-only, sin dedupe)."""
    try:
        nodes = await _query_all([
            {"name": "data-type", "value": "bounty-claim"},
            {"name": "bounty-id", "value": bounty_id},
        ], limit=limit)
        return [_node_tags(n) for n in nodes]
    except Exception as exc:
        logger.warning("list_bounty_claims %s falló: %s", bounty_id, exc)
        return []


async def list_bounty_claims_by_author(author_hash: str, limit: int = 1000) -> List[Dict[str, str]]:
    """Todos los bounties que un autor ha cobrado (para el Passport)."""
    try:
        nodes = await _query_all([
            {"name": "data-type",  "value": "bounty-claim"},
            {"name": "author-uid", "value": author_hash},
        ], limit=limit)
        return [_node_tags(n) for n in nodes]
    except Exception as exc:
        logger.warning("list_bounty_claims_by_author %s falló: %s", author_hash, exc)
        return []


# ── API de Conocimiento que paga a humanos (Proof-of-Knowledge ③) ─────────
# `api-key` versionado (última versión = saldo vigente); `api-usage`
# versionado (pending → settled) para la liquidación idempotente.

@retry(
    retry=retry_if_exception_type((httpx.TransportError, ConnectionError, TimeoutError, OSError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
async def write_api_key(key_hash: str, owner: str, balance: float, status: str = "active") -> str:
    tags = [
        {"name": "data-type",  "value": "api-key"},
        {"name": "key-hash",   "value": key_hash},
        {"name": "owner",      "value": owner},
        {"name": "balance",    "value": f"{balance:.4f}"},
        {"name": "key-status", "value": status},
        {"name": "Content-Type", "value": "application/json"},
    ]
    return await _upload(b"{}", tags)


async def read_api_key(key_hash: str) -> Optional[Dict[str, str]]:
    try:
        node = await _query_latest([
            {"name": "data-type", "value": "api-key"},
            {"name": "key-hash",  "value": key_hash},
        ])
        if node:
            return _node_tags(node)
    except Exception as exc:
        logger.warning("read_api_key falló: %s", exc)
    return None


@retry(
    retry=retry_if_exception_type((httpx.TransportError, ConnectionError, TimeoutError, OSError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
async def write_api_usage(
    usage_id: str, key_hash: str, price: float, authors: str, status: str = "pending",
) -> str:
    tags = [
        {"name": "data-type",    "value": "api-usage"},
        {"name": "usage-id",     "value": usage_id},
        {"name": "key-hash",     "value": key_hash},
        {"name": "price",        "value": f"{price:.4f}"},
        {"name": "authors",      "value": authors[:1000]},
        {"name": "usage-status", "value": status},
        {"name": "Content-Type", "value": "application/json"},
    ]
    return await _upload(b"{}", tags)


# ── Atlas de Problemas y Soluciones (nodos territoriales) ─────────────────
# `problem` versionado por problem-id (open → solving → solved, última versión
# gana); `problem-confirm` append-only con kind=problem|solution.

@retry(
    retry=retry_if_exception_type((httpx.TransportError, ConnectionError, TimeoutError, OSError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
async def write_problem(
    problem_id: str, node_id: str, reporter_hash: str, text: str,
    status: str, solver: str = "", solution: str = "",
) -> str:
    tags = [
        {"name": "data-type",      "value": "problem"},
        {"name": "problem-id",     "value": problem_id},
        {"name": "node-id",        "value": node_id},
        {"name": "reporter",       "value": reporter_hash},
        {"name": "problem-text",   "value": (text or "")[:300]},
        {"name": "problem-status", "value": status},
        {"name": "solver",         "value": solver},
        {"name": "solution",       "value": (solution or "")[:300]},
        {"name": "Content-Type",   "value": "application/json"},
    ]
    tx_id = await _upload(b"{}", tags)
    logger.info("🚩 Problema %s (%s) sellado en Irys.", problem_id, status)
    return tx_id


async def read_problem(problem_id: str) -> Optional[Dict[str, str]]:
    try:
        node = await _query_latest([
            {"name": "data-type",  "value": "problem"},
            {"name": "problem-id", "value": problem_id},
        ])
        if node:
            return _node_tags(node)
    except Exception as exc:
        logger.warning("read_problem %s falló: %s", problem_id, exc)
    return None


async def list_node_problems(node_id: str, limit: int = 200) -> List[Dict[str, str]]:
    try:
        nodes = await _query_all([
            {"name": "data-type", "value": "problem"},
            {"name": "node-id",   "value": node_id},
        ], limit=limit)
        return _dedupe_latest(nodes, "problem-id")
    except Exception as exc:
        logger.warning("list_node_problems %s falló: %s", node_id, exc)
        return []


async def list_solved_problems(limit: int = 500) -> List[Dict[str, str]]:
    """Problemas resueltos de TODA la red (para el atlas público)."""
    try:
        nodes = await _query_all([
            {"name": "data-type", "value": "problem"},
        ], limit=limit)
        latest = _dedupe_latest(nodes, "problem-id")
        return [p for p in latest if p.get("problem-status") == "solved"]
    except Exception as exc:
        logger.warning("list_solved_problems falló: %s", exc)
        return []


@retry(
    retry=retry_if_exception_type((httpx.TransportError, ConnectionError, TimeoutError, OSError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
async def write_problem_confirm(problem_id: str, uid_hash: str, kind: str) -> str:
    """Confirmación append-only: kind=problem (es real) | solution (funciona)."""
    tags = [
        {"name": "data-type",   "value": "problem-confirm"},
        {"name": "problem-id",  "value": problem_id},
        {"name": "uid-hash",    "value": uid_hash},
        {"name": "confirm-kind", "value": kind},
        {"name": "Content-Type", "value": "application/json"},
    ]
    return await _upload(b"{}", tags)


async def list_problem_confirms(problem_id: str, kind: str, limit: int = 1000) -> List[Dict[str, str]]:
    try:
        nodes = await _query_all([
            {"name": "data-type",   "value": "problem-confirm"},
            {"name": "problem-id",  "value": problem_id},
            {"name": "confirm-kind", "value": kind},
        ], limit=limit)
        return [_node_tags(n) for n in nodes]
    except Exception as exc:
        logger.warning("list_problem_confirms %s falló: %s", problem_id, exc)
        return []


# ── Synergix Academy: micro-credenciales de aprendizaje ──────────────────
# `credential` es versionada por (uid-hash, domain): última versión gana con
# el nivel y las lecciones aprobadas. `lesson-result` es append-only (audit).

@retry(
    retry=retry_if_exception_type((httpx.TransportError, ConnectionError, TimeoutError, OSError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
async def write_credential(
    uid_hash: str, domain: str, level: int, lessons_passed: int,
) -> str:
    tags = [
        {"name": "data-type",      "value": "credential"},
        {"name": "uid-hash",       "value": uid_hash},
        {"name": "domain",         "value": (domain or "")[:40]},
        {"name": "level",          "value": str(int(level))},
        {"name": "lessons-passed", "value": str(int(lessons_passed))},
        {"name": "Content-Type",   "value": "application/json"},
    ]
    tx_id = await _upload(b"{}", tags)
    logger.info("🎖️ Credencial %s/%s nivel %d sellada en Irys.", uid_hash, domain, level)
    return tx_id


async def read_credential(uid_hash: str, domain: str) -> Optional[Dict[str, str]]:
    try:
        node = await _query_latest([
            {"name": "data-type", "value": "credential"},
            {"name": "uid-hash",  "value": uid_hash},
            {"name": "domain",    "value": domain},
        ])
        if node:
            return _node_tags(node)
    except Exception as exc:
        logger.warning("read_credential %s/%s falló: %s", uid_hash, domain, exc)
    return None


async def list_credentials(uid_hash: str, limit: int = 200) -> List[Dict[str, str]]:
    """Credenciales vigentes de un usuario (última versión por dominio)."""
    try:
        nodes = await _query_all([
            {"name": "data-type", "value": "credential"},
            {"name": "uid-hash",  "value": uid_hash},
        ], limit=limit)
        return _dedupe_latest(nodes, "domain")
    except Exception as exc:
        logger.warning("list_credentials %s falló: %s", uid_hash, exc)
        return []


@retry(
    retry=retry_if_exception_type((httpx.TransportError, ConnectionError, TimeoutError, OSError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
async def write_lesson_result(
    uid_hash: str, domain: str, score: float, passed: bool,
) -> str:
    tags = [
        {"name": "data-type", "value": "lesson-result"},
        {"name": "uid-hash",  "value": uid_hash},
        {"name": "domain",    "value": (domain or "")[:40]},
        {"name": "score",     "value": f"{float(score):.1f}"},
        {"name": "passed",    "value": "yes" if passed else "no"},
        {"name": "Content-Type", "value": "application/json"},
    ]
    return await _upload(b"{}", tags)


async def list_pending_api_usage(limit: int = 200) -> List[Dict[str, str]]:
    """Eventos de uso de la API aún no liquidados (última versión = pending)."""
    try:
        nodes = await _query_all([
            {"name": "data-type", "value": "api-usage"},
        ], limit=limit)
        latest = _dedupe_latest(nodes, "usage-id")
        return [u for u in latest if u.get("usage-status", "pending") == "pending"]
    except Exception as exc:
        logger.warning("list_pending_api_usage falló: %s", exc)
        return []


@retry(
    retry=retry_if_exception_type((httpx.TransportError, ConnectionError, TimeoutError, OSError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
async def write_oracle_stake(
    uid_hash: str, amount: float, status: str, wrong_streak: int = 0,
    votes_total: int = 0, votes_correct: int = 0,
) -> str:
    tags = [
        {"name": "data-type",    "value": "oracle-stake"},
        {"name": "uid-hash",     "value": uid_hash},
        {"name": "amount",       "value": f"{amount:.2f}"},
        {"name": "stake-status", "value": status},
        {"name": "wrong-streak", "value": str(int(wrong_streak))},
        # Reputación como juez (Passport §10.2): tasa de acierto.
        {"name": "votes-total",   "value": str(int(votes_total))},
        {"name": "votes-correct", "value": str(int(votes_correct))},
        {"name": "Content-Type", "value": "application/json"},
    ]
    tx_id = await _upload(b"{}", tags)
    logger.info("🔮 Stake de oráculo %s (%s, %.0f SYNX) sellado.", uid_hash, status, amount)
    return tx_id


async def read_oracle_stake(uid_hash: str) -> Optional[Dict[str, str]]:
    try:
        node = await _query_latest([
            {"name": "data-type", "value": "oracle-stake"},
            {"name": "uid-hash",  "value": uid_hash},
        ])
        if node:
            return _node_tags(node)
    except Exception as exc:
        logger.warning("read_oracle_stake %s falló: %s", uid_hash, exc)
    return None


@retry(
    retry=retry_if_exception_type((httpx.TransportError, ConnectionError, TimeoutError, OSError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
async def write_oracle_review(
    aporte_tx: str, author_hash: str, base_synx: float, status: str,
    created_ts: int, votes: int = 0,
) -> str:
    tags = [
        {"name": "data-type",     "value": "oracle-review"},
        {"name": "aporte-tx",     "value": aporte_tx},
        {"name": "author-uid",    "value": author_hash},
        {"name": "base-synx",     "value": f"{base_synx:.2f}"},
        {"name": "review-status", "value": status},
        {"name": "created-ts",    "value": str(int(created_ts))},
        {"name": "votes",         "value": str(int(votes))},
        {"name": "Content-Type",  "value": "application/json"},
    ]
    tx_id = await _upload(b"{}", tags)
    logger.info("⚖️ Review de oráculos %s → %s.", aporte_tx[:12], status)
    return tx_id


async def read_oracle_review(aporte_tx: str) -> Optional[Dict[str, str]]:
    try:
        node = await _query_latest([
            {"name": "data-type", "value": "oracle-review"},
            {"name": "aporte-tx", "value": aporte_tx},
        ])
        if node:
            return _node_tags(node)
    except Exception as exc:
        logger.warning("read_oracle_review %s falló: %s", aporte_tx[:12], exc)
    return None


async def list_oracle_reviews(limit: int = 200) -> List[Dict[str, str]]:
    """Última versión de cada review (todas; el caller filtra por status)."""
    try:
        nodes = await _query_all([
            {"name": "data-type", "value": "oracle-review"},
        ], limit=limit)
        return _dedupe_latest(nodes, "aporte-tx")
    except Exception as exc:
        logger.warning("list_oracle_reviews falló: %s", exc)
        return []


@retry(
    retry=retry_if_exception_type((httpx.TransportError, ConnectionError, TimeoutError, OSError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
async def write_oracle_vote(aporte_tx: str, uid_hash: str, vote: bool) -> str:
    tags = [
        {"name": "data-type", "value": "oracle-vote"},
        {"name": "aporte-tx", "value": aporte_tx},
        {"name": "uid-hash",  "value": uid_hash},
        {"name": "vote",      "value": "yes" if vote else "no"},
        {"name": "Content-Type", "value": "application/json"},
    ]
    return await _upload(b"{}", tags)


async def list_oracle_votes(aporte_tx: str, limit: int = 200) -> List[Dict[str, str]]:
    """Voto vigente de cada oráculo sobre un aporte (última por usuario)."""
    try:
        nodes = await _query_all([
            {"name": "data-type", "value": "oracle-vote"},
            {"name": "aporte-tx", "value": aporte_tx},
        ], limit=limit)
        return _dedupe_latest(nodes, "uid-hash")
    except Exception as exc:
        logger.warning("list_oracle_votes %s falló: %s", aporte_tx[:12], exc)
        return []


# ═══════════════════════════════════════════════════════════════════════
# PROTOCOLO GLOBAL (Fase 3): PASSPORT · VACÍOS · GOBERNANZA
#
#   data-type=passport       → reputación agregada y verificable del usuario
#                              (última versión por uid gana; §10).
#   data-type=knowledge-gap  → pregunta sin respuesta detectada por el
#                              Agente (IEC §9.3), pública en el nodo.
#   data-type=proposal       → propuesta de gobernanza del nodo (§6.2 uso 6).
#   data-type=proposal-vote  → voto ponderado (1 SYNX = 1 voto); última
#                              versión por usuario gana.
# ═══════════════════════════════════════════════════════════════════════

async def list_impact_counters_by_author(author_hash: str, limit: int = 500) -> List[Dict[str, str]]:
    """Contadores de impacto vigentes de todos los aportes de un autor."""
    try:
        nodes = await _query_all([
            {"name": "data-type",  "value": "impact-counter"},
            {"name": "author-uid", "value": author_hash},
        ], limit=limit)
        return _dedupe_latest(nodes, "aporte-tx")
    except Exception as exc:
        logger.warning("list_impact_counters_by_author %s falló: %s", author_hash, exc)
        return []


@retry(
    retry=retry_if_exception_type((httpx.TransportError, ConnectionError, TimeoutError, OSError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
async def write_passport(uid_hash: str, data: Dict[str, Any]) -> str:
    """Sella el Passport (reputación agregada) de un usuario en Irys."""
    content = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    tags = [
        {"name": "data-type", "value": "passport"},
        {"name": "uid-hash",  "value": uid_hash},
        {"name": "rank",      "value": str(data.get("rank", ""))},
        {"name": "points",    "value": str(data.get("points", 0))},
        {"name": "contributions", "value": str(data.get("contributions", 0))},
        {"name": "synx-earned",   "value": f"{float(data.get('synx_earned', 0)):.2f}"},
        {"name": "Content-Type",  "value": "application/json"},
    ]
    tx_id = await _upload(content, tags)
    logger.info("🪪 Passport de %s sellado en Irys. Ver dato: %s", uid_hash, _gw(tx_id))
    return tx_id


async def read_passport(uid_hash: str) -> Optional[Dict[str, Any]]:
    """Última versión del Passport: {"tags": ..., "data": ..., "tx": ...}."""
    try:
        node = await _query_latest([
            {"name": "data-type", "value": "passport"},
            {"name": "uid-hash",  "value": uid_hash},
        ])
        if not node:
            return None
        raw = await _fetch(node["id"])
        return {
            "tx": node["id"],
            "tags": _node_tags(node),
            "data": json.loads(raw.decode("utf-8")),
        }
    except Exception as exc:
        logger.warning("read_passport %s falló: %s", uid_hash, exc)
        return None


def _question_hash(question: str) -> str:
    return hashlib.sha256(question.strip().lower().encode("utf-8")).hexdigest()[:12]


async def write_knowledge_gap(node_id: str, question: str, lang: str) -> Optional[str]:
    """Registra un vacío de conocimiento (pregunta sin respuesta, IEC §9.3).

    Dedupe por hash de la pregunta: la misma duda no se registra dos veces.
    """
    qh = _question_hash(question)
    try:
        existing = await _query_latest([
            {"name": "data-type",     "value": "knowledge-gap"},
            {"name": "question-hash", "value": qh},
        ])
        if existing:
            return None
        tags = [
            {"name": "data-type",     "value": "knowledge-gap"},
            {"name": "node-id",       "value": node_id},
            {"name": "question",      "value": question[:200]},
            {"name": "question-hash", "value": qh},
            {"name": "language",      "value": lang},
            {"name": "Content-Type",  "value": "application/json"},
        ]
        tx_id = await _upload(b"{}", tags)
        logger.info("🕳️ Vacío de conocimiento registrado en %s: %s", node_id, question[:80])
        return tx_id
    except Exception as exc:
        logger.warning("write_knowledge_gap falló: %s", exc)
        return None


async def list_node_gaps(node_id: str, limit: int = 20) -> List[Dict[str, str]]:
    """Vacíos de conocimiento del nodo, más nuevos primero (dedupe por pregunta)."""
    try:
        nodes = await _query_all([
            {"name": "data-type", "value": "knowledge-gap"},
            {"name": "node-id",   "value": node_id},
        ], limit=limit)
        return _dedupe_latest(nodes, "question-hash")
    except Exception as exc:
        logger.warning("list_node_gaps %s falló: %s", node_id, exc)
        return []


@retry(
    retry=retry_if_exception_type((httpx.TransportError, ConnectionError, TimeoutError, OSError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
async def write_proposal(
    proposal_id: str, node_id: str, creator_hash: str, text: str,
    status: str, voting_until: int,
) -> str:
    tags = [
        {"name": "data-type",       "value": "proposal"},
        {"name": "proposal-id",     "value": proposal_id},
        {"name": "node-id",         "value": node_id},
        {"name": "creator",         "value": creator_hash},
        {"name": "text",            "value": (text or "")[:200]},
        {"name": "proposal-status", "value": status},
        {"name": "voting-until",    "value": str(int(voting_until))},
        {"name": "Content-Type",    "value": "application/json"},
    ]
    content = json.dumps({"text": text}, ensure_ascii=False).encode("utf-8")
    tx_id = await _upload(content, tags)
    logger.info("🗳️ Propuesta %s (%s) sellada en Irys.", proposal_id, status)
    return tx_id


async def read_proposal(proposal_id: str) -> Optional[Dict[str, str]]:
    try:
        node = await _query_latest([
            {"name": "data-type",   "value": "proposal"},
            {"name": "proposal-id", "value": proposal_id},
        ])
        if node:
            return _node_tags(node)
    except Exception as exc:
        logger.warning("read_proposal %s falló: %s", proposal_id, exc)
    return None


async def list_node_proposals(node_id: str, limit: int = 100) -> List[Dict[str, str]]:
    try:
        nodes = await _query_all([
            {"name": "data-type", "value": "proposal"},
            {"name": "node-id",   "value": node_id},
        ], limit=limit)
        return _dedupe_latest(nodes, "proposal-id")
    except Exception as exc:
        logger.warning("list_node_proposals %s falló: %s", node_id, exc)
        return []


@retry(
    retry=retry_if_exception_type((httpx.TransportError, ConnectionError, TimeoutError, OSError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
async def write_proposal_vote(
    proposal_id: str, uid_hash: str, vote: bool, weight: int
) -> str:
    tags = [
        {"name": "data-type",   "value": "proposal-vote"},
        {"name": "proposal-id", "value": proposal_id},
        {"name": "uid-hash",    "value": uid_hash},
        {"name": "vote",        "value": "yes" if vote else "no"},
        {"name": "weight",      "value": str(int(weight))},
        {"name": "Content-Type", "value": "application/json"},
    ]
    return await _upload(b"{}", tags)


async def list_proposal_votes(proposal_id: str, limit: int = 1000) -> List[Dict[str, str]]:
    """Voto vigente de cada miembro (última versión por usuario)."""
    try:
        nodes = await _query_all([
            {"name": "data-type",   "value": "proposal-vote"},
            {"name": "proposal-id", "value": proposal_id},
        ], limit=limit)
        return _dedupe_latest(nodes, "uid-hash")
    except Exception as exc:
        logger.warning("list_proposal_votes %s falló: %s", proposal_id, exc)
        return []


# ═══════════════════════════════════════════════════════════════════════
# DIAGNÓSTICO / BALANCE
# ═══════════════════════════════════════════════════════════════════════

async def diagnose_irys_balance() -> None:
    """Consulta y loguea el balance vía el microservicio irys-uploader."""
    try:
        cli = await _client()
        resp = await cli.get(_UPLOADER_BALANCE_URL, timeout=20.0)
        if resp.status_code == 200:
            body = resp.json() if isinstance(resp.json(), dict) else {}
            balance_atomic = int(body.get("balance", 0))
            logger.info(
                "💰 Irys balance: %d atomic (%s %s) — address=%s",
                balance_atomic,
                balance_atomic / 1e18,
                IRYS_TOKEN.upper(),
                body.get("address", "?"),
            )
        else:
            logger.warning(
                "irys-uploader /balance retornó HTTP %d: %s",
                resp.status_code, resp.text[:200],
            )
    except Exception as exc:
        logger.warning("diagnose_irys_balance falló: %s", exc)


# Alias para compatibilidad con sync_brain.py
diagnose_payment_stream = diagnose_irys_balance


# ═══════════════════════════════════════════════════════════════════════
# CONECTIVIDAD / INICIALIZACIÓN
# ═══════════════════════════════════════════════════════════════════════

async def ensure_irys_connected() -> None:
    """Verifica conectividad con el microservicio irys-uploader y el nodo Irys."""
    cli = await _client()
    try:
        resp = await cli.get(_UPLOADER_HEALTH_URL, timeout=20.0)
        if resp.status_code == 200:
            info = resp.json() if isinstance(resp.json(), dict) else {}
            logger.info(
                "✅ irys-uploader conectado — address=%s token=%s node=%s",
                info.get("address", "?"),
                info.get("token", IRYS_TOKEN),
                info.get("node", IRYS_NODE_URL),
            )
        else:
            logger.warning(
                "irys-uploader /health retornó HTTP %d: %s",
                resp.status_code, resp.text[:200],
            )
    except Exception as exc:
        logger.warning("No se pudo verificar conectividad con irys-uploader: %s", exc)

    try:
        resp = await cli.get(f"{IRYS_NODE_URL}/info", timeout=20.0)
        if resp.status_code == 200:
            info = resp.json() if isinstance(resp.json(), dict) else {}
            logger.info(
                "✅ Nodo Irys alcanzable — version=%s token=%s",
                info.get("version", "?"), IRYS_TOKEN,
            )
    except Exception as exc:
        logger.warning("No se pudo verificar nodo Irys: %s", exc)

    await diagnose_irys_balance()


async def get_irys_client() -> None:
    """Alias de conectividad (reemplaza get_greenfield_client)."""
    await ensure_irys_connected()
    return None


# Alias para compatibilidad con sync_brain.py y fusion_brain.py
get_greenfield_client = get_irys_client


# ═══════════════════════════════════════════════════════════════════════
# NO-OPS (operaciones que no aplican a Irys)
# ═══════════════════════════════════════════════════════════════════════

async def cleanup_orphaned_created_objects() -> int:
    """No hay objetos huérfanos en Irys — no-op."""
    logger.debug("cleanup_orphaned_created_objects: no aplica para Irys")
    return 0


async def ensure_bucket_exists() -> None:
    """No hay buckets en Irys — no-op."""
    pass


# ═══════════════════════════════════════════════════════════════════════
# EXPORTACIONES PÚBLICAS
# ═══════════════════════════════════════════════════════════════════════

__all__ = [
    # Conectividad
    "get_irys_client",
    "get_greenfield_client",
    "ensure_irys_connected",
    "diagnose_irys_balance",
    "diagnose_payment_stream",
    "ensure_bucket_exists",
    # Utilidades
    "_hash_uid",
    "PRIVATE_KEY",
    "IRYS_NODE_URL",
    "IRYS_GATEWAY_URL",
    "IRYS_TOKEN",
    "TELEGRAM_TOKEN",
    "BRAIN_CODES",
    # Perfiles de usuario
    "read_user_tags",
    "write_user_tags",
    "read_user_pointer",
    "profile_exists",
    # Aportes
    "write_aporte",
    "read_aporte",
    "list_aportes",
    # Prueba de Impacto Real
    "write_impact_counter",
    "read_impact_counter",
    "write_impact_royalty",
    # Proveedores
    "write_provider",
    "list_node_providers",
    # Financiamiento colectivo
    "write_project",
    "read_project",
    "list_node_projects",
    "write_project_fund",
    "list_project_funds",
    "write_project_vote",
    "list_project_votes",
    # Protocolo Global (Fase 3)
    "list_impact_counters_by_author",
    "write_passport",
    "read_passport",
    "write_knowledge_gap",
    "list_node_gaps",
    "write_proposal",
    "read_proposal",
    "list_node_proposals",
    "write_proposal_vote",
    "list_proposal_votes",
    # Jueces Oráculos
    "write_oracle_stake",
    "read_oracle_stake",
    "write_oracle_review",
    "read_oracle_review",
    "list_oracle_reviews",
    "write_oracle_vote",
    "list_oracle_votes",
    # Wallet custodial
    "write_custodial_wallet",
    "read_custodial_wallet",
    # Nodos de comunidad
    "write_node",
    "get_node_record",
    "list_node_records",
    "write_node_member",
    "get_node_member",
    "list_node_members",
    "list_user_memberships",
    "list_node_aportes",
    "count_node_aportes",
    "write_node_bond",
    "get_node_bond",
    "list_user_bonds",
    "write_redemption",
    "get_redemption",
    "list_user_redemptions",
    "list_address_redemptions",
    "list_all_redemptions",
    # Leaderboard
    "rebuild_top10",
    "compute_top10",
    "get_top10_cached",
    # Usuarios
    "get_all_user_uids",
    "reset_all_daily_counts",
    # Brain
    "get_all_brain_pointers",
    "update_brain_pointer_tag",
    "upload_brain_index",
    "download_brain_index",
    "upload_brain_meta",
    "download_brain_meta",
    "get_brain_pointer",
    "set_brain_pointer",
    # Challenges
    "get_current_challenge",
    "save_challenge",
    "load_current_challenge_from_irys",
    "load_current_challenge_from_greenfield",
    # AI Guard
    "load_ai_guard",
    "get_ai_guard_patterns",
    "check_ai_guard",
    # Emergency Lock
    "check_emergency_lock",
    "is_emergency_locked",
    "create_emergency_lock",
    "delete_emergency_lock",
    # System Config
    "load_system_config",
    "get_system_config",
    # Logs
    "upload_log",
    # No-ops
    "cleanup_orphaned_created_objects",
]
