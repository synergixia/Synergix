"""
greenfield.py — Servicio Web3 de Synergix.
Implementa firma ECDSA V4 nativa (sin Node.js) usando eth-account y hmac.
Timestamp estricto UTC para evitar invalidaciones por desincronización NTP.
"""

import hashlib
import hmac
import json
import logging
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Optional, Dict, List
import httpx
from eth_account import Account
from eth_account.messages import encode_defunct

from aisynergix.config.constants import (
    APORTES_PREFIX,
    BRAIN_POINTER_OBJECT,
    GREENFIELD_BUCKET,
    GREENFIELD_SP_ENDPOINT,
    OPERATOR_ADDRESS,
    OPERATOR_PRIVATE_KEY,
    SIGNING_ALGORITHM,
    SIGNING_REGION,
    SIGNING_SERVICE,
    TOP10_JSON_OBJECT,
    USERS_PREFIX,
)

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# CONSTRUCCIÓN DEL CANONICAL REQUEST (Firma V4 — Greenfield ECDSA)
#
# PASO A PASO DE LA FIRMA ECDSA V4:
#
# 1. Canonical URI:    La ruta del objeto URL-encoded (sin el host).
#    Ej: /synergixai/aisynergix/users/6g7t8k9ti3p0 → /synergixai%2Faisynergix%2Fusers%2F6g7t8k9ti3p0
#
# 2. Canonical Query: Parámetros de query ordenados alfabéticamente, URL-encoded.
#    Ej: {"prefix": "aisynergix/users", "delimiter": "/"} → "delimiter=%2F&prefix=aisynergix%2Fusers"
#
# 3. Canonical Headers: Headers relevantes en minúsculas, ordenados y con \n.
#    Headers obligatorios: "host", "x-gnfd-date", más cualquier header personalizado.
#    Ej: "host:gnfd-testnet-sp1.bnbchain.org\nx-gnfd-date:20240715T183045Z\n"
#
# 4. Signed Headers: Lista de header keys separados por ";".
#    Ej: "host;x-gnfd-date"
#
# 5. Payload Hash: SHA-256 del body (o "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855" para body vacío).
#
# 6. Canonical Request: Concatenación de los 5 pasos anteriores con \n.
#    Ej: "GET\n/...\nquery\nheaders\nsigned_headers\npayload_hash"
#
# 7. String to Sign: "GNFD1-ECDSA\n{timestamp}\n{sha256(CanonicalRequest)}"
#
# 8. Firma ECDSA: eth_account.sign_message sobre el String to Sign.
#
# 9. Header Auth: "GNFD1-ECDSA Credential={address}, SignedMsg={hash}, Signature={sig}"
#
# NOTA: El timestamp debe ser estricto UTC para evitar invalidaciones por desincronización NTP.
# ─────────────────────────────────────────────────────────────────────────────

def _sha256_hex(data: bytes) -> str:
    """Devuelve el SHA-256 de los bytes dados como string hexadecimal en minúsculas."""
    return hashlib.sha256(data).hexdigest()


def _build_canonical_request(
    method: str,
    path: str,
    query_params: dict[str, str],
    headers: dict[str, str],
    body: bytes,
) -> tuple[str, str]:
    """
    Construye el Canonical Request según especificación Greenfield ECDSA V4.
    
    Args:
        method: Método HTTP (GET, HEAD, PUT, etc.)
        path: Ruta del objeto (ej: /bucket/key)
        query_params: Parámetros de query
        headers: Headers HTTP (ya en minúsculas)
        body: Cuerpo de la petición en bytes
    
    Returns:
        tuple: (canonical_request_string, signed_headers_string)
    """
    # Paso 1: Canonical URI (URL-encoded, safe="/")
    canonical_uri = urllib.parse.quote(path, safe="/")
    
    # Paso 2: Canonical Query String (ordenado alfabéticamente)
    sorted_query = sorted(query_params.items())
    canonical_query = "&".join(
        f"{urllib.parse.quote(k, safe='')}={urllib.parse.quote(v, safe='')}"
        for k, v in sorted_query
    )
    
    # Paso 3: Canonical Headers (claves en minúsculas, ordenadas)
    sorted_headers = sorted((k.lower(), str(v).strip()) for k, v in headers.items())
    canonical_headers = "".join(f"{k}:{v}\n" for k, v in sorted_headers)
    
    # Paso 4: Signed Headers (claves separadas por ";")
    signed_headers_str = ";".join(k for k, _ in sorted_headers)
    
    # Paso 5: Payload Hash (SHA-256 del body)
    payload_hash = _sha256_hex(body)
    
    # Paso 6: Canonical Request (concatenación con \n)
    canonical_request = "\n".join([
        method.upper(),
        canonical_uri,
        canonical_query,
        canonical_headers,
        signed_headers_str,
        payload_hash,
    ])
    
    logger.debug(f"Canonical Request construido:\n{canonical_request}")
    return canonical_request, signed_headers_str


