"""
irys.py — Capa de almacenamiento permanente sobre Irys (Arweave).
Reemplaza greenfield.py. Misma API pública, sin SDK propio ni Go.

Arquitectura:
  Upload  → ANS-104 DataItem firmado con ECDSA Ethereum → POST /tx/{token}
  Lectura → GraphQL para metadatos + gateway HTTP para contenido
  Auth    → misma PRIVATE_KEY ya configurada (wallet BNB/ETH)

Ventajas respecto a Greenfield:
  - Tags ilimitados (sin límite de 4 tags / 64 bytes)
  - Sin patches de SDK ni compilación Go
  - Inmutable y permanente en Arweave
  - Patrón "latest by tag" para datos mutables (perfiles de usuario)
"""

import asyncio
import gzip
import hashlib
import json
import logging
import os
import struct
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

import httpx
from eth_account import Account
from eth_keys import keys as _eth_keys
from eth_utils import keccak
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
TELEGRAM_TOKEN: str = _getenv("TELEGRAM_TOKEN", "")
THINKER_HOST: str = _getenv("THINKER_HOST", "http://thinker:8081")
JUDGE_HOST: str = _getenv("JUDGE_HOST", "http://judge:8080")
CACHE_TTL: str = _getenv("CACHE_TTL", "12")

_APP_NAME = "Synergix"
_GRAPHQL_URL = f"{IRYS_NODE_URL}/graphql"
_UPLOAD_URL = f"{IRYS_NODE_URL}/tx/{IRYS_TOKEN}"

BRAIN_CODES: List[str] = ["prog", "tech", "cien", "know"]


# ═══════════════════════════════════════════════════════════════════════
# GHOST PROTOCOL — Ofuscación de UID
# ═══════════════════════════════════════════════════════════════════════

def _hash_uid(uid: int) -> str:
    """SHA-256('Synergix_' + uid) → hex[:12]. Igual que en Greenfield."""
    return hashlib.sha256(f"Synergix_{uid}".encode()).hexdigest()[:12]


# ═══════════════════════════════════════════════════════════════════════
# ANS-104 DATAITEM — Implementación nativa Python para Ethereum
# ═══════════════════════════════════════════════════════════════════════
# Ethereum signer: tipo 3, firma 65 bytes, owner 65 bytes (clave pública
# sin comprimir: 0x04 + 32 bytes X + 32 bytes Y).

def _avro_long(n: int) -> bytes:
    """Zigzag + varint encoding para campos long de Avro."""
    n = (n << 1) ^ (n >> 63)
    buf = bytearray()
    while n > 0x7F:
        buf.append((n & 0x7F) | 0x80)
        n >>= 7
    buf.append(n)
    return bytes(buf)


def _avro_str(s: str) -> bytes:
    b = s.encode("utf-8")
    return _avro_long(len(b)) + b


def _encode_tags_avro(tags: List[Dict[str, str]]) -> bytes:
    """Serializa tags en formato Avro para ANS-104."""
    if not tags:
        return b""
    result = _avro_long(len(tags))
    for t in tags:
        result += _avro_str(t["name"])
        result += _avro_str(t["value"])
    result += _avro_long(0)  # fin de array
    return result


def _deep_hash(data: Any) -> bytes:
    """Arweave deep hash (SHA-384 iterativo)."""
    if isinstance(data, (bytes, bytearray)):
        tag = b"blob\n" + str(len(data)).encode()
        return hashlib.sha384(tag + data).digest()
    tag = b"list\n" + str(len(data)).encode()
    acc = hashlib.sha384(tag).digest()
    for item in data:
        acc = hashlib.sha384(acc + _deep_hash(item)).digest()
    return acc


def _privkey_bytes(private_key: str) -> bytes:
    """
    Retorna los 32 bytes raw de la private key, usando la misma lógica que
    eth_account.Account.from_key (removeprefix '0x', NO lstrip — lstrip
    eliminaría también ceros iniciales, produciendo una clave distinta).
    """
    return bytes(Account.from_key(private_key).key)


