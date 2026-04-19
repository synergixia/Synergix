"""
Cliente Greenfield asíncrono para Synergix (Nodo Fantasma).
Implementa operaciones ECDSA V4 con eth‑account, reintentos exponenciales y ofuscación determinista.
100% Python puro, cero Node.js. Apunta 100% a BNB Greenfield MAINNET.
"""

import asyncio
import hashlib
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx
from eth_account import Account
from eth_account.messages import encode_defunct
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)
from tenacity.stop import stop_base

logger = logging.getLogger("synergix.greenfield")

# ──────────────────────────────────────────────────────────────────────────────
# OFUSCACIÓN DETERMINISTA
# ──────────────────────────────────────────────────────────────────────────────

SALT_UID = "Synergix_"

def _hash_uid(uid: int) -> str:
    """
    SHA‑256 truncado a 12 caracteres, determinista.
    Se usa para TODAS las rutas y tags; el UID real nunca sale del servidor.
    """
    raw = f"{SALT_UID}{uid}".encode()
    return hashlib.sha256(raw).hexdigest()[:12]

# ──────────────────────────────────────────────────────────────────────────────
# CLIENTE GREENFIELD (ECDSA V4)
# ──────────────────────────────────────────────────────────────────────────────

class GreenfieldClient:
    """
    Cliente asíncrono para BNB Greenfield (mainnet DCellar).
    Todas las operaciones llevan reintentos exponenciales (3 intentos) y manejo robusto de excepciones.
    """
    def __init__(
        self,
        private_key: str,
        rpc_url: Optional[str] = None,
        chain_id: Optional[str] = None,
        bucket_name: Optional[str] = None,
    ):
        self.account = Account.from_key(private_key)
        
        # Apuntando directo a Mainnet mediante variables de entorno u omisión
        self.rpc_url = (rpc_url or os.getenv("GREENFIELD_RPC_URL", "https://greenfield-chain.bnbchain.org:443")).rstrip("/")
        self.chain_id = int(chain_id or os.getenv("GREENFIELD_CHAIN_ID", "1017"))
        self.bucket_name = bucket_name or os.getenv("BUCKET_NAME", "synergixai")
        
        self._client = None
        self._auth_token = None
        self._auth_expiry = 0

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            timeout = httpx.Timeout(float(os.getenv("UPLOAD_TIMEOUT", "30.0")))
            self._client = httpx.AsyncClient(
                base_url=self.rpc_url,
                timeout=timeout,
                headers={"User-Agent": "Synergix-NodoFantasma/1.0"},
            )
        return self._client

    async def _acquire_auth_token(self) -> str:
        """Obtiene un token de autenticación firmado (ECDSA V4)."""
        now = int(time.time())
        if self._auth_token and now < self._auth_expiry - 60:
            return self._auth_token

        message = f"Synergix auth {now}"
        signable = encode_defunct(text=message)
        signed = self.account.sign_message(signable)
        token = f"{self.account.address}:{signed.signature.hex()}"

        # Simulamos una validez de 1 hora
        self._auth_token = token
        self._auth_expiry = now + 3600
        return token

    async def _signed_request(
        self,
        method: str,
        path: str,
        params: Optional[Dict] = None,
        json_data: Optional[Dict] = None,
        data: Optional[bytes] = None,
        headers: Optional[Dict] = None,
    ) -> httpx.Response:
        """
        Realiza una solicitud HTTP firmada con reintentos exponenciales.
        Usa el esquema de autenticación personalizado de Greenfield.
        """
        client = await self._ensure_client()
        auth_token = await self._acquire_auth_token()
        default_headers = {
            "Authorization": f"Greenfield {auth_token}",
            "Content-Type": "application/json",
        }
        if headers:
            default_headers.update(headers)
        if data:
            default_headers["Content-Type"] = "application/octet-stream"

        retryer = AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=1, max=10),
            retry=retry_if_exception_type(
                (httpx.NetworkError, httpx.TimeoutException, httpx.HTTPStatusError)
            ),
            reraise=True,
        )

        async for attempt in retryer:
            with attempt:
                response = await client.request(
                    method=method,
                    url=path,
                    params=params,
                    json=json_data if json_data else None,
                    content=data if data else None,
                    headers=default_headers,
                )
                response.raise_for_status()
                return response
        raise RuntimeError("Unreachable")

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    # ──────────────────────────────────────────────────────────────────────────
    # OPERACIONES DE USUARIO (archivos de 0 bytes con tags)
    # ──────────────────────────────────────────────────────────────────────────

    async def get_user_metadata(self, uid_ofuscado: str) -> Dict[str, Any]:
        """
        Lee los tags de un archivo de usuario.
        """
        try:
            resp = await self._signed_request(
                "GET",
                f"/{self.bucket_name}/aisynergix/users/{uid_ofuscado}",
                params={"tags": "true"},
            )
            data = resp.json()
            return data.get("tags", {})
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return {}
            raise

    async def update_user_metadata(
        self, uid_ofuscado: str, tags: Dict[str, str]
    ) -> None:
        """
        Actualiza los tags de un archivo de usuario.
        """
        existing = await self.get_user_metadata(uid_ofuscado)
        if not existing:
            await self._signed_request(
                "PUT",
                f"/{self.bucket_name}/aisynergix/users/{uid_ofuscado}",
                data=b"",
                headers={"x-amz-tagging": self._encode_tags(tags)},
            )
        else:
            await self._signed_request(
                "POST",
                f"/{self.bucket_name}/aisynergix/users/{uid_ofuscado}",
                headers={"x-amz-tagging": self._encode_tags(tags)},
            )

    async def list_users(self) -> List[Tuple[str, Dict[str, str]]]:
        """
        Lista todos los archivos de usuarios.
        """
        resp = await self._signed_request(
            "GET",
            f"/{self.bucket_name}/aisynergix/users/",
            params={"list": "true", "tags": "true"},
        )
        items = resp.json().get("objects", [])
        result = []
        for item in items:
            uid = item["key"].split("/")[-1]
            tags = item.get("tags", {})
            result.append((uid, tags))
        return result

    async def add_residual_points(self, uid_ofuscado: str) -> None:
        """
        Lazy update: suma +1 a points y total_uses_count.
        """
        tags = await self.get_user_metadata(uid_ofuscado)
        if not tags:
            return
        points = int(tags.get("points", "0"))
        total_uses = int(tags.get("total_uses_count", "0"))
        tags["points"] = str(points + 1)
        tags["total_uses_count"] = str(total_uses + 1)
        tags["last_seen_ts"] = str(int(time.time()))
        await self.update_user_metadata(uid_ofuscado, tags)

    # ──────────────────────────────────────────────────────────────────────────
    # APORTES (archivos de texto con tags)
    # ──────────────────────────────────────────────────────────────────────────

    async def upload_aporte(
        self,
        uid_ofuscado: str,
        content: str,
        quality_score: int,
        category: str,
        impact_index: float,
        lang: str,
    ) -> str:
        """
        Sube un aporte a Greenfield.
        """
        timestamp = int(time.time())
        date_prefix = datetime.now(timezone.utc).strftime("%Y-%m")
        object_name = (
            f"aisynergix/aportes/{date_prefix}/{uid_ofuscado}_{timestamp}.txt"
        )
        tags = {
            "quality_score": str(quality_score),
            "category": category,
            "impact_index": str(impact_index),
            "author_uid": uid_ofuscado,
            "lang": lang,
        }
        await self.put_object(object_name, content.encode("utf-8"), tags)
        return object_name

    async def put_object(
        self, object_name: str, data: bytes, tags: Optional[Dict[str, str]] = None
    ) -> None:
        headers = {}
        if tags:
            headers["x-amz-tagging"] = self._encode_tags(tags)
        await self._signed_request(
            "PUT",
            f"/{self.bucket_name}/{object_name}",
            data=data,
            headers=headers,
        )

    async def get_object(self, object_name: str) -> Tuple[bytes, Dict[str, str]]:
        resp = await self._signed_request(
            "GET",
            f"/{self.bucket_name}/{object_name}",
            params={"tags": "true"},
        )
        tags = self._decode_tags(resp.headers.get("x-amz-tagging", ""))
        return resp.content, tags

    async def list_objects(
        self, prefix: str, limit: int = 1000
    ) -> List[Tuple[str, Dict[str, str]]]:
        resp = await self._signed_request(
            "GET",
            f"/{self.bucket_name}/{prefix}",
            params={"list": "true", "tags": "true", "max-keys": str(limit)},
        )
        items = resp.json().get("objects", [])
        return [(item["key"], item.get("tags", {})) for item in items]

    # ──────────────────────────────────────────────────────────────────────────
    # HELPERS
    # ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _encode_tags(tags: Dict[str, str]) -> str:
        return "&".join(f"{k}={v.replace(' ', '+')}" for k, v in tags.items())

    @staticmethod
    def _decode_tags(tag_header: str) -> Dict[str, str]:
        if not tag_header:
            return {}
        result = {}
        for pair in tag_header.split("&"):
            if "=" in pair:
                k, v = pair.split("=", 1)
                result[k] = v.replace("+", " ")
        return result