def _build_authorization_header(
    method: str,
    path: str,
    query_params: dict[str, str],
    headers: dict[str, str],
    body: bytes,
    timestamp: str,
) -> str:
    """
    Genera el header 'Authorization' usando firma ECDSA V4 de Greenfield.
    
    Args:
        method: Método HTTP
        path: Ruta del objeto
        query_params: Parámetros de query
        headers: Headers HTTP (en minúsculas)
        body: Cuerpo de la petición
        timestamp: Timestamp ISO8601 compacto
    
    Returns:
        str: Header Authorization completo
    """
    if not OPERATOR_PRIVATE_KEY:
        raise ValueError("OPERATOR_PRIVATE_KEY no configurada. Verifica el archivo .env")
    
    # Construir Canonical Request
    canonical_request, signed_headers_str = _build_canonical_request(
        method, path, query_params, headers, body
    )
    
    # Hash del Canonical Request
    canonical_request_hash = _sha256_hex(canonical_request.encode("utf-8"))
    
    # String to Sign (formato: ALGORITHM + \n + timestamp + \n + hash)
    string_to_sign = f"{SIGNING_ALGORITHM}\n{timestamp}\n{canonical_request_hash}"
    
    # Firma ECDSA usando eth-account
    message = encode_defunct(text=string_to_sign)
    signed = Account.sign_message(message, private_key=OPERATOR_PRIVATE_KEY)
    signature_hex = signed.signature.hex()
    
    # Construir header Authorization
    authorization = (
        f"{SIGNING_ALGORITHM} "
        f"Credential={OPERATOR_ADDRESS},"
        f"SignedMsg={canonical_request_hash},"
        f"Signature={signature_hex}"
    )
    
    logger.debug(f"Authorization header generado: {authorization[:50]}...")
    return authorization


def _get_timestamp() -> str:
    """
    Genera timestamp ISO8601 compacto estricto UTC.
    
    Formato: '20240715T183045Z' (YYYYMMDDThhmmssZ)
    Crucial para evitar invalidaciones por desincronización NTP.
    """
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _build_signed_headers(
    method: str,
    path: str,
    query_params: dict[str, str],
    extra_headers: dict[str, str],
    body: bytes,
) -> dict[str, str]:
    """
    Construye headers completos con firma ECDSA V4.
    
    Args:
        method: Método HTTP
        path: Ruta del objeto
        query_params: Parámetros de query
        extra_headers: Headers adicionales (Content-Type, etc.)
        body: Cuerpo de la petición
    
    Returns:
        dict: Headers HTTP firmados listos para usar
    """
    timestamp = _get_timestamp()
    
    # Extraer host del endpoint (sin protocolo)
    host = GREENFIELD_SP_ENDPOINT.replace("https://", "").replace("http://", "")
    
    # Headers base obligatorios
    base_headers = {
        "host": host,
        "x-gnfd-date": timestamp,
        **{k.lower(): str(v) for k, v in extra_headers.items()},
    }
    
    # Generar header Authorization
    authorization = _build_authorization_header(
        method, path, query_params, base_headers, body, timestamp
    )
    
    # Headers finales (con capitalización adecuada para HTTP)
    final_headers = {
        "Host": host,
        "X-Gnfd-Date": timestamp,
        "Authorization": authorization,
        **extra_headers,
    }
    
    return final_headers


def _build_tags_xml(tags: Dict[str, str]) -> str:
    """
    Genera el XML de Tagging para Greenfield.
    
    Args:
        tags: Diccionario de tags (key → value)
    
    Returns:
        str: XML válido para operación PUT tagging
    """
    xml = "<Tagging><TagSet>"
    for k, v in tags.items():
        # Escape básico para XML
        k_escaped = k.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        v_escaped = v.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        xml += f"<Tag><Key>{k_escaped}</Key><Value>{v_escaped}</Value></Tag>"
    xml += "</TagSet></Tagging>"
    return xml