def _get_owner_bytes(private_key: str) -> bytes:
    """Clave pública sin comprimir de 65 bytes (0x04 + X + Y)."""
    pk = _eth_keys.PrivateKey(_privkey_bytes(private_key))
    owner = b"\x04" + pk.public_key.to_bytes()

    # Sanity check: la dirección derivada debe coincidir con Account.address
    expected = Account.from_key(private_key).address.lower()
    derived = "0x" + keccak(primitive=owner[1:])[-20:].hex()
    if derived.lower() != expected:
        logger.error("Owner mismatch! derived=%s expected=%s", derived, expected)
    return owner


def _eth_sign(private_key: str, message: bytes) -> bytes:
    """
    Firma ANS-104 para sigType=3 (Ethereum/Secp256k1):
    sign(keccak256(deepHash)) con ECDSA raw — SIN prefijo personal_sign.

    arbundles/Irys usa @noble/curves/secp256k1 que firma el hash directamente
    (no usa wallet.signMessage que añadiría \\x19Ethereum Signed Message:\\n).
    """
    signing_hash = keccak(primitive=message)   # keccak256 del deepHash → 32 bytes
    pk = _eth_keys.PrivateKey(_privkey_bytes(private_key))
    sig = pk.sign_msg_hash(signing_hash)
    r = sig.r.to_bytes(32, "big")
    s = sig.s.to_bytes(32, "big")
    v = bytes([sig.v + 27])   # eth_keys usa 0/1; Ethereum espera 27/28
    return r + s + v


def _build_data_item(data: bytes, tags: List[Dict[str, str]]) -> bytes:
    """
    Construye y firma un ANS-104 DataItem con Ethereum (tipo 3).
    Incluye App-Name automáticamente como primer tag.
    """
    if not PRIVATE_KEY:
        raise RuntimeError("PRIVATE_KEY no está configurada en .env")

    all_tags = [{"name": "App-Name", "value": _APP_NAME}] + tags
    owner = _get_owner_bytes(PRIVATE_KEY)
    tags_bytes = _encode_tags_avro(all_tags)

    msg_hash = _deep_hash([
        b"dataitem",
        b"1",
        b"3",        # tipo Ethereum como cadena ASCII
        owner,
        b"",         # sin target
        b"",         # sin anchor
        tags_bytes,
        data,
    ])

    sig = _eth_sign(PRIVATE_KEY, msg_hash)   # 65 bytes r+s+v

    # ── Auto-verificación local de la firma ──
    # Si la firma es matemáticamente correcta, recover_public_key debe
    # devolver la misma dirección que el owner. Si falla aquí, el bug
    # está en _eth_sign. Si pasa aquí pero Irys aún rechaza, el bug
    # está en el formato binario o en los inputs del deep_hash.
    try:
        from eth_keys.datatypes import Signature
        signing_hash = keccak(primitive=msg_hash)   # debe coincidir con _eth_sign
        v_raw = sig[64] - 27
        r_int = int.from_bytes(sig[:32], "big")
        s_int = int.from_bytes(sig[32:64], "big")
        sig_obj = Signature(vrs=(v_raw, r_int, s_int))
        recovered_pk = sig_obj.recover_public_key_from_msg_hash(signing_hash)
        recovered_addr = "0x" + recovered_pk.to_canonical_address().hex()
        expected_addr = Account.from_key(PRIVATE_KEY).address.lower()
        if recovered_addr.lower() != expected_addr:
            logger.error(
                "Sig local FAIL: recovered=%s expected=%s msg_hash=%s",
                recovered_addr, expected_addr, msg_hash.hex(),
            )
        else:
            logger.info(
                "Sig local OK: addr=%s msg_hash=%s sig=%s owner=%s tags_bytes_len=%d data_len=%d",
                expected_addr, msg_hash.hex()[:32], sig.hex()[:32],
                owner.hex()[:32], len(tags_bytes), len(data),
            )
    except Exception as e:
        logger.error("Sig local verify error: %s", e)

    item = struct.pack("<H", 3)                      # sig_type
    item += sig                                       # 65 bytes firma
    item += owner                                     # 65 bytes owner
    item += b"\x00"                                   # sin target
    item += b"\x00"                                   # sin anchor
    item += struct.pack("<Q", len(all_tags))           # nº de tags
    item += struct.pack("<Q", len(tags_bytes))         # bytes de tags
    item += tags_bytes
    item += data
    return item


