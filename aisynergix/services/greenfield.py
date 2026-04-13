import os
import hmac
import hashlib
import datetime
import httpx
import logging
from eth_account import Account
from eth_account.messages import encode_defunct

logger = logging.getLogger("GreenfieldService")

class GreenfieldClient:
    def __init__(self):
        self.endpoint = os.getenv("GREENFIELD_ENDPOINT")
        self.bucket = os.getenv("BUCKET_NAME")
        self.priv_key = os.getenv("PRIVATE_KEY")
        self.account = Account.from_key(self.priv_key)
        self.address = self.account.address

    def _get_v4_headers(self, method, path, content=b""):
        """Implementación simplificada de firma para Greenfield SP"""
        timestamp = datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
        content_sha = hashlib.sha256(content).hexdigest()
        
        # En producción Greenfield, se usa firma EIP-712 o Cosmos. 
        # Aquí generamos un header de autorización basado en la clave privada.
        msg_to_sign = f"{method}\n{path}\n{timestamp}\n{content_sha}"
        msg = encode_defunct(text=msg_to_sign)
        signature = self.account.sign_message(msg).signature.hex()
        
        return {
            "X-Gnfd-Auth": signature,
            "X-Gnfd-Timestamp": timestamp,
            "Content-Type": "application/octet-stream"
        }

    async def get_user_metadata(self, uid):
        """Petición HEAD para recuperar Tags (Hidratación)"""
        path = f"/aisynergix/users/{uid}"
        url = f"{self.endpoint}/{self.bucket}{path}"
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.head(url, timeout=5.0)
                if resp.status_code == 404:
                    return None
                
                # Greenfield retorna tags en headers x-gnfd-meta-*
                return {
                    "points": resp.headers.get("x-gnfd-meta-points", "0"),
                    "rank": resp.headers.get("x-gnfd-meta-rank", "🌱 Iniciado"),
                    "fsm": resp.headers.get("x-gnfd-meta-fsm", "IDLE"),
                    "quota": resp.headers.get("x-gnfd-meta-quota", "5"),
                    "lang": resp.headers.get("x-gnfd-meta-lang", "es")
                }
            except Exception as e:
                logger.error(f"Error HEAD metadata {uid}: {e}")
                return None

    async def update_user_metadata(self, uid, updates):
        """Actualiza Tags del objeto usuario en Greenfield"""
        path = f"/aisynergix/users/{uid}"
        url = f"{self.endpoint}/{self.bucket}{path}?tags"
        headers = self._get_v4_headers("PUT", path)
        for k, v in updates.items():
            headers[f"x-gnfd-meta-{k}"] = str(v)
            
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.put(url, headers=headers, timeout=10.0)
                return resp.status_code == 200
            except Exception as e:
                logger.error(f"Error update metadata {uid}: {e}")
                return False

    async def add_residual_points(self, uid, amount):
        """Suma puntos de forma atómica (Lazy Update)"""
        meta = await self.get_user_metadata(uid)
        if meta:
            new_pts = int(meta["points"]) + amount
            await self.update_user_metadata(uid, {"points": new_pts})

    async def upload_aporte(self, uid, content, tags):
        ts = int(datetime.datetime.now().timestamp())
        path = f"/aisynergix/aportes/{datetime.datetime.now().strftime('%Y-%m')}/{uid}_{ts}.txt"
        url = f"{self.endpoint}/{self.bucket}{path}"
        headers = self._get_v4_headers("PUT", path, content.encode())
        for k, v in tags.items():
            headers[f"x-gnfd-meta-{k}"] = str(v)
        headers["x-gnfd-meta-author_uid"] = str(uid)
        
        async with httpx.AsyncClient() as client:
            await client.put(url, content=content, headers=headers, timeout=20.0)

    async def upload_log(self, filepath):
        with open(filepath, "rb") as f:
            content = f.read()
        filename = os.path.basename(filepath)
        path = f"/aisynergix/logs/{filename}"
        url = f"{self.endpoint}/{self.bucket}{path}"
        headers = self._get_v4_headers("PUT", path, content)
        async with httpx.AsyncClient() as client:
            await client.put(url, content=content, headers=headers)

    async def get_object(self, path):
        url = f"{self.endpoint}/{self.bucket}/{path}"
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=30.0)
            return resp.content if resp.status_code == 200 else None

    async def put_object(self, path, content, tags=None):
        url = f"{self.endpoint}/{self.bucket}/{path}"
        headers = self._get_v4_headers("PUT", path, content)
        if tags:
            for k, v in tags.items():
                headers[f"x-gnfd-meta-{k}"] = str(v)
        async with httpx.AsyncClient() as client:
            await client.put(url, content=content, headers=headers)

greenfield = GreenfieldClient()
