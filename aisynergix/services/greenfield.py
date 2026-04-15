"""
greenfield.py — Servicio Web3 de Synergix.
Implementa firma ECDSA V4 nativa (sin Node.js) usando eth-account y hmac.
Todas las operaciones de lectura/escritura sobre BNB Greenfield pasan por aquí.
"""

import hashlib
import hmac
import json
import logging
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Optional, Dict

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
    USERS_PREFIX,
)

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# CONSTRUCCIÓN DEL CANONICAL REQUEST (Firma V4 — Greenfield ECDSA)
#
# El proceso de firma sigue estos pasos:
#
#  1. Canonical URI:    La ruta del objeto URL-encoded (sin el host).
#  2. Canonical Query: Parámetros de query ordenados alfabéticamente.
#  3. Canonical Headers: Headers relevantes en minúsculas, ordenados y con \n.
#  4. Signed Headers:  Lista de header keys separados por ";".
#  5. Payload Hash:    SHA-256 del body (o "e3b0..." para body vacío).
#  6. Canonical Request: Concatenación de los 6 pasos anteriores con \n.
#  7. String to Sign:  "GNFD1-ECDSA\n{timestamp}\n{sha256(CanonicalRequest)}"
#  8. Firma ECDSA:     eth_account.sign_message sobre el String to Sign.
#  9. Header Auth:     "GNFD1-ECDSA Credential={address}, SignedMsg={hash}, Signature={sig}"
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
    """Construye el Canonical Request y devuelve (canonical_request, signed_headers_str)."""
    # Paso 1: Canonical URI
    canonical_uri = urllib.parse.quote(path, safe="/")

    # Paso 2: Canonical Query String
    sorted_query = sorted(query_params.items())
    canonical_query = "&".join(
        f"{urllib.parse.quote(k, safe='')}={urllib.parse.quote(v, safe='')}"
        for k, v in sorted_query
    )

    # Paso 3: Canonical Headers
    sorted_headers = sorted((k.lower(), str(v).strip()) for k, v in headers.items())
    canonical_headers = "".join(f"{k}:{v}\n" for k, v in sorted_headers)

    # Paso 4: Signed Headers
    signed_headers_str = ";".join(k for k, _ in sorted_headers)

    # Paso 5: Payload Hash
    payload_hash = _sha256_hex(body)

    # Paso 6: Canonical Request
    canonical_request = "\n".join([
        method.upper(),
        canonical_uri,
        canonical_query,
        canonical_headers,
        signed_headers_str,
        payload_hash,
    ])

    return canonical_request, signed_headers_str

def _build_authorization_header(
    method: str,
    path: str,
    query_params: dict[str, str],
    headers: dict[str, str],
    body: bytes,
    timestamp: str,
) -> str:
    """Genera el header 'Authorization' usando firma ECDSA V4 de Greenfield."""
    if not OPERATOR_PRIVATE_KEY:
        raise ValueError("OPERATOR_PRIVATE_KEY no configurada.")

    canonical_request, signed_headers_str = _build_canonical_request(
        method, path, query_params, headers, body
    )

    canonical_request_hash = _sha256_hex(canonical_request.encode("utf-8"))
    string_to_sign = f"{SIGNING_ALGORITHM}\n{timestamp}\n{canonical_request_hash}"

    message = encode_defunct(text=string_to_sign)
    signed = Account.sign_message(message, private_key=OPERATOR_PRIVATE_KEY)
    signature_hex = signed.signature.hex()

    authorization = (
        f"{SIGNING_ALGORITHM} "
        f"Credential={OPERATOR_ADDRESS},"
        f"SignedMsg={canonical_request_hash},"
        f"Signature={signature_hex}"
    )
    return authorization

def _get_timestamp() -> str:
    """Timestamp ISO8601 compacto: '20240715T183045Z'"""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

def _build_signed_headers(
    method: str,
    path: str,
    query_params: dict[str, str],
    extra_headers: dict[str, str],
    body: bytes,
) -> dict[str, str]:
    """Construye headers completos con firma."""
    timestamp = _get_timestamp()
    host = GREENFIELD_SP_ENDPOINT.replace("https://", "").replace("http://", "")
    
    base_headers = {
        "host": host,
        "x-gnfd-date": timestamp,
        **{k.lower(): str(v) for k, v in extra_headers.items()},
    }

    authorization = _build_authorization_header(
        method, path, query_params, base_headers, body, timestamp
    )

    final_headers = {
        "Host": host,
        "X-Gnfd-Date": timestamp,
        "Authorization": authorization,
        **extra_headers,
    }
    return final_headers

