import asyncio
import hashlib
import io
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any, Dict, List, Optional, Tuple

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

# ── SDK oficial de Greenfield ──────────────────────────────────────────
# NOTA: El SDK usa BaseSettings con extra='forbid'. Solo lee HOST, PORT,
# CHAIN_ID, PRIVATE_KEY del entorno. Las demás variables usan prefijo
# SYNERGIX_ para evitar colisiones.
from greenfield_python_sdk.key_manager import KeyManager
from greenfield_python_sdk.greenfield_client import GreenfieldClient as BaseGreenfieldClient
from greenfield_python_sdk.models.bucket import (
    CreateBucketOptions,
    VisibilityType,
)
from greenfield_python_sdk.models.eip712_messages.storage.msg_set_tag import (
    TYPE_URL as MSG_SET_TAG_TYPE_URL,
)
from greenfield_python_sdk.models.object import (
    CreateObjectOptions,
    GetObjectOption,
    ListObjectsOptions,
    ListObjectsResult,
    PutObjectOptions,
)
from greenfield_python_sdk.models.request import ResourceType
from greenfield_python_sdk.protos.greenfield.storage import (
    MsgSetTag,
    ResourceTags,
    ResourceTagsTag,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# HELPER: Lee variables con prefijo SYNERGIX_ o fallback sin prefijo
# ═══════════════════════════════════════════════════════════════════════

def _getenv(key: str, default: str = "") -> str:
    """
    Lee variable de entorno con prefijo SYNERGIX_ primero,
    luego intenta sin prefijo (compatibilidad hacia atrás).
    """
    return os.getenv(f"SYNERGIX_{key}", os.getenv(key, default))


# ═══════════════════════════════════════════════════════════════════════
# CONSTANTES — AHORA CON PREFIJO SYNERGIX_ (excepto las que usa el SDK)
# ═══════════════════════════════════════════════════════════════════════

# Estas variables las lee el SDK de Greenfield directamente:
# HOST, PORT, CHAIN_ID, PRIVATE_KEY — NO usar prefijo para estas
# porque el SDK las espera exactamente así.

# Variables propias de Synergix (con prefijo para evitar extra_forbidden)
BUCKET_NAME: str = _getenv("BUCKET_NAME", "synergix-v1")
SP_ENDPOINT: str = _getenv(
    "SP_ENDPOINT",
    "https://greenfield-sp.bnbchain.org",
)
DCELLAR_SP_ADDRESS: str = _getenv("DCELLAR_SP_ADDRESS", "")
BUCKET_ID: str = _getenv(
    "BUCKET_ID",
    "0x000000000000000000000000000000000000000000000000000000000000fd06",
)
TELEGRAM_TOKEN: str = _getenv("TELEGRAM_TOKEN", "")
THINKER_HOST: str = _getenv("THINKER_HOST", "http://thinker:8081")
JUDGE_HOST: str = _getenv("JUDGE_HOST", "http://judge:8080")
CACHE_TTL: str = _getenv("CACHE_TTL", "12")

# Variables del SDK (sin prefijo, el SDK las lee del entorno)
GREENFIELD_RPC_URL: str = os.getenv(
    "HOST",  # El SDK usa HOST internamente
    "https://greenfield-chain.bnbchain.org",
)
GREENFIELD_PORT: int = int(os.getenv("PORT", "443"))
GREENFIELD_CHAIN_ID: int = int(os.getenv("CHAIN_ID", "1017"))
PRIVATE_KEY: str = os.getenv("PRIVATE_KEY", "")

# Greenfield ~2 s block time + SP indexing lag.  After broadcasting
# MsgCreateObject the SP needs this many seconds before it will accept
# put_object for the new object (error 20008 otherwise).
_SP_SYNC_DELAY: int = 12

# ── AI Guard (anti-jailbreak) ─────────────────────────────────────────
_ai_guard_patterns: List[str] = []

# ── Emergency Lock ────────────────────────────────────────────────────
_emergency_lock_active: bool = False

# ── System Config ─────────────────────────────────────────────────────
_system_config: Dict[str, Any] = {}

_DEFAULT_SYSTEM_CONFIG: Dict[str, Any] = {
    "quality_threshold": 5.0,
    "elite_threshold": 9.0,
    "legendary_threshold": 9.5,
    "trust_score_increment": 0.1,
    "trust_score_decrement": 0.2,
    "min_contribution_length": 20,
}


# ═══════════════════════════════════════════════════════════════════════
# CLIENTE GLOBAL (inicialización perezosa thread-safe para asyncio)
# ═══════════════════════════════════════════════════════════════════════

_client: Optional[BaseGreenfieldClient] = None
_key_manager: Optional[KeyManager] = None
_client_lock: bool = False


def _hash_uid(uid: int) -> str:
    """
    Ofusca un UID de Telegram usando SHA-256 + salt.
    uid → SHA-256("Synergix_" + uid) → hex[:12]
    """
    raw = f"Synergix_{uid}".encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    return digest[:12]


def _dict_to_tags(kv: Dict[str, str]) -> ResourceTags:
    """Convierte un diccionario Python → ResourceTags (protobuf)."""
    tags_list = [
        ResourceTagsTag(key=str(k), value=str(v))
        for k, v in kv.items()
    ]
    return ResourceTags(tags=tags_list)


def _tags_to_dict(tags: Optional[ResourceTags]) -> Dict[str, str]:
    """Convierte ResourceTags → dict Python."""
    if tags is None or not tags.tags:
        return {}
    return {t.key: t.value for t in tags.tags}


def _normalize_path(*segments: str) -> str:
    """Normaliza segmentos de ruta para Greenfield (nunca empieza con /)."""
    p = str(PurePosixPath(*segments))
    return p[1:] if p.startswith("/") else p


def _extract_txhash(tx_result: Any) -> str:
    """Extract blockchain tx hash from a broadcast_message response (best-effort)."""
    if tx_result is None:
        return ""
    try:
        if isinstance(tx_result, dict):
            return str(tx_result.get("txhash") or tx_result.get("tx_hash") or "")
        for attr in ("txhash", "tx_hash", "hash"):
            val = getattr(tx_result, attr, None)
            if val:
                return str(val)
        # Some SDK versions nest it under tx_response
        inner = getattr(tx_result, "tx_response", None)
        if inner:
            for attr in ("txhash", "tx_hash"):
                val = getattr(inner, attr, None)
                if val:
                    return str(val)
    except Exception:
        pass
    return ""


# ═══════════════════════════════════════════════════════════════════════
# INICIALIZACIÓN DEL CLIENTE
# ═══════════════════════════════════════════════════════════════════════

async def get_client() -> BaseGreenfieldClient:
    """
    Obtiene o crea el GreenfieldClient asíncrono global.

    ── POR QUÉ SE LIMPIA os.environ ────────────────────────────────────
    NetworkConfiguration (Pydantic BaseSettings, extra='forbid') rechaza
    cualquier variable de entorno cuyo nombre no reconozca.  Limpiamos el
    entorno, dejamos solo las 4 vars que el SDK espera y luego restauramos
    el entorno original.  NO creamos subclase de GreenfieldClient: el SDK
    no es un BaseModel de Pydantic, es una clase Python regular, y heredar
    ConfigDict sobre ella rompe su __init__ e impide que se instancien los
    sub-clientes (account, bucket, object, …).
    """
    global _client, _key_manager, _client_lock

    if _client is not None:
        return _client

    if not PRIVATE_KEY:
        raise RuntimeError("PRIVATE_KEY no está configurada en .env")

    if _client_lock:
        for _ in range(20):
            if _client is not None:
                return _client
            await asyncio.sleep(0.1)
        raise RuntimeError(
            "Timeout esperando inicialización del cliente Greenfield"
        )

    _client_lock = True
    try:
        # ── Limpiar prefijo 0x de la private key ──────────────────
        clean_key = (
            PRIVATE_KEY[2:]
            if PRIVATE_KEY.startswith(("0x", "0X"))
            else PRIVATE_KEY
        )

        from greenfield_python_sdk.config import NetworkConfiguration

        # ═══════════════════════════════════════════════════════════════
        # NetworkConfiguration es un pydantic_settings.BaseSettings con
        # extra='forbid'.  Ni _env_file=None ni limpiar os.environ es
        # suficiente para evitar que lea el .env del disco en todas las
        # versiones de pydantic-settings.
        #
        # SOLUCIÓN DEFINITIVA: model_construct() bypasea completamente
        # toda la maquinaria de validación y lectura de env/archivo.
        # Construye el objeto Pydantic directamente con los valores dados,
        # sin tocar ni el entorno ni el disco.
        # ═══════════════════════════════════════════════════════════════
        network_config = NetworkConfiguration.model_construct(
            _fields_set={"host", "port", "chain_id"},
            host=GREENFIELD_RPC_URL.rstrip("/"),
            port=GREENFIELD_PORT,
            chain_id=GREENFIELD_CHAIN_ID,
        )

        _key_manager = KeyManager(private_key=clean_key)

        # ─────────────────────────────────────────────────────────────
        # ✅ GreenfieldClient ES un async context manager.
        # Su __aenter__ instancia los sub-clientes (account, bucket,
        # object, blockchain_client).  __init__ NO los crea.
        #
        # Para un singleton persistente llamamos __aenter__ manualmente.
        # ─────────────────────────────────────────────────────────────
        _client = BaseGreenfieldClient(
            network_configuration=network_config,
            key_manager=_key_manager,
        )

        # Paso 1: __aenter__ instancia account, bucket, object, etc.
        await _client.__aenter__()
        # Paso 2: async_init sincroniza la cuenta on-chain
        await _client.async_init()

        logger.info(
            "✅ GreenfieldClient inicializado — address=%s chain=%s",
            _key_manager.address,
            GREENFIELD_CHAIN_ID,
        )

    except Exception as exc:
        _client = None
        _client_lock = False
        logger.exception("❌ Fallo al inicializar GreenfieldClient")
        raise RuntimeError(
            f"No se pudo conectar a Greenfield: {exc}"
        ) from exc

    return _client


# ═══════════════════════════════════════════════════════════════════════
# ASEGURAMIENTO DEL BUCKET
# ═══════════════════════════════════════════════════════════════════════

@retry(
    retry=retry_if_exception_type(
        (httpx.HTTPError, ConnectionError, TimeoutError, OSError)
    ),
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=2, max=30),
)
async def ensure_bucket_exists() -> None:
    """
    Verifica que el bucket 'synergix-v1' existe en Greenfield mainnet.
    Si no existe, lo crea con visibilidad privada y 10 GiB/mes de cuota.
    """
    client = await get_client()
    try:
        await client.bucket.get_bucket_head(BUCKET_NAME)
        logger.debug("Bucket '%s' ya existe", BUCKET_NAME)
        return
    except Exception:
        logger.info("Bucket '%s' no encontrado — creando…", BUCKET_NAME)

    sps = await client.blockchain_client.get_active_sps()
    if not sps:
        raise RuntimeError("No hay Storage Providers activos en Greenfield")

    primary_sp = sps[0]["operator_address"]
    await client.bucket.create_bucket(
        bucket_name=BUCKET_NAME,
        primary_sp_address=primary_sp,
        opts=CreateBucketOptions(
            visibility=VisibilityType.VISIBILITY_TYPE_PRIVATE,
            charged_read_quota=10 * 1024 * 1024 * 1024,
        ),
    )
    logger.info("✅ Bucket '%s' creado exitosamente", BUCKET_NAME)


