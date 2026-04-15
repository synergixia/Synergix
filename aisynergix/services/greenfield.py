### `aisynergix/services/greenfield.py` — Código completo

```python
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
from typing import Any, Optional

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
    """
    Construye el Canonical Request y devuelve (canonical_request, signed_headers_str).

    Args:
        method:       Método HTTP en mayúsculas (GET, PUT, HEAD, DELETE).
        path:         Ruta del recurso, ej: /synergixai/aisynergix/users/123.
        query_params: Diccionario de parámetros de query string.
        headers:      Diccionario de headers HTTP a incluir en la firma.
        body:         Cuerpo de la petición en bytes.

    Returns:
        Tupla (canonical_request_string, signed_headers_string).
    """
    # Paso 1: Canonical URI — path URL-encoded preservando las barras "/"
    canonical_uri = urllib.parse.quote(path, safe="/")

    # Paso 2: Canonical Query String — parámetros ordenados alfabéticamente
    sorted_query = sorted(query_params.items())
    canonical_query = "&".join(
        f"{urllib.parse.quote(k, safe='')}={urllib.parse.quote(v, safe='')}"
        for k, v in sorted_query
    )

    # Paso 3: Canonical Headers — en minúsculas, ordenados, valor trimmed
    sorted_headers = sorted((k.lower(), v.strip()) for k, v in headers.items())
    canonical_headers = "".join(f"{k}:{v}\n" for k, v in sorted_headers)

    # Paso 4: Signed Headers — solo los nombres de los headers, separados por ";"
    signed_headers_str = ";".join(k for k, _ in sorted_headers)

    # Paso 5: Payload Hash — SHA-256 del body (body vacío → hash vacío conocido)
    payload_hash = _sha256_hex(body)

    # Paso 6: Canonical Request — concatenación final
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
    """
    Genera el header 'Authorization' usando firma ECDSA V4 de Greenfield.

    Args:
        method:       Método HTTP.
        path:         Ruta del objeto en el SP.
        query_params: Parámetros de query.
        headers:      Headers de la petición (deben incluir 'x-gnfd-date').
        body:         Cuerpo de la petición.
        timestamp:    Timestamp ISO8601 en formato Greenfield (ej: 20240101T120000Z).

    Returns:
        String completo del header Authorization.

    Raises:
        ValueError: Si OPERATOR_PRIVATE_KEY no está configurada.
    """
    if not OPERATOR_PRIVATE_KEY:
        raise ValueError(
            "OPERATOR_PRIVATE_KEY no está configurada en las variables de entorno."
        )

    # Paso 6: Construir el Canonical Request
    canonical_request, signed_headers_str = _build_canonical_request(
        method, path, query_params, headers, body
    )

    # Paso 7: String to Sign
    # Formato: GNFD1-ECDSA\n{timestamp}\n{sha256_hex(canonical_request)}
    canonical_request_hash = _sha256_hex(canonical_request.encode("utf-8"))
    string_to_sign = f"{SIGNING_ALGORITHM}\n{timestamp}\n{canonical_request_hash}"

    # Paso 8: Firma ECDSA usando eth-account
    # encode_defunct hashea el mensaje con el prefijo Ethereum standard (\x19Ethereum...)
    # Greenfield acepta este formato para autenticación de SP.
    message = encode_defunct(text=string_to_sign)
    signed = Account.sign_message(message, private_key=OPERATOR_PRIVATE_KEY)
    signature_hex = signed.signature.hex()

    # Paso 9: Construcción del header Authorization
    authorization = (
        f"{SIGNING_ALGORITHM} "
        f"Credential={OPERATOR_ADDRESS},"
        f"SignedMsg={canonical_request_hash},"
        f"Signature={signature_hex}"
    )

    return authorization


def _get_timestamp() -> str:
    """
    Devuelve el timestamp actual en formato ISO8601 compacto requerido por Greenfield.
    Ejemplo: '20240715T183045Z'
    """
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _build_signed_headers(                                                                               method: str,
    path: str,
    query_params: dict[str, str],
    extra_headers: dict[str, str],
    body: bytes,
) -> dict[str, str]:
    """
    Construye el diccionario completo de headers HTTP con la firma incluida.

    Args:
        method:        Método HTTP.
        path:          Ruta del recurso.
        query_params:  Parámetros de query.
        extra_headers: Headers adicionales específicos del request (ej: Content-Type, tags).                 body:          Cuerpo de la petición.

    Returns:                                                                                                 Diccionario de headers listo para pasar a httpx.
    """
    timestamp = _get_timestamp()
    host = GREENFIELD_SP_ENDPOINT.replace("https://", "").replace("http://", "")                     
    # Headers base que siempre se firman
    base_headers: dict[str, str] = {                                                                         "host": host,
        "x-gnfd-date": timestamp,
        **{k.lower(): v for k, v in extra_headers.items()},
    }
                                                                                                         authorization = _build_authorization_header(
        method, path, query_params, base_headers, body, timestamp
    )

    # Retornamos los headers finales para httpx (con capitalización normal)
    final_headers: dict[str, str] = {
        "Host": host,
        "X-Gnfd-Date": timestamp,                                                                            "Authorization": authorization,
        **extra_headers,
    }                                                                                                
    return final_headers
                                                                                                     
# ─────────────────────────────────────────────────────────────────────────────
# CLIENTE GREENFIELD — API PÚBLICA                                                                   # ─────────────────────────────────────────────────────────────────────────────


async def get_user_metadata(uid: str) -> Optional[dict[str, str]]:                                       """
    Obtiene los metadatos (Tags) de un archivo de usuario en Greenfield.

    Realiza un HEAD request al objeto aisynergix/users/{uid}.                                            Si el objeto no existe (404), retorna None (usuario nuevo).
    Si existe, retorna un dict con los tags del objeto.
                                                                                                         Args:
        uid: ID único del usuario de Telegram (como string).

    Returns:                                                                                                 dict con los tags del usuario, o None si no existe.

    Raises:
        httpx.HTTPStatusError: Para errores inesperados del SP (no 404).                                 """
    object_path = f"/{GREENFIELD_BUCKET}/{USERS_PREFIX}/{uid}"
    query_params: dict[str, str] = {}                                                                    body = b""

    headers = _build_signed_headers("HEAD", object_path, query_params, {}, body)

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.head(
                f"{GREENFIELD_SP_ENDPOINT}{object_path}",                                                            headers=headers,
            )

            if response.status_code == 404:
                logger.info(f"[Greenfield] Usuario {uid} no existe (404). Nuevo usuario.")
                return None

            response.raise_for_status()

            # Los tags de Greenfield vienen en el header 'X-Gnfd-Object-Attributes'
            # como un string JSON o en 'X-Gnfd-Metadata'.                                                        # Intentamos parsear desde el header de atributos.
            raw_tags = response.headers.get("X-Gnfd-Object-Attributes", "{}")
            try:
                tags = json.loads(raw_tags)
            except json.JSONDecodeError:
                # Fallback: parsear tags como "key=value,key2=value2"
                tags = {}
                for pair in raw_tags.split(","):
                    if "=" in pair:
                        k, v = pair.split("=", 1)
                        tags[k.strip()] = v.strip()                                                  
            logger.debug(f"[Greenfield] Metadata de {uid}: {tags}")
            return tags
                                                                                                             except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None                                                                                      logger.error(f"[Greenfield] Error HTTP obteniendo metadata de {uid}: {e}")
            raise

                                                                                                     async def update_user_metadata(uid: str, tags: dict[str, str]) -> bool:
    """
    Actualiza los Tags (metadatos) del archivo de usuario en Greenfield.
                                                                                                         En Greenfield, los tags de un objeto se actualizan mediante una petición
    PUT al endpoint de metadatos del objeto.

    Args:
        uid:  ID del usuario.
        tags: Diccionario con los tags a establecer.
                                                                                                         Returns:
        True si la actualización fue exitosa, False en caso de error.
    """
    object_path = f"/{GREENFIELD_BUCKET}/{USERS_PREFIX}/{uid}"

    # Greenfield recibe los tags como XML o JSON en el query param "tagging"
    tags_xml = _build_tags_xml(tags)
    body = tags_xml.encode("utf-8")                                                                  
    extra_headers = {
        "Content-Type": "application/xml",
        "Content-Length": str(len(body)),                                                                }

    headers = _build_signed_headers(                                                                         "PUT", object_path, {"tagging": ""}, extra_headers, body
    )

    async with httpx.AsyncClient(timeout=30.0) as client:                                                    try:
            response = await client.put(
                f"{GREENFIELD_SP_ENDPOINT}{object_path}?tagging",
                headers=headers,                                                                                     content=body,
            )
            response.raise_for_status()
            logger.info(f"[Greenfield] Tags de {uid} actualizados: {tags}")
            return True

        except httpx.HTTPStatusError as e:
            logger.error(f"[Greenfield] Error actualizando metadata de {uid}: {e}")                              return False


async def put_object(
    object_key: str,
    body: bytes,
    content_type: str = "application/octet-stream",
    tags: Optional[dict[str, str]] = None,
) -> bool:
    """                                                                                                  Sube un objeto arbitrario a BNB Greenfield.
                                                                                                         Args:
        object_key:   Clave completa del objeto (sin el bucket), ej: "aisynergix/users/123".
        body:         Contenido del objeto en bytes.
        content_type: MIME type del contenido.                                                               tags:         Tags opcionales a asociar al objeto.
                                                                                                         Returns:
        True si la subida fue exitosa.                                                                   """
    object_path = f"/{GREENFIELD_BUCKET}/{object_key}"                                                   query_params: dict[str, str] = {}
                                                                                                         extra_headers: dict[str, str] = {
        "Content-Type": content_type,                                                                        "Content-Length": str(len(body)),
    }                                                                                                
    # Si hay tags, los enviamos en el header X-Gnfd-Object-Attributes                                    if tags:
        extra_headers["X-Gnfd-Object-Attributes"] = json.dumps(tags)                                 
    headers = _build_signed_headers("PUT", object_path, query_params, extra_headers, body)           
    async with httpx.AsyncClient(timeout=60.0) as client:                                                    try:
            response = await client.put(                                                                             f"{GREENFIELD_SP_ENDPOINT}{object_path}",
                headers=headers,
                content=body,
            )
            response.raise_for_status()
            logger.info(f"[Greenfield] Objeto subido: {object_key} ({len(body)} bytes)")
            return True

        except httpx.HTTPStatusError as e:
            logger.error(f"[Greenfield] Error subiendo objeto {object_key}: {e}")                                return False
                                                                                                     
async def get_object(object_key: str) -> Optional[bytes]:                                                """
    Descarga el contenido de un objeto de BNB Greenfield.                                            
    Args:                                                                                                    object_key: Clave completa del objeto.
                                                                                                         Returns:
        Contenido del objeto en bytes, o None si no existe.                                              """
    object_path = f"/{GREENFIELD_BUCKET}/{object_key}"                                                   query_params: dict[str, str]
