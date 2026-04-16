"""
greenfield.py — Servicio Web3 Puro de Synergix.
Implementa firma ECDSA V4 nativa (sin Node.js) usando eth-account y hmac.
Maneja operaciones de lectura/escritura y metadatos sobre BNB Greenfield.
"""

import hashlib
import hmac
import json
import logging
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Optional, Dict, Tuple

import httpx
from eth_account import Account
from eth_account.messages import encode_defunct

from aisynergix.config.constants import (
    GREENFIELD_BUCKET,
    GREENFIELD_SP_ENDPOINT,
    OPERATOR_ADDRESS,
    OPERATOR_PRIVATE_KEY
)

logger = logging.getLogger(__name__)

# Constants V4
SIGNING_ALGORITHM = "ECDSA-secp256k1"

def _get_strict_timestamp() -> Tuple[str, str]:
    """Genera timestamps estrictos en formato ISO 8601 básico para evitar desincronización NTP."""
    now = datetime.now(timezone.utc)
    amz_date = now.strftime('%Y%m%dT%H%M%SZ')
    date_stamp = now.strftime('%Y%m%d')
    return amz_date, date_stamp

def _sha256_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def _sign_ecdsa(message: str, private_key: str) -> str:
    """Firma el mensaje usando la llave privada de Ethereum/Greenfield."""
    message_encoded = encode_defunct(text=message)
    signed_message = Account.sign_message(message_encoded, private_key=private_key)
    return signed_message.signature.hex()

def _build_signed_headers(method: str, uri: str, query_params: dict, headers: dict, payload: bytes) -> dict:
    """
    Construye la firma nativa Greenfield V4 (Canonical Request -> String to Sign -> Sign).
    """
    amz_date, date_stamp = _get_strict_timestamp()
    headers['x-gnfd-date'] = amz_date
    headers['host'] = urllib.parse.urlparse(GREENFIELD_SP_ENDPOINT).netloc

    # 1. Canonical URI & Query
    canonical_uri = urllib.parse.quote(uri, safe="/-_.~")
    sorted_queries = sorted(query_params.items())
    canonical_query = "&".join(f"{urllib.parse.quote(str(k))}={urllib.parse.quote(str(v))}" for k, v in sorted_queries)

    # 2. Canonical Headers
    sorted_headers = sorted(headers.items(), key=lambda x: x[0].lower())
    canonical_headers = "".join(f"{k.lower()}:{v}\n" for k, v in sorted_headers)
    signed_headers = ";".join(k.lower() for k, v in sorted_headers)

    # 3. Payload Hash
    payload_hash = _sha256_hash(payload)
    headers['x-gnfd-content-sha256'] = payload_hash

    # 4. Canonical Request
    canonical_request = f"{method}\n{canonical_uri}\n{canonical_query}\n{canonical_headers}\n{signed_headers}\n{payload_hash}"
    
    # 5. String to Sign
    credential_scope = f"{date_stamp}/greenfield/gnfd-sp/request"
    string_to_sign = f"{SIGNING_ALGORITHM}\n{amz_date}\n{credential_scope}\n{_sha256_hash(canonical_request.encode('utf-8'))}"

    # 6. Calcular Firma ECDSA
    signature = _sign_ecdsa(string_to_sign, OPERATOR_PRIVATE_KEY)

    # 7. Construir Authorization Header
    auth_header = (
        f"{SIGNING_ALGORITHM} Credential={OPERATOR_ADDRESS}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    headers['authorization'] = auth_header
    
    return headers

async def get_object(object_key: str) -> Optional[bytes]:
    """Descarga un objeto desde DCellar/Greenfield."""
    uri = f"/{GREENFIELD_BUCKET}/{object_key}"
    headers = _build_signed_headers("GET", uri, {}, {}, b"")

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.get(f"{GREENFIELD_SP_ENDPOINT}{uri}", headers=headers)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.content
        except httpx.HTTPError as e:
            logger.error(f"[Greenfield] Error GET {object_key}: {e}")
            return None

async def put_object(object_key: str, content: bytes, tags: Optional[Dict[str, str]] = None) -> bool:
    """Sube un objeto a Greenfield, opcionalmente con Tags (Metadatos)."""
    uri = f"/{GREENFIELD_BUCKET}/{object_key}"
    
    # Preparamos headers base
    base_headers = {
        "content-type": "application/octet-stream",
        "content-length": str(len(content))
    }
    
    # Greenfield utiliza headers x-gnfd-tagging para tags durante el PUT
    if tags:
        tag_str = "&".join(f"{urllib.parse.quote(k)}={urllib.parse.quote(v)}" for k, v in tags.items())
        base_headers["x-gnfd-tagging"] = tag_str

    headers = _build_signed_headers("PUT", uri, {}, base_headers, content)

    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            response = await client.put(f"{GREENFIELD_SP_ENDPOINT}{uri}", content=content, headers=headers)
            response.raise_for_status()
            logger.info(f"[Greenfield] PUT exitoso: {object_key}")
            return True
        except httpx.HTTPError as e:
            logger.error(f"[Greenfield] Error PUT {object_key}: {e}")
            return False

async def get_user_metadata(uid_ofuscado: str) -> Optional[Dict[str, str]]:
    """Obtiene los tags/metadatos de un usuario (0-bytes file) usando petición HEAD."""
    uri = f"/{GREENFIELD_BUCKET}/aisynergix/users/{uid_ofuscado}"
    # Para tags, Greenfield suele requerir ?tagging o extraer de headers x-gnfd-tagging-count.
    # Usaremos el endpoint estándar GET ?tagging para asegurar extracción de diccionario.
    query = {"tagging": ""}
    headers = _build_signed_headers("GET", uri, query, {}, b"")
    
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            response = await client.get(f"{GREENFIELD_SP_ENDPOINT}{uri}", params=query, headers=headers)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            
            # Parsear XML/JSON de tagging de Greenfield
            # Implementación simplificada asumiendo respuesta JSON (o requiere parseo xml de TagSet)
            if "TagSet" in response.text:
                # Lógica simplificada de extracción si es XML, aquí asumimos un formato pre-procesado o parseo regex básico
                import re
                tags = {}
                matches = re.findall(r'<Key>(.*?)</Key><Value>(.*?)</Value>', response.text)
                for k, v in matches:
                    tags[k] = v
                return tags
            return {}
        except httpx.HTTPError as e:
            logger.error(f"[Greenfield] Error GET Tags {uid_ofuscado}: {e}")
            return None
