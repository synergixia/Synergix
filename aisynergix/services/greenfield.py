import asyncio
import hmac
import hashlib
import time
import json
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime
from urllib.parse import urlparse, quote

import httpx
from eth_account import Account
from eth_account.messages import encode_defunct

# Configuración de Logs
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Synergix.Greenfield")

class GreenfieldClient:
    """
    Cliente Soberano para BNB Greenfield (Nodo Fantasma).
    Implementa firmas ECDSA V4 para operaciones Stateless.
    """
    def __init__(self, endpoint: str, bucket_name: str, private_key: str):
        self.endpoint = endpoint.rstrip("/")
        self.bucket_name = bucket_name
        self.private_key = private_key
        self.account = Account.from_key(private_key)
        self.address = self.account.address
        self.timeout = httpx.Timeout(15.0, connect=5.0)

    def _get_signature_v4(self, method: str, path: str, headers: Dict[str, str], payload: bytes = b"") -> str:
        """
        Genera la firma V4 compatible con BNB Greenfield.
        Nota: Greenfield usa un esquema de firma basado en el mensaje de Ethereum.
        """
        # Simplificación para el entorno Ghost Node: Firma del hash de la petición
        timestamp = headers.get("x-gnfd-expiry-timestamp", str(int(time.time() + 3600)))
        content_hash = hashlib.sha256(payload).hexdigest()
        
        canonical_string = f"{method}\n{path}\n{timestamp}\n{content_hash}"
        message = encode_defunct(text=canonical_string)
        signed_message = Account.sign_message(message, private_key=self.private_key)
        return signed_message.signature.hex()

    async def _request(self, method: str, object_key: str, params: Dict = None, headers: Dict = None, content: bytes = b"") -> httpx.Response:
        path = f"/{self.bucket_name}/{object_key.lstrip('/')}"
        url = f"{self.endpoint}{path}"
        
        if headers is None:
            headers = {}
        
        # Headers obligatorios para Greenfield
        timestamp = str(int(time.time() + 3600))
        headers.update({
            "x-gnfd-expiry-timestamp": timestamp,
            "x-gnfd-request-date": datetime.utcnow().strftime("%Y%m%dT%H%M%SZ"),
            "Host": urlparse(self.endpoint).netloc
        })
        
        signature = self._get_signature_v4(method, path, headers, content)
        headers["Authorization"] = f"GNFD-V4-HMAC-SHA256 Signature={signature}"

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.request(method, url, params=params, headers=headers, content=content)
                return response
            except Exception as e:
                logger.error(f"Error en petición Greenfield: {e}")
                raise

    async def get_user_metadata(self, uid: int) -> Optional[Dict[str, str]]:
        """Recupera los Tags del usuario vía HEAD request."""
        object_key = f"aisynergix/usuarios/{uid}"
        response = await self._request("HEAD", object_key)
        
        if response.status_code == 404:
            return None
        
        # Greenfield devuelve los tags en headers con prefijo x-gnfd-tag-
        tags = {}
        for k, v in response.headers.items():
            if k.lower().startswith("x-gnfd-tag-"):
                tags[k[11:].lower()] = v
        return tags

    async def update_user_metadata(self, uid: int, updates: Dict[str, str]):
        """Actualiza Tags de forma atómica."""
        object_key = f"aisynergix/usuarios/{uid}"
        headers = {}
        for k, v in updates.items():
            headers[f"x-gnfd-tag-{k}"] = str(v)
        
        # Operación especial de actualización de metadatos
        response = await self._request("PUT", object_key, params={"metadata": ""}, headers=headers)
        if response.status_code not in (200, 204):
            logger.error(f"Error actualizando metadata para {uid}: {response.text}")
            return False
        return True

    async def add_residual_points(self, uid: int, amount: int):
        """Lazy Update: Suma puntos pasivos sin bloquear el flujo principal."""
        try:
            current = await self.get_user_metadata(uid)
            if current:
                puntos = int(current.get("puntos", 0)) + amount
                await self.update_user_metadata(uid, {"puntos": str(puntos)})
                logger.info(f"Puntos residuales (+{amount}) otorgados a {uid}")
        except Exception as e:
            logger.warning(f"Fallo en Lazy Update de puntos para {uid}: {e}")

    async def upload_aporte(self, uid: int, content: str, tags: Dict[str, str]):
        """Sube un nuevo fragmento de conocimiento."""
        ts = int(time.time())
        month = datetime.utcnow().strftime("%Y-%m")
        object_key = f"aisynergix/aportes/{month}/{uid}_{ts}.txt"
        
        headers = {f"x-gnfd-tag-{k}": str(v) for k, v in tags.items()}
        headers["x-gnfd-tag-author_uid"] = str(uid)
        
        response = await self._request("PUT", object_key, headers=headers, content=content.encode("utf-8"))
        return response.status_code in (200, 201)

    async def upload_log(self, filepath: str):
        """Sube el log comprimido a DCellar."""
        filename = filepath.split("/")[-1]
        object_key = f"aisynergix/logs/{filename}"
        
        with open(filepath, "rb") as f:
            content = f.read()
            
        response = await self._request("PUT", object_key, content=content)
        return response.status_code in (200, 201)

    async def get_object(self, path: str) -> Optional[bytes]:
        """Descarga un objeto (ej: el cerebro o config)."""
        response = await self._request("GET", path)
        if response.status_code == 200:
            return response.content
        return None

    async def put_object(self, path: str, content: bytes, tags: Dict[str, str] = None):
        """Sube un objeto arbitrario."""
        headers = {}
        if tags:
            headers = {f"x-gnfd-tag-{k}": str(v) for k, v in tags.items()}
        
        response = await self._request("PUT", path, headers=headers, content=content)
        return response.status_code in (200, 201, 204)