# ─────────────────────────────────────────────────────────────────────────────
# API PÚBLICA — OPERACIONES WEB3
# ─────────────────────────────────────────────────────────────────────────────

async def get_user_metadata(uid_ofuscado: str) -> Optional[Dict[str, str]]:
    """
    HEAD request para obtener metadatos (Tags) del usuario desde Greenfield.
    
    Args:
        uid_ofuscado: UID ofuscado del usuario (ej: "6g7t8k9ti3p0")
    
    Returns:
        Optional[Dict]: Diccionario de tags o None si el usuario no existe
    """
    object_path = f"/{GREENFIELD_BUCKET}/{USERS_PREFIX}/{uid_ofuscado}"
    headers = _build_signed_headers("HEAD", object_path, {}, {}, b"")
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.head(f"{GREENFIELD_SP_ENDPOINT}{object_path}", headers=headers)
            
            if response.status_code == 404:
                logger.debug(f"Usuario {uid_ofuscado} no existe en Greenfield (404)")
                return None
            
            response.raise_for_status()
            
            # Extraer tags del header X-Gnfd-Object-Attributes
            raw_attr = response.headers.get("X-Gnfd-Object-Attributes", "{}")
            try:
                tags = json.loads(raw_attr)
                logger.debug(f"Tags obtenidos para {uid_ofuscado}: {tags}")
                return tags
            except json.JSONDecodeError:
                logger.warning(f"Tags JSON inválido para {uid_ofuscado}: {raw_attr}")
                return {}
                
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error get_user_metadata {uid_ofuscado}: {e.response.status_code}")
            return None
        except Exception as e:
            logger.error(f"Error get_user_metadata {uid_ofuscado}: {e}", exc_info=True)
            return None


async def update_user_metadata(uid_ofuscado: str, tags: Dict[str, str]) -> bool:
    """
    PUT tagging para actualizar metadatos del usuario en Greenfield.
    
    Args:
        uid_ofuscado: UID ofuscado del usuario
        tags: Diccionario de tags a actualizar
    
    Returns:
        bool: True si la actualización fue exitosa
    """
    object_path = f"/{GREENFIELD_BUCKET}/{USERS_PREFIX}/{uid_ofuscado}"
    body = _build_tags_xml(tags).encode("utf-8")
    
    extra_headers = {
        "Content-Type": "application/xml",
        "Content-Length": str(len(body))
    }
    
    headers = _build_signed_headers("PUT", object_path, {"tagging": ""}, extra_headers, body)
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.put(
                f"{GREENFIELD_SP_ENDPOINT}{object_path}?tagging",
                headers=headers,
                content=body
            )
            response.raise_for_status()
            logger.info(f"Tags actualizados para {uid_ofuscado}: {tags}")
            return True
            
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error update_user_metadata {uid_ofuscado}: {e.response.status_code}")
            return False
        except Exception as e:
            logger.error(f"Error update_user_metadata {uid_ofuscado}: {e}", exc_info=True)
            return False


async def put_object(
    object_key: str,
    body: bytes,
    content_type: str = "application/octet-stream",
    tags: Optional[Dict[str, str]] = None
) -> bool:
    """
    Sube un objeto a Greenfield con tags opcionales.
    
    Args:
        object_key: Clave del objeto (ej: "aisynergix/users/6g7t8k9ti3p0")
        body: Contenido del objeto en bytes
        content_type: Tipo MIME del contenido
        tags: Tags a asociar con el objeto
    
    Returns:
        bool: True si la subida fue exitosa
    """
    object_path = f"/{GREENFIELD_BUCKET}/{object_key}"
    
    extra_headers = {
        "Content-Type": content_type,
        "Content-Length": str(len(body))
    }
    
    if tags:
        extra_headers["X-Gnfd-Object-Attributes"] = json.dumps(tags)
    
    headers = _build_signed_headers("PUT", object_path, {}, extra_headers, body)
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            response = await client.put(
                f"{GREENFIELD_SP_ENDPOINT}{object_path}",
                headers=headers,
                content=body
            )
            response.raise_for_status()
            logger.info(f"Objeto subido: {object_key} ({len(body)} bytes)")
            return True
            
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error put_object {object_key}: {e.response.status_code}")
            return False
        except Exception as e:
            logger.error(f"Error put_object {object_key}: {e}", exc_info=True)
            return False


