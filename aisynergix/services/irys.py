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
        logger.warning("GraphQL errors: %s", str(result["errors"])[:400])
    return result


def _owner_filter() -> str:
    """Filtro owners para limitar queries al wallet propio y evitar timeouts."""
    if not PRIVATE_KEY:
        return ""
    address = Account.from_key(PRIVATE_KEY).address
    return f', owners: ["{address}"]'


def _tag_filter(tags: List[Dict[str, str]]) -> str:
    """Convierte lista de tags a filtro GraphQL."""
    parts = [
        f'{{name: "{t["name"]}", values: ["{t["value"]}"]}}' for t in tags
    ]
    return ", ".join(parts)


async def _query_latest(tags: List[Dict[str, str]]) -> Optional[Dict[str, Any]]:
    """Retorna el nodo más reciente que coincida con los tags dados."""
    q = f"""
    {{
      transactions(
        tags: [{_tag_filter(tags)}]{_owner_filter()},
        first: 20
      ) {{ edges {{ node {{ id tags {{ name value }} timestamp }} }} }}
    }}
    """
    try:
        data = await _gql(q)
        edges = (data.get("data") or {}).get("transactions", {}).get("edges", [])
        nodes = [e["node"] for e in edges if e.get("node")]
        if not nodes:
            return None
        nodes.sort(key=lambda n: n.get("timestamp", 0), reverse=True)
        return nodes[0]
    except Exception as exc:
        logger.warning("_query_latest falló: %s", exc)
        return None


async def _query_all(
    tags: List[Dict[str, str]], limit: int = 100
) -> List[Dict[str, Any]]:
    """Retorna todos los nodos (DESC por timestamp) que coincidan con los tags dados."""
    q = f"""
    {{
      transactions(
        tags: [{_tag_filter(tags)}]{_owner_filter()},
        first: {limit}
      ) {{ edges {{ node {{ id tags {{ name value }} timestamp }} }} }}
    }}
    """
    try:
        data = await _gql(q)
        edges = (data.get("data") or {}).get("transactions", {}).get("edges", [])
        nodes = [e["node"] for e in edges if e.get("node")]
        nodes.sort(key=lambda n: n.get("timestamp", 0), reverse=True)
        return nodes
    except Exception as exc:
        logger.warning("_query_all falló: %s", exc)
        return []


async def _query_by_id(tx_id: str) -> Optional[Dict[str, Any]]:
    """Obtiene tags de una transacción por su ID."""
    q = f"""
    {{
      transactions(ids: ["{tx_id}"]{_owner_filter()}) {{
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
}
_PROFILE_TAG_RMAP: Dict[str, str] = {v: k for k, v in _PROFILE_TAG_MAP.items()}


@retry(
    retry=retry_if_exception_type((httpx.TransportError, ConnectionError, TimeoutError, OSError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
async def read_user_tags(uid_ofuscado: str) -> Dict[str, str]:
    """Lee el perfil de usuario más reciente desde Irys."""
    try:
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
    """Sube nueva versión del perfil de usuario a Irys."""
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
    """Calcula top10 leyendo todos los perfiles desde Irys."""
    nodes = await _query_all(
        [{"name": "data-type", "value": "user-profile"}], limit=1000
    )
    seen: Set[str] = set()
    usuarios: List[Dict[str, Any]] = []
    for node in nodes:
        rt = _node_tags(node)
        uid = rt.get("uid-hash", "")
        if not uid or uid in seen:
            continue
        seen.add(uid)
        try:
            usuarios.append({
                "uid":              uid,
                "points":           int(rt.get("points", "0")),
                "rank":             rt.get("rank", "🌱 Iniciado"),
                "contribution_count": int(rt.get("contribution-count", "0")),
                "total_uses_count": int(rt.get("total-uses-count", "0")),
            })
        except (ValueError, TypeError):
            continue
    usuarios.sort(key=lambda u: u["points"], reverse=True)
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
    # Aportes
    "write_aporte",
    "read_aporte",
    "list_aportes",
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