# ═══════════════════════════════════════════════════════════════════════
# CLIENTE HTTP (singleton)
# ═══════════════════════════════════════════════════════════════════════

_http: Optional[httpx.AsyncClient] = None


async def _client() -> httpx.AsyncClient:
    global _http
    if _http is None or _http.is_closed:
        _http = httpx.AsyncClient(timeout=60.0)
    return _http


# ═══════════════════════════════════════════════════════════════════════
# UPLOAD
# ═══════════════════════════════════════════════════════════════════════

@retry(
    retry=retry_if_exception_type((httpx.TransportError, ConnectionError, TimeoutError, OSError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
async def _upload(data: bytes, tags: List[Dict[str, str]]) -> str:
    """Sube un DataItem a Irys. Retorna el transaction ID."""
    item = _build_data_item(data, tags)
    cli = await _client()
    resp = await cli.post(
        _UPLOAD_URL,
        content=item,
        headers={"Content-Type": "application/octet-stream"},
    )
    if not resp.is_success:
        logger.error("Irys upload %d — body: %s", resp.status_code, resp.text[:400])
    resp.raise_for_status()
    tx_id: str = resp.json().get("id", "")
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
    await _upload(b"{}", [
        {"name": "data-type", "value": "emergency-lock"},
        {"name": "lock-status",  "value": "active"},
    ])
    _emergency_lock_active = True
    logger.warning("🔒 Emergency lock ACTIVADO en Irys")


async def delete_emergency_lock() -> None:
    """No se puede borrar en Irys; se sube un tx con status=inactive."""
    global _emergency_lock_active
    await _upload(b"{}", [
        {"name": "data-type", "value": "emergency-lock"},
        {"name": "lock-status",  "value": "inactive"},
    ])
    _emergency_lock_active = False
    logger.warning("🔓 Emergency lock DESACTIVADO en Irys")


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
            await _upload(
                _DEFAULT_AI_GUARD.encode("utf-8"),
                [{"name": "data-type", "value": "ai-guard"}],
            )
            logger.info("🛡️ ai-guard creado en Irys con patrones por defecto")
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
            await _upload(
                json.dumps(_DEFAULT_SYSTEM_CONFIG, indent=2).encode("utf-8"),
                [{"name": "data-type", "value": "system-config"}],
            )
            logger.info("⚙️ system-config creado en Irys con valores por defecto")
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

    await _upload(b"{}", irys_tags)
    logger.info("✅ Perfil %s actualizado en Irys", uid_ofuscado)


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
        {"name": "content-type",  "value": "text/plain"},
    ]
    for k, v in tags.items():
        if v is not None and str(v) != "":
            irys_tags.append({"name": k.replace("_", "-"), "value": str(v)})

    tx_id = await _upload(texto.encode("utf-8"), irys_tags)
    logger.info("✅ Aporte subido a Irys: tx=%s uid=%s", tx_id, uid_ofuscado)
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
                "uid":             uid,
                "points":          int(rt.get("points", "0")),
                "rank":            rt.get("rank", "🌱 Iniciado"),
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
    await _upload(b"{}", [
        {"name": "data-type",     "value": "brain-pointer"},
        {"name": "brain-code",    "value": code},
        {"name": "brain-version", "value": version_name},
    ])
    logger.info("🧠 Brain pointer [%s] → %s", code, version_name)


async def upload_brain_index(code: str, version_name: str, binary: bytes) -> None:
    """Sube el binario FAISS a Irys."""
    await _upload(binary, [
        {"name": "data-type",     "value": "brain-index"},
        {"name": "brain-code",    "value": code},
        {"name": "brain-version", "value": version_name},
        {"name": "Content-Type",  "value": "application/octet-stream"},
    ])
    logger.info("🧠 Brain index [%s] %s subido a Irys", code, version_name)


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
    await _upload(content, [
        {"name": "data-type",     "value": "brain-meta"},
        {"name": "brain-code",    "value": code},
        {"name": "brain-version", "value": version_name},
        {"name": "Content-Type",  "value": "application/json"},
    ])


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
    await _upload(b"{}", [
        {"name": "data-type",     "value": "brain-pointer-global"},
        {"name": "brain-version", "value": version},
    ])
    _single_brain_pointer = version
    logger.info("🧠 Brain pointer global → %s", version)


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
        await _upload(content, [
            {"name": "data-type",    "value": "challenge"},
            {"name": "challenge-id", "value": str(challenge.get("id", ""))},
        ])
        logger.info("🎯 Challenge guardado en Irys: %s", challenge.get("id"))
    except Exception as exc:
        logger.warning("Challenge en RAM pero no persistido en Irys: %s", exc)


# ═══════════════════════════════════════════════════════════════════════
# LOGS
# ═══════════════════════════════════════════════════════════════════════

async def upload_log(date_str: str, log_content: str) -> None:
    """Sube log diario comprimido a Irys."""
    compressed = gzip.compress(log_content.encode("utf-8"))
    await _upload(compressed, [
        {"name": "data-type",    "value": "log"},
        {"name": "log-date",     "value": date_str},
        {"name": "Content-Type", "value": "application/gzip"},
    ])
    logger.info("📄 Log subido a Irys: %s", date_str)


# ═══════════════════════════════════════════════════════════════════════
# DIAGNÓSTICO / BALANCE
# ═══════════════════════════════════════════════════════════════════════

async def diagnose_irys_balance() -> None:
    """Consulta y loguea el balance disponible en el nodo Irys."""
    if not PRIVATE_KEY:
        return
    try:
        account = Account.from_key(PRIVATE_KEY)
        cli = await _client()
        resp = await cli.get(
            f"{IRYS_NODE_URL}/account/balance/{IRYS_TOKEN}",
            params={"address": account.address},
        )
        if resp.status_code == 200:
            balance_atomic = int(resp.json().get("balance", 0))
            logger.info(
                "💰 Irys balance: %d atomic (%s BNB) — address=%s",
                balance_atomic,
                balance_atomic / 1e18,
                account.address,
            )
        else:
            logger.warning("Irys balance check retornó HTTP %d", resp.status_code)
    except Exception as exc:
        logger.warning("diagnose_irys_balance falló: %s", exc)


# Alias para compatibilidad con sync_brain.py
diagnose_payment_stream = diagnose_irys_balance


# ═══════════════════════════════════════════════════════════════════════
# CONECTIVIDAD / INICIALIZACIÓN
# ═══════════════════════════════════════════════════════════════════════

async def ensure_irys_connected() -> None:
    """Verifica conectividad con Irys y loguea info de la wallet."""
    if not PRIVATE_KEY:
        raise RuntimeError("PRIVATE_KEY no está configurada en .env")
    account = Account.from_key(PRIVATE_KEY)
    try:
        cli = await _client()
        resp = await cli.get(f"{IRYS_NODE_URL}/info")
        if resp.status_code == 200:
            info = resp.json()
            logger.info(
                "✅ Irys conectado — address=%s version=%s token=%s",
                account.address,
                info.get("version", "?"),
                IRYS_TOKEN,
            )
        else:
            logger.warning("Irys /info retornó HTTP %d", resp.status_code)
    except Exception as exc:
        logger.warning("No se pudo verificar conectividad Irys: %s", exc)
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