def _build_tags_xml(tags: Dict[str, str]) -> str:
    """Genera el XML de Tagging para Greenfield."""
    xml = "<Tagging><TagSet>"
    for k, v in tags.items():
        xml += f"<Tag><Key>{k}</Key><Value>{v}</Value></Tag>"
    xml += "</TagSet></Tagging>"
    return xml

# ─────────────────────────────────────────────────────────────────────────────
# API PÚBLICA
# ─────────────────────────────────────────────────────────────────────────────

async def get_user_metadata(uid: str) -> Optional[dict[str, str]]:
    """HEAD request para obtener metadatos (Tags) del usuario."""
    object_path = f"/{GREENFIELD_BUCKET}/{USERS_PREFIX}/{uid}"
    headers = _build_signed_headers("HEAD", object_path, {}, {}, b"")

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.head(f"{GREENFIELD_SP_ENDPOINT}{object_path}", headers=headers)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            
            raw_attr = response.headers.get("X-Gnfd-Object-Attributes", "{}")
            try:
                return json.loads(raw_attr)
            except:
                return {}
        except Exception as e:
            logger.error(f"Error get_user_metadata {uid}: {e}")
            return None

async def update_user_metadata(uid: str, tags: dict[str, str]) -> bool:
    """PUT tagging para actualizar metadatos del usuario."""
    object_path = f"/{GREENFIELD_BUCKET}/{USERS_PREFIX}/{uid}"
    body = _build_tags_xml(tags).encode("utf-8")
    extra = {"Content-Type": "application/xml", "Content-Length": str(len(body))}
    headers = _build_signed_headers("PUT", object_path, {"tagging": ""}, extra, body)

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.put(
                f"{GREENFIELD_SP_ENDPOINT}{object_path}?tagging",
                headers=headers,
                content=body
            )
            response.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Error update_user_metadata {uid}: {e}")
            return False

async def put_object(
    object_key: str,
    body: bytes,
    content_type: str = "application/octet-stream",
    tags: Optional[dict[str, str]] = None
) -> bool:
    """Sube un objeto a Greenfield."""
    object_path = f"/{GREENFIELD_BUCKET}/{object_key}"
    extra = {"Content-Type": content_type, "Content-Length": str(len(body))}
    if tags:
        extra["X-Gnfd-Object-Attributes"] = json.dumps(tags)
    
    headers = _build_signed_headers("PUT", object_path, {}, extra, body)

    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            response = await client.put(f"{GREENFIELD_SP_ENDPOINT}{object_path}", headers=headers, content=body)
            response.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Error put_object {object_key}: {e}")
            return False

async def get_object(object_key: str) -> Optional[bytes]:
    """Descarga un objeto de Greenfield."""
    object_path = f"/{GREENFIELD_BUCKET}/{object_key}"
    headers = _build_signed_headers("GET", object_path, {}, {}, b"")

    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            response = await client.get(f"{GREENFIELD_SP_ENDPOINT}{object_path}", headers=headers)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.content
        except Exception as e:
            logger.error(f"Error get_object {object_key}: {e}")
            return None

async def list_objects(prefix: str) -> list[str]:
    """Lista objetos con un prefijo dado."""
    object_path = f"/{GREENFIELD_BUCKET}/"
    query = {"prefix": prefix, "delimiter": "/"}
    headers = _build_signed_headers("GET", object_path, query, {}, b"")

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.get(f"{GREENFIELD_SP_ENDPOINT}{object_path}", params=query, headers=headers)
            response.raise_for_status()
            # Simplificación: En un SP real parsearíamos el XML. 
            # Aquí asumimos que devuelve una lista de keys si el SP es compatible con JSON o parseamos básico.
            # Por brevedad, devolvemos las claves encontradas en el cuerpo si es texto simple o simulado.
            return [] # Implementación real requeriría xml.etree.ElementTree
        except Exception as e:
            logger.error(f"Error list_objects {prefix}: {e}")
            return []

async def upload_aporte(uid: str, content: str, metadata: dict[str, Any]) -> bool:
    """Sube un aporte de conocimiento."""
    timestamp = int(datetime.now().timestamp())
    object_key = f"{APORTES_PREFIX}/{uid}_{timestamp}.json"
    body = json.dumps({"content": content, "metadata": metadata}, ensure_ascii=False).encode("utf-8")
    return await put_object(object_key, body, content_type="application/json", tags={"author": uid, "type": "aporte"})