# ──────────────────────────────────────────────────────────────────────────────
# INSTANCIA GLOBAL (singleton asíncrono)
# ──────────────────────────────────────────────────────────────────────────────

_gf_client: Optional[GreenfieldClient] = None

async def get_greenfield_client() -> GreenfieldClient:
    """Devuelve la instancia única del cliente Greenfield usando Mainnet y variables nativas."""
    global _gf_client
    if _gf_client is None:
        private_key = os.getenv("GREENFIELD_PRIVATE_KEY")
        if not private_key:
            raise ValueError("Falta GREENFIELD_PRIVATE_KEY en las variables de entorno.")
        
        _gf_client = GreenfieldClient(private_key=private_key)
    return _gf_client

async def close_greenfield_client() -> None:
    """Cierra el cliente Greenfield (llamar al apagado)."""
    global _gf_client
    if _gf_client:
        await _gf_client.close()
        _gf_client = None

# ──────────────────────────────────────────────────────────────────────────────
# FUNCIONES DE CONVENIENCIA (para uso desde otros módulos)
# ──────────────────────────────────────────────────────────────────────────────

async def hash_uid(uid: int) -> str:
    return _hash_uid(uid)

async def get_user_metadata(uid_ofuscado: str) -> Dict[str, Any]:
    client = await get_greenfield_client()
    return await client.get_user_metadata(uid_ofuscado)