# ═══════════════════════════════════════════════════════════════════════
# OPERACIONES DE USUARIO (Archivos fantasma de 0 bytes + tags)
# ═══════════════════════════════════════════════════════════════════════

def _user_path(uid_ofuscado: str) -> str:
    return _normalize_path("aisynergix", "users", uid_ofuscado)


@retry(
    retry=retry_if_exception_type(
        (httpx.HTTPError, ConnectionError, TimeoutError, OSError)
    ),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
async def read_user_tags(uid_ofuscado: str) -> Dict[str, str]:
    """
    Lee los tags Web3 del archivo fantasma de un usuario.
    Si el archivo no existe, retorna valores por defecto.
    """
    client = await get_client()
    path = _user_path(uid_ofuscado)
    try:
        obj_info = await client.object.get_object_head(BUCKET_NAME, path)
        return _tags_to_dict(obj_info.tags)
    except Exception:
        return {
            "fsm_state": "idle",
            "points": "0",
            "rank": "🌱 Iniciado",
            "daily_aportes_count": "0",
            "total_uses_count": "0",
            "language": "es",
            "last_seen_ts": "0",
        }


@retry(
    retry=retry_if_exception_type(
        (httpx.HTTPError, ConnectionError, TimeoutError, OSError)
    ),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
async def write_user_tags(uid_ofuscado: str, tags: Dict[str, str]) -> None:
    """
    Crea o actualiza el archivo fantasma (0 bytes) de un usuario.

    REGLA DE MERGE: cuando el objeto ya existe, leemos los tags actuales y
    aplicamos los nuevos encima. Esto garantiza que campos como
    wallet_address, guardados por rutas independientes (wallet_verify.py),
    NO sean borrados por MsgSetTag cuando UserProfile.to_tags() los omite.
    MsgSetTag reemplaza TODOS los tags del objeto; sin el merge perderíamos
    wallet_address en la siguiente contribución.
    """
    client = await get_client()
    path = _user_path(uid_ofuscado)

    exists = False
    existing_tags: Dict[str, str] = {}
    try:
        obj_info = await client.object.get_object_head(BUCKET_NAME, path)
        exists = True
        existing_tags = _tags_to_dict(obj_info.tags)
    except Exception:
        exists = False

    # Merge: existing tags act as base, incoming tags take priority.
    # Preserves wallet_address and any future out-of-profile fields.
    merged_tags = {**existing_tags, **tags}

    if not exists:
        try:
            await client.object.create_object(
                BUCKET_NAME,
                path,
                io.BytesIO(b""),
                CreateObjectOptions(
                    visibility=VisibilityType.VISIBILITY_TYPE_PRIVATE,
                    content_type="application/octet-stream",
                ),
            )
        except Exception as create_exc:
            logger.warning(
                "create_object falló para usuario %s: %s — no se pueden fijar tags",
                uid_ofuscado,
                create_exc,
            )
            raise  # Object not on-chain at all — MsgSetTag would fail

        # The object is now on-chain (CREATED status).  Try to seal it via
        # put_object; if it fails (SP auto-sealed the 0-byte object or timing
        # issue) we log a warning but CONTINUE to MsgSetTag.  Tags work on
        # both CREATED and SEALED objects.
        await asyncio.sleep(_SP_SYNC_DELAY)
        try:
            await client.object.put_object(
                bucket_name=BUCKET_NAME,
                object_name=path,
                object_size=0,
                reader=io.BytesIO(b""),
                opts=PutObjectOptions(content_type="application/octet-stream"),
            )
        except Exception as put_exc:
            logger.warning(
                "put_object para objeto fantasma %s: %s — continuando con MsgSetTag",
                uid_ofuscado,
                put_exc,
            )

    resource = (
        f"grn:{ResourceType.RESOURCE_TYPE_OBJECT.value}"
        f"::{BUCKET_NAME}/{path}"
    )
    msg_set = MsgSetTag(
        operator=_key_manager.address if _key_manager else "",
        resource=resource,
        tags=_dict_to_tags(merged_tags),
    )
    await client.blockchain_client.broadcast_message(
        messages=[msg_set],
        type_url=[MSG_SET_TAG_TYPE_URL],
    )
    logger.info("✅ Usuario %s actualizado en Greenfield", uid_ofuscado)


# ═══════════════════════════════════════════════════════════════════════
# OPERACIONES DE APORTES
# ═══════════════════════════════════════════════════════════════════════

def _aporte_path(
    uid_ofuscado: str, ts: Optional[int] = None
) -> str:
    """
    Construye la ruta del aporte:
    aisynergix/aportes/YYYY-MM/{uid_ofuscado}_{ts}.txt
    """
    ahora = (
        datetime.now(timezone.utc)
        if ts is None
        else datetime.fromtimestamp(ts, tz=timezone.utc)
    )
    mes = ahora.strftime("%Y-%m")
    ts_val = ts or int(ahora.timestamp())
    return _normalize_path(
        "aisynergix", "aportes", mes, f"{uid_ofuscado}_{ts_val}.txt"
    )


@retry(
    retry=retry_if_exception_type(
        (httpx.HTTPError, ConnectionError, TimeoutError, OSError)
    ),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
async def write_aporte(
    uid_ofuscado: str,
    texto: str,
    tags: Dict[str, str],
    ts: Optional[int] = None,
) -> str:
    """
    Escribe un aporte en Greenfield con sus tags obligatorios.
    ─────────────────────────────────────────────────────────
    Flujo resiliente:
      1. create_object solo si el objeto aún no existe en la cadena
         (idempotente para reintentos — el objeto puede haberse creado
          en un intento previo antes de que fallara put_object).
      2. put_object best-effort: si el SP ya selló el objeto (error 50004
         o 50002) se registra un warning y se continúa.  El contenido ya
         está en el SP desde create_object para objetos pequeños.
      3. MsgSetTag siempre se ejecuta — funciona sobre objetos CREATED y
         SEALED.
    Retorna el tx hash del broadcast (si el SDK lo expone) o la ruta del
    objeto como identificador.
    """
    client = await get_client()
    path = _aporte_path(uid_ofuscado, ts)

    full_tags = dict(tags)
    full_tags["author_uid"] = uid_ofuscado

    encoded = texto.encode("utf-8")
    payload_size = len(encoded)

    # ── Step 1: create object only if not already on-chain ────────────
    object_exists = False
    try:
        await client.object.get_object_head(BUCKET_NAME, path)
        object_exists = True
        logger.debug("Aporte %s ya existe en Greenfield — saltando create_object", path)
    except Exception:
        object_exists = False

    if not object_exists:
        await client.object.create_object(
            BUCKET_NAME,
            path,
            io.BytesIO(encoded),
            CreateObjectOptions(
                visibility=VisibilityType.VISIBILITY_TYPE_PRIVATE,
                content_type="text/plain; charset=utf-8",
            ),
        )
        # Give the SP time to index the new object before put_object
        await asyncio.sleep(_SP_SYNC_DELAY)

    # ── Step 2: upload content to SP (best-effort) ────────────────────
    # Small objects are often auto-sealed by the SP immediately after
    # create_object, making put_object fail with error 50004 / 50002.
    # Content is already on-chain in that case; tags are what matters.
    try:
        await client.object.put_object(
            bucket_name=BUCKET_NAME,
            object_name=path,
            object_size=payload_size,
            reader=io.BytesIO(encoded),
            opts=PutObjectOptions(content_type="text/plain; charset=utf-8"),
        )
    except Exception as put_exc:
        logger.warning(
            "put_object para aporte %s: %s — continuando con MsgSetTag",
            path, put_exc,
        )

    # ── Step 3: set metadata tags (critical — never skip) ─────────────
    resource = (
        f"grn:{ResourceType.RESOURCE_TYPE_OBJECT.value}"
        f"::{BUCKET_NAME}/{path}"
    )
    msg_set = MsgSetTag(
        operator=_key_manager.address if _key_manager else "",
        resource=resource,
        tags=_dict_to_tags(full_tags),
    )
    tx_result = await client.blockchain_client.broadcast_message(
        messages=[msg_set],
        type_url=[MSG_SET_TAG_TYPE_URL],
    )

    # Extract blockchain tx hash for user display (best-effort)
    tx_hash = _extract_txhash(tx_result)

    logger.info("✅ Aporte escrito en %s (tx: %s)", path, tx_hash or "N/A")
    return tx_hash if tx_hash else path


@retry(
    retry=retry_if_exception_type(
        (httpx.HTTPError, ConnectionError, TimeoutError, OSError)
    ),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
async def read_aporte(path: str) -> Tuple[str, Dict[str, str]]:
    """
    Lee el contenido textual y los tags de un aporte desde Greenfield.
    Retorna (texto, tags_dict).
    """
    client = await get_client()
    raw_data, obj_info = await client.object.get_object(
        BUCKET_NAME, path, GetObjectOption()
    )
    texto = (
        raw_data.decode("utf-8")
        if isinstance(raw_data, bytes)
        else str(raw_data)
    )
    tags = _tags_to_dict(obj_info.tags)
    return texto, tags


# ═══════════════════════════════════════════════════════════════════════
# LISTADO DE APORTES DE UN USUARIO
# ═══════════════════════════════════════════════════════════════════════

@retry(
    retry=retry_if_exception_type(
        (httpx.HTTPError, ConnectionError, TimeoutError, OSError)
    ),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
async def list_aportes(
    uid_ofuscado: str, limit: int = 50
) -> List[Dict[str, Any]]:
    """
    Lista los últimos aportes de un usuario recorriendo prefijos
    aisynergix/aportes/YYYY-MM/{uid_ofuscado}_*.
    Retorna lista de dicts con {path, size, tags}.
    """
    client = await get_client()
    resultados: List[Dict[str, Any]] = []

    ahora = datetime.now(timezone.utc)
    for offset in range(12):
        year = ahora.year
        month = ahora.month - offset
        while month <= 0:
            month += 12
            year -= 1

        prefix = (
            f"aisynergix/aportes/{year:04d}-{month:02d}/{uid_ofuscado}_"
        )
        opts = ListObjectsOptions(
            prefix=prefix, max_keys=limit, delimiter=""
        )
        try:
            res: ListObjectsResult = await client.object.list_objects(
                BUCKET_NAME, opts
            )
            for obj in res.objects if hasattr(res, "objects") else []:
                try:
                    obj_info = await client.object.get_object_head(
                        BUCKET_NAME, obj.object_name
                    )
                    tags = _tags_to_dict(obj_info.tags)
                    resultados.append(
                        {
                            "path": obj.object_name,
                            "size": obj_info.payload_size,
                            "tags": tags,
                        }
                    )
                except Exception:
                    continue
        except Exception:
            continue

        if len(resultados) >= limit:
            break

    resultados.sort(key=lambda x: x["path"], reverse=True)
    return resultados[:limit]


# ═══════════════════════════════════════════════════════════════════════
# DATOS GLOBALES: JSONs en Greenfield (top10, config, challenges)
# ═══════════════════════════════════════════════════════════════════════

@retry(
    retry=retry_if_exception_type(
        (httpx.HTTPError, ConnectionError, TimeoutError, OSError)
    ),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
async def read_json_data(data_path: str) -> Dict[str, Any]:
    """
    Lee un archivo JSON almacenado en Greenfield.
    Si no existe, retorna dict vacío.
    """
    client = await get_client()
    try:
        raw_data, _ = await client.object.get_object(
            BUCKET_NAME, data_path, GetObjectOption()
        )
        text = (
            raw_data.decode("utf-8")
            if isinstance(raw_data, bytes)
            else raw_data
        )
        return json.loads(text)
    except Exception:
        return {}


@retry(
    retry=retry_if_exception_type(
        (httpx.HTTPError, ConnectionError, TimeoutError, OSError)
    ),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
async def write_json_data(data_path: str, data: Dict[str, Any]) -> None:
    """
    Crea o sobrescribe un archivo JSON en Greenfield.
    ─────────────────────────────────────────────────────────
    HÍBRIDO: MsgCreateObject broadcast + put_object para los bytes.
    ─────────────────────────────────────────────────────────
    """
    client = await get_client()
    content = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    reader = io.BytesIO(content)
    payload_size = len(content)

    exists = False
    try:
        await client.object.get_object_head(BUCKET_NAME, data_path)
        exists = True
    except Exception:
        exists = False

    if exists:
        await client.object.delete_object(BUCKET_NAME, data_path)
        await asyncio.sleep(_SP_SYNC_DELAY)

    await client.object.create_object(
        BUCKET_NAME,
        data_path,
        io.BytesIO(content),
        CreateObjectOptions(
            visibility=VisibilityType.VISIBILITY_TYPE_PRIVATE,
            content_type="application/json",
        ),
    )
    await asyncio.sleep(_SP_SYNC_DELAY)
    await client.object.put_object(
        bucket_name=BUCKET_NAME,
        object_name=data_path,
        object_size=payload_size,
        reader=io.BytesIO(content),
        opts=PutObjectOptions(content_type="application/json"),
    )
    logger.debug("JSON escrito en %s", data_path)


# ═══════════════════════════════════════════════════════════════════════
# BRAIN POINTER
# ═══════════════════════════════════════════════════════════════════════

async def get_brain_pointer() -> str:
    """
    Lee la versión actual del cerebro federado.
    Archivo: aisynergix/data/brain_pointer (0 bytes, tag latest_v).
    """
    client = await get_client()
    try:
        obj_info = await client.object.get_object_head(
            BUCKET_NAME, "aisynergix/data/brain_pointer"
        )
        tags = _tags_to_dict(obj_info.tags)
        return tags.get("latest_v", "v0.0.0")
    except Exception:
        return "v0.0.0"


async def set_brain_pointer(version: str) -> None:
    """
    Actualiza el brain_pointer con una nueva versión.
    HÍBRIDO: MsgCreateObject si no existe, MsgSetTag si ya existe.
    """
    client = await get_client()
    data_path = "aisynergix/data/brain_pointer"

    exists = False
    try:
        await client.object.get_object_head(BUCKET_NAME, data_path)
        exists = True
    except Exception:
        exists = False

    if not exists:
        await client.object.create_object(
            BUCKET_NAME,
            data_path,
            io.BytesIO(b""),
            CreateObjectOptions(
                visibility=VisibilityType.VISIBILITY_TYPE_PRIVATE,
                content_type="application/octet-stream",
            ),
        )
        await asyncio.sleep(_SP_SYNC_DELAY)
        await client.object.put_object(
            bucket_name=BUCKET_NAME,
            object_name=data_path,
            object_size=0,
            reader=io.BytesIO(b""),
            opts=PutObjectOptions(content_type="application/octet-stream"),
        )

    resource = (
        f"grn:{ResourceType.RESOURCE_TYPE_OBJECT.value}"
        f"::{BUCKET_NAME}/{data_path}"
    )
    msg_set = MsgSetTag(
        operator=_key_manager.address if _key_manager else "",
        resource=resource,
        tags=_dict_to_tags({"latest_v": version}),
    )
    await client.blockchain_client.broadcast_message(
        messages=[msg_set],
        type_url=[MSG_SET_TAG_TYPE_URL],
    )
    logger.info("🧠 Brain pointer → %s", version)


# ═══════════════════════════════════════════════════════════════════════
# RESET MASIVO DE daily_aportes_count (CRON DIARIO)
# ═══════════════════════════════════════════════════════════════════════

@retry(
    retry=retry_if_exception_type(
        (httpx.HTTPError, ConnectionError, TimeoutError, OSError)
    ),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
)
async def reset_all_daily_counts() -> int:
    """
    Recorre cada usuario en aisynergix/users/ y pone
    daily_aportes_count = 0.  No toca points, rank ni total_uses_count.
    Retorna el total de usuarios actualizados.
    """
    client = await get_client()
    count = 0
    prefix = "aisynergix/users/"

    opts = ListObjectsOptions(prefix=prefix, max_keys=1000, delimiter="")
    try:
        res = await client.object.list_objects(BUCKET_NAME, opts)
        for obj in res.objects if hasattr(res, "objects") else []:
            try:
                obj_info = await client.object.get_object_head(
                    BUCKET_NAME, obj.object_name
                )
                current = _tags_to_dict(obj_info.tags)
                current["daily_aportes_count"] = "0"

                resource = (
                    f"grn:{ResourceType.RESOURCE_TYPE_OBJECT.value}"
                    f"::{BUCKET_NAME}/{obj.object_name}"
                )
                msg = MsgSetTag(
                    operator=_key_manager.address if _key_manager else "",
                    resource=resource,
                    tags=_dict_to_tags(current),
                )
                await client.blockchain_client.broadcast_message(
                    messages=[msg],
                    type_url=[MSG_SET_TAG_TYPE_URL],
                )
                count += 1
            except Exception:
                continue
    except Exception:
        pass

    logger.info("📅 Reset diario: %d usuarios actualizados", count)
    return count


# ═══════════════════════════════════════════════════════════════════════
# LEADERBOARD (top10 desde datos reales)
# ═══════════════════════════════════════════════════════════════════════

# In-process cache — avoids put_object on a previously-SEALED path.
# The SP rejects put_object with 50004 when we delete+recreate an object
# at the same path it already knows as SEALED.  Storing the leaderboard
# only in RAM (rebuilt every 10 min by the cron job) is safer and faster.
_top10_cache: List[Dict[str, Any]] = []


def get_top10_cached() -> List[Dict[str, Any]]:
    """Return the last-computed top-10 list from the in-process cache."""
    return _top10_cache


@retry(
    retry=retry_if_exception_type(
        (httpx.HTTPError, ConnectionError, TimeoutError, OSError)
    ),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
async def rebuild_top10() -> List[Dict[str, Any]]:
    """
    Reconstruye el leaderboard global leyendo todos los usuarios reales
    de Greenfield y actualiza el cache RAM.  No escribe a Greenfield para
    evitar el ciclo delete+create+put que falla con error 50004 cuando el
    SP tiene en caché el objeto anterior como SEALED.
    Retorna la lista de los 10 mejores.
    """
    global _top10_cache
    client = await get_client()
    usuarios: List[Dict[str, Any]] = []
    prefix = "aisynergix/users/"

    opts = ListObjectsOptions(prefix=prefix, max_keys=1000, delimiter="")
    try:
        res = await client.object.list_objects(BUCKET_NAME, opts)
        for obj in res.objects if hasattr(res, "objects") else []:
            try:
                obj_info = await client.object.get_object_head(
                    BUCKET_NAME, obj.object_name
                )
                tags = _tags_to_dict(obj_info.tags)
                usuarios.append(
                    {
                        "uid": obj.object_name.replace(prefix, ""),
                        "points": int(tags.get("points", "0")),
                        "rank": tags.get("rank", "🌱 Iniciado"),
                        "total_uses_count": int(
                            tags.get("total_uses_count", "0")
                        ),
                    }
                )
            except Exception:
                continue
    except Exception:
        pass

    usuarios.sort(key=lambda u: u["points"], reverse=True)
    top10 = usuarios[:10]
    _top10_cache = top10
    logger.info(
        "🏆 Leaderboard reconstruido: %d usuarios en top10 (RAM cache)", len(top10)
    )

    # Best-effort: write top10.json to Greenfield the FIRST TIME only.
    # We never delete+recreate it (that causes SP error 50004 on SEALED objects).
    # The RAM cache is the authoritative source; this write is for visibility in DCellar.
    await _write_top10_if_missing(top10)

    return top10


async def _write_top10_if_missing(top10: List[Dict[str, Any]]) -> None:
    """Create aisynergix/data/top10.json only if it doesn't already exist."""
    data_path = "aisynergix/data/top10.json"
    try:
        client = await get_client()
        try:
            await client.object.get_object_head(BUCKET_NAME, data_path)
            return  # Already exists — never overwrite a SEALED object
        except Exception:
            pass  # Does not exist yet — create it

        content = json.dumps(top10, ensure_ascii=False, indent=2).encode("utf-8")
        payload_size = len(content)

        await client.object.create_object(
            BUCKET_NAME,
            data_path,
            io.BytesIO(content),
            CreateObjectOptions(
                visibility=VisibilityType.VISIBILITY_TYPE_PRIVATE,
                content_type="application/json",
            ),
        )
        await asyncio.sleep(_SP_SYNC_DELAY)
        await client.object.put_object(
            bucket_name=BUCKET_NAME,
            object_name=data_path,
            object_size=payload_size,
            reader=io.BytesIO(content),
            opts=PutObjectOptions(content_type="application/json"),
        )
        logger.info("📊 top10.json creado en Greenfield (primera vez)")
    except Exception as exc:
        logger.warning("No se pudo escribir top10.json en Greenfield: %s", exc)


# ═══════════════════════════════════════════════════════════════════════
# CHALLENGES SEMANALES
# ═══════════════════════════════════════════════════════════════════════

# Same rationale as top10: avoid write_json_data (put_object) on a path
# the SP has previously seen as SEALED.  Challenges are regenerated weekly;
# losing the current one on container restart is acceptable.
_challenge_cache: Optional[Dict[str, Any]] = None


async def get_current_challenge() -> Optional[Dict[str, Any]]:
    """Obtiene el challenge semanal actual (RAM cache, sin lectura de Greenfield)."""
    return _challenge_cache


async def save_challenge(challenge: Dict[str, Any]) -> None:
    """Guarda el challenge semanal en el cache RAM."""
    global _challenge_cache
    _challenge_cache = challenge
    logger.info("🎯 Challenge guardado en cache RAM: %s", challenge.get("id"))


# ═══════════════════════════════════════════════════════════════════════
# SUBIDA DE LOGS
# ═══════════════════════════════════════════════════════════════════════

async def upload_log(date_str: str, log_content: str) -> None:
    """
    Sube un archivo de log diario comprimido a Greenfield.
    Ruta: aisynergix/logs/{YYYY-MM-DD}.log.gz
    HÍBRIDO: MsgCreateObject broadcast + put_object.
    El log diario se escribe UNA sola vez (si ya existe, se omite) para
    evitar el ciclo delete+recreate que genera error 50004 del SP.
    """
    import gzip as _gzip
    client = await get_client()
    data_path = f"aisynergix/logs/{date_str}.log.gz"

    # Skip if today's log already exists — delete+recreate causes SP error 50004
    try:
        await client.object.get_object_head(BUCKET_NAME, data_path)
        logger.debug("Log %s ya existe en Greenfield — omitiendo subida", data_path)
        return
    except Exception:
        pass  # Does not exist yet — proceed to create

    compressed = _gzip.compress(log_content.encode("utf-8"))
    payload_size = len(compressed)

    await client.object.create_object(
        BUCKET_NAME,
        data_path,
        io.BytesIO(compressed),
        CreateObjectOptions(
            visibility=VisibilityType.VISIBILITY_TYPE_PRIVATE,
            content_type="application/gzip",
        ),
    )
    await asyncio.sleep(_SP_SYNC_DELAY)
    await client.object.put_object(
        bucket_name=BUCKET_NAME,
        object_name=data_path,
        object_size=payload_size,
        reader=io.BytesIO(compressed),
        opts=PutObjectOptions(content_type="application/gzip"),
    )
    logger.info("📄 Log subido: %s", data_path)


# ═══════════════════════════════════════════════════════════════════════
# UTILIDAD: LISTA DE TODOS LOS USUARIOS
# ═══════════════════════════════════════════════════════════════════════

async def get_all_user_uids() -> List[str]:
    """
    Obtiene todos los UIDs ofuscados de usuarios registrados.
    Útil para broadcasts masivos y crons.
    """
    client = await get_client()
    uids: List[str] = []
    prefix = "aisynergix/users/"

    opts = ListObjectsOptions(prefix=prefix, max_keys=1000, delimiter="")
    try:
        res = await client.object.list_objects(BUCKET_NAME, opts)
        for obj in res.objects if hasattr(res, "objects") else []:
            uid = obj.object_name.replace(prefix, "")
            if uid:
                uids.append(uid)
    except Exception:
        pass

    return uids


# ═══════════════════════════════════════════════════════════════════════
# AI GUARD — Anti-jailbreak pattern list
# ═══════════════════════════════════════════════════════════════════════

async def load_ai_guard() -> List[str]:
    """Load ai_guard.txt from Greenfield; create with defaults only if truly missing."""
    global _ai_guard_patterns
    client = await get_client()
    path = "ai_guard.txt"

    default_content = (
        "# Synergix AI Guard — anti-jailbreak patterns\n"
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
    default_patterns = [
        line.strip()
        for line in default_content.splitlines()
        if line.strip() and not line.startswith("#")
    ]

    # Step 1: try to read the file content
    try:
        raw, _ = await client.object.get_object(BUCKET_NAME, path, GetObjectOption())
        text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        patterns = [
            line.strip()
            for line in text.splitlines()
            if line.strip() and not line.startswith("#")
        ]
        _ai_guard_patterns = patterns
        logger.info("🛡️ AI Guard cargado: %d patrones", len(patterns))
        return patterns
    except Exception:
        pass

    # Step 2: reading failed — check if the object exists on-chain (SEALED/CREATED)
    object_exists = False
    try:
        await client.object.get_object_head(BUCKET_NAME, path)
        object_exists = True
    except Exception:
        object_exists = False

    if object_exists:
        # Object exists but content is unreadable (SP quirk / SEALED state).
        # Use default patterns in RAM without attempting to create a duplicate.
        logger.info("🛡️ ai_guard.txt existe en Greenfield — usando patrones por defecto en RAM")
        _ai_guard_patterns = default_patterns
        return default_patterns

    # Step 3: object genuinely doesn't exist — create it with defaults
    try:
        encoded = default_content.encode("utf-8")
        await client.object.create_object(
            BUCKET_NAME,
            path,
            io.BytesIO(encoded),
            CreateObjectOptions(
                visibility=VisibilityType.VISIBILITY_TYPE_PRIVATE,
                content_type="text/plain",
            ),
        )
        await asyncio.sleep(_SP_SYNC_DELAY)
        await client.object.put_object(
            bucket_name=BUCKET_NAME,
            object_name=path,
            object_size=len(encoded),
            reader=io.BytesIO(encoded),
            opts=PutObjectOptions(content_type="text/plain"),
        )
        logger.info("🛡️ ai_guard.txt creado con patrones por defecto")
    except Exception as create_exc:
        logger.warning("No se pudo crear ai_guard.txt: %s", create_exc)

    _ai_guard_patterns = default_patterns
    return default_patterns


def get_ai_guard_patterns() -> List[str]:
    """Return current in-memory AI guard pattern list."""
    return _ai_guard_patterns


def check_ai_guard(text: str) -> bool:
    """Return True if text contains a blocked pattern (case-insensitive)."""
    text_lower = text.lower()
    return any(pattern.lower() in text_lower for pattern in _ai_guard_patterns)


# ═══════════════════════════════════════════════════════════════════════
# EMERGENCY LOCK — Write-blocker switch (0-byte object in bucket root)
# ═══════════════════════════════════════════════════════════════════════

async def check_emergency_lock() -> bool:
    """Check if emergency_lock exists in Greenfield and update global flag."""
    global _emergency_lock_active
    client = await get_client()
    try:
        await client.object.get_object_head(BUCKET_NAME, "emergency_lock")
        _emergency_lock_active = True
        return True
    except Exception:
        _emergency_lock_active = False
        return False


def is_emergency_locked() -> bool:
    """Return current in-memory emergency lock state (no network call)."""
    return _emergency_lock_active


async def create_emergency_lock() -> None:
    """Create emergency_lock ghost object to halt all writes."""
    global _emergency_lock_active
    client = await get_client()
    path = "emergency_lock"
    try:
        await client.object.get_object_head(BUCKET_NAME, path)
        _emergency_lock_active = True
        return
    except Exception:
        pass
    await client.object.create_object(
        BUCKET_NAME,
        path,
        io.BytesIO(b""),
        CreateObjectOptions(
            visibility=VisibilityType.VISIBILITY_TYPE_PRIVATE,
            content_type="application/octet-stream",
        ),
    )
    await asyncio.sleep(_SP_SYNC_DELAY)
    try:
        await client.object.put_object(
            bucket_name=BUCKET_NAME,
            object_name=path,
            object_size=0,
            reader=io.BytesIO(b""),
            opts=PutObjectOptions(content_type="application/octet-stream"),
        )
    except Exception:
        pass
    _emergency_lock_active = True
    logger.warning("🔒 Emergency lock ACTIVADO")


async def delete_emergency_lock() -> None:
    """Remove emergency_lock to resume writes."""
    global _emergency_lock_active
    client = await get_client()
    try:
        await client.object.delete_object(BUCKET_NAME, "emergency_lock")
    except Exception:
        pass
    _emergency_lock_active = False
    logger.warning("🔓 Emergency lock DESACTIVADO")


# ═══════════════════════════════════════════════════════════════════════
# SYSTEM CONFIG — Centralized thresholds in Greenfield
# ═══════════════════════════════════════════════════════════════════════

async def _write_system_config_if_missing() -> None:
    """Create aisynergix/data/system_config.json in Greenfield if absent."""
    data_path = "aisynergix/data/system_config.json"
    try:
        client = await get_client()
        try:
            await client.object.get_object_head(BUCKET_NAME, data_path)
            return
        except Exception:
            pass
        content = json.dumps(_DEFAULT_SYSTEM_CONFIG, ensure_ascii=False, indent=2).encode("utf-8")
        await client.object.create_object(
            BUCKET_NAME,
            data_path,
            io.BytesIO(content),
            CreateObjectOptions(
                visibility=VisibilityType.VISIBILITY_TYPE_PRIVATE,
                content_type="application/json",
            ),
        )
        await asyncio.sleep(_SP_SYNC_DELAY)
        await client.object.put_object(
            bucket_name=BUCKET_NAME,
            object_name=data_path,
            object_size=len(content),
            reader=io.BytesIO(content),
            opts=PutObjectOptions(content_type="application/json"),
        )
        logger.info("⚙️ system_config.json creado con valores por defecto")
    except Exception as exc:
        logger.warning("No se pudo crear system_config.json: %s", exc)


async def load_system_config() -> Dict[str, Any]:
    """Load system_config.json from Greenfield; create with defaults if missing."""
    global _system_config
    data_path = "aisynergix/data/system_config.json"
    try:
        data = await read_json_data(data_path)
        if data:
            merged = {**_DEFAULT_SYSTEM_CONFIG, **data}
            _system_config = merged
            logger.info("⚙️ system_config.json cargado desde Greenfield")
            return merged
    except Exception:
        pass
    try:
        await _write_system_config_if_missing()
    except Exception:
        pass
    _system_config = dict(_DEFAULT_SYSTEM_CONFIG)
    return _system_config


def get_system_config() -> Dict[str, Any]:
    """Return current system config dict (falls back to defaults if not loaded)."""
    if not _system_config:
        return dict(_DEFAULT_SYSTEM_CONFIG)
    return _system_config


# ── Alias para compatibilidad con sync_brain.py ───────────────────────
get_greenfield_client = get_client


# ═══════════════════════════════════════════════════════════════════════
# EXPORTACIONES PÚBLICAS
# ═══════════════════════════════════════════════════════════════════════

__all__ = [
    "get_client",
    "get_greenfield_client",
    "ensure_bucket_exists",
    "_hash_uid",
    "read_user_tags",
    "write_user_tags",
    "write_aporte",
    "read_aporte",
    "list_aportes",
    "read_json_data",
    "write_json_data",
    "get_brain_pointer",
    "set_brain_pointer",
    "reset_all_daily_counts",
    "rebuild_top10",
    "get_top10_cached",
    "get_all_user_uids",
    "get_current_challenge",
    "save_challenge",
    "upload_log",
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
    # Constants
    "BUCKET_NAME",
    "PRIVATE_KEY",
    "GREENFIELD_RPC_URL",
    "GREENFIELD_CHAIN_ID",
    "SP_ENDPOINT",
    "BUCKET_ID",
    "TELEGRAM_TOKEN",
]
