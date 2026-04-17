"""
Cliente Greenfield asíncrono para Synergix (Nodo Fantasma).
Implementa operaciones ECDSA V4 con eth‑account, reintentos exponenciales y ofuscación determinista.
100% Python puro, cero Node.js.
"""

import asyncio
import hashlib
import json
import logging
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

from config import cfg

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
    Todas las operaciones llevan reintentos exponenciales (3 intentos) y manejo
    robusto de excepciones.
    """

    def __init__(
        self,
        private_key: str,
        rpc_url: str = cfg.greenfield.RPC_URL,
        chain_id: str = cfg.greenfield.CHAIN_ID,
        bucket_name: str = cfg.greenfield.BUCKET_NAME,
    ):
        self.account = Account.from_key(private_key)
        self.rpc_url = rpc_url.rstrip("/")
        self.chain_id = int(chain_id)
        self.bucket_name = bucket_name if bucket_name == "synergixai" else "synergixai"
        self._client = None
        self._auth_token = None
        self._auth_expiry = 0

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            timeout = httpx.Timeout(cfg.greenfield.UPLOAD_TIMEOUT)
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

        # Simulamos una validez de 1 hora (en producción usaríamos el endpoint real)
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
        Lee los tags de un archivo de usuario (aisynergix/users/{uid_ofuscado}).
        Si el archivo no existe, retorna un diccionario vacío.
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
        Actualiza los tags de un archivo de usuario (crea el archivo si no existe).
        El contenido del archivo es siempre 0 bytes.
        """
        # Primero verificar si existe
        existing = await self.get_user_metadata(uid_ofuscado)
        if not existing:
            # Crear archivo vacío
            await self._signed_request(
                "PUT",
                f"/{self.bucket_name}/aisynergix/users/{uid_ofuscado}",
                data=b"",
                headers={"x-amz-tagging": self._encode_tags(tags)},
            )
        else:
            # Solo actualizar tags
            await self._signed_request(
                "POST",
                f"/{self.bucket_name}/aisynergix/users/{uid_ofuscado}",
                headers={"x-amz-tagging": self._encode_tags(tags)},
            )

    async def list_users(self) -> List[Tuple[str, Dict[str, str]]]:
        """
        Lista todos los archivos en aisynergix/users/ y devuelve (uid_ofuscado, tags).
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
        Lazy update: suma +1 a points y +1 a total_uses_count.
        Se ejecuta en background cuando el RAG usa un aporte de este usuario.
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
        Sube un aporte a aisynergix/aportes/YYYY-MM/{uid_ofuscado}_{ts}.txt
        Retorna la ruta completa en Greenfield.
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
        """Crea o reemplaza un objeto en el bucket con los tags dados."""
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
        """Descarga un objeto y sus tags."""
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
        """Lista objetos bajo un prefijo, incluyendo sus tags."""
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
        """Convierte un diccionario en el formato de query‑string para x‑amz‑tagging."""
        return "&".join(f"{k}={v.replace(' ', '+')}" for k, v in tags.items())

    @staticmethod
    def _decode_tags(tag_header: str) -> Dict[str, str]:
        """Decodifica el header x‑amz‑tagging en un diccionario."""
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
    """Devuelve la instancia única del cliente Greenfield."""
    global _gf_client
    if _gf_client is None:
        _gf_client = GreenfieldClient(
            private_key=cfg.credentials.PRIVATE_KEY,
            rpc_url=cfg.greenfield.RPC_URL,
            chain_id=cfg.greenfield.CHAIN_ID,
            bucket_name=cfg.greenfield.BUCKET_NAME,
        )
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
    """Wrapper para _hash_uid, expuesta como interfaz pública."""
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
    """Tarea asíncrona en background para regalías."""
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