async def get_object(object_key: str) -> Optional[bytes]:
    """
    Descarga un objeto de Greenfield.
    
    Args:
        object_key: Clave del objeto
    
    Returns:
        Optional[bytes]: Contenido del objeto o None si no existe
    """
    object_path = f"/{GREENFIELD_BUCKET}/{object_key}"
    headers = _build_signed_headers("GET", object_path, {}, {}, b"")
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            response = await client.get(f"{GREENFIELD_SP_ENDPOINT}{object_path}", headers=headers)
            
            if response.status_code == 404:
                logger.debug(f"Objeto {object_key} no existe en Greenfield (404)")
                return None
            
            response.raise_for_status()
            content = response.content
            logger.debug(f"Objeto descargado: {object_key} ({len(content)} bytes)")
            return content
            
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error get_object {object_key}: {e.response.status_code}")
            return None
        except Exception as e:
            logger.error(f"Error get_object {object_key}: {e}", exc_info=True)
            return None


async def list_objects(prefix: str, delimiter: str = "/") -> List[str]:
    """
    Lista objetos con un prefijo dado en Greenfield.
    
    Args:
        prefix: Prefijo para filtrar objetos
        delimiter: Delimitador para simular directorios
    
    Returns:
        List[str]: Lista de claves de objetos
    """
    object_path = f"/{GREENFIELD_BUCKET}/"
    query_params = {"prefix": prefix, "delimiter": delimiter}
    
    headers = _build_signed_headers("GET", object_path, query_params, {}, b"")
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.get(
                f"{GREENFIELD_SP_ENDPOINT}{object_path}",
                params=query_params,
                headers=headers
            )
            response.raise_for_status()
            
            # Parsear respuesta XML de Greenfield ListObjects
            # Nota: Esta es una implementación simplificada
            content = response.text
            objects = []
            
            # Buscar <Key> elementos en el XML
            import re
            keys = re.findall(r'<Key>([^<]+)</Key>', content)
            
            for key in keys:
                if key.startswith(prefix):
                    objects.append(key)
            
            logger.debug(f"Listados {len(objects)} objetos con prefijo {prefix}")
            return objects
            
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error list_objects {prefix}: {e.response.status_code}")
            return []
        except Exception as e:
            logger.error(f"Error list_objects {prefix}: {e}", exc_info=True)
            return []


async def upload_aporte(
    uid_ofuscado: str,
    content: str,
    quality_score: float,
    categoria: str
) -> bool:
    """
    Sube un aporte de conocimiento a Greenfield con estructura YYYY-MM/uid_ofuscado_ts.txt.
    
    Args:
        uid_ofuscado: UID ofuscado del autor
        content: Contenido del aporte
        quality_score: Puntuación de calidad (0-10)
        categoria: Categoría del aporte
    
    Returns:
        bool: True si la subida fue exitosa
    """
    from datetime import datetime
    
    # Generar timestamp actual
    now = datetime.now(timezone.utc)
    timestamp = int(now.timestamp())
    
    # Estructura: aisynergix/aportes/YYYY-MM/uid_ofuscado_timestamp.txt
    month_folder = now.strftime("%Y-%m")
    object_key = f"{APORTES_PREFIX}/{month_folder}/{uid_ofuscado}_{timestamp}.txt"
    
    # Preparar body y tags
    body = content.encode("utf-8")
    
    tags = {
        "author_uid": uid_ofuscado,
        "quality_score": str(quality_score),
        "categoria": categoria,
        "timestamp": str(timestamp),
        "content_length": str(len(content))
    }
    
    # Subir objeto
    success = await put_object(object_key, body, content_type="text/plain", tags=tags)
    
    if success:
        logger.info(f"Aporte subido: {object_key} (score: {quality_score}, cat: {categoria})")
    else:
        logger.error(f"Error subiendo aporte: {object_key}")
    
    return success


async def get_top10_json() -> Optional[Dict[str, Any]]:
    """
    Descarga el archivo top10.json desde Greenfield.
    
    Returns:
        Optional[Dict]: Diccionario con el ranking Top 10 o None si error
    """
    content = await get_object(TOP10_JSON_OBJECT)
    
    if not content:
        logger.warning("No se pudo descargar top10.json desde Greenfield")
        return None
    
    try:
        data = json.loads(content.decode("utf-8"))
        logger.debug(f"Top10.json descargado: {len(data.get('ranking', []))} usuarios")
        return data
    except json.JSONDecodeError as e:
        logger.error(f"Error parseando top10.json: {e}")
        return None
    except Exception as e:
        logger.error(f"Error procesando top10.json: {e}")
        return None