async def update_user_metadata(uid_ofuscado: str, tags: Dict[str, str]) -> None:
    client = await get_greenfield_client()
    await client.update_user_metadata(uid_ofuscado, tags)

async def list_users() -> List[Tuple[str, Dict[str, str]]]:
    client = await get_greenfield_client()
    return await client.list_users()

async def add_residual_points(uid_ofuscado: str) -> None:
    client = await get_greenfield_client()
    await client.add_residual_points(uid_ofuscado)

async def upload_aporte(
    uid_ofuscado: str,
    content: str,
    quality_score: int,
    category: str,
    impact_index: float,
    lang: str,
) -> str:
    client = await get_greenfield_client()
    return await client.upload_aporte(
        uid_ofuscado, content, quality_score, category, impact_index, lang
    )

async def put_object(
    object_name: str, data: bytes, tags: Optional[Dict[str, str]] = None
) -> None:
    client = await get_greenfield_client()
    await client.put_object(object_name, data, tags)

async def get_object(object_name: str) -> Tuple[bytes, Dict[str, str]]:
    client = await get_greenfield_client()
    return await client.get_object(object_name)

async def list_objects(prefix: str, limit: int = 1000) -> List[Tuple[str, Dict[str, str]]]:
    client = await get_greenfield_client()
    return await client.list_objects(prefix, limit)
