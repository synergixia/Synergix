import os
import json
import hashlib
import asyncio
import gzip
import hmac
import time
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Tuple
from urllib.parse import urlparse

import httpx
from eth_account import Account
from eth_account.messages import encode_defunct
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from hexbytes import HexBytes


GREENFIELD_RPC_URL = os.getenv("GREENFIELD_RPC_URL", "https://greenfield-chain.bnbchain.world:443")
GREENFIELD_CHAIN_ID = int(os.getenv("GREENFIELD_CHAIN_ID", "1017"))
BUCKET_NAME = os.getenv("BUCKET_NAME", "synergix-v1")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")

if not PRIVATE_KEY:
    raise EnvironmentError("PRIVATE_KEY no configurada en el entorno.")

ACCOUNT = Account.from_key(PRIVATE_KEY)
ADDRESS = ACCOUNT.address

SP_ENDPOINT = os.getenv("SP_ENDPOINT", "https://greenfield-sp.bnbchain.world")

DELEGATE_PREFIX = "/greenfield/admin/v1"

CACHE_TTL = int(os.getenv("CACHE_TTL", "12"))
DEFAULT_HEADERS = {"Content-Type": "application/json"}
TIMEOUT = httpx.Timeout(30.0, connect=15.0)

SALT_UID = "Synergix_"
BUCKET_BASE = "aisynergix"
USERS_PREFIX = f"{BUCKET_BASE}/users"
APORTES_PREFIX = f"{BUCKET_BASE}/aportes"
DATA_PREFIX = f"{BUCKET_BASE}/data"
LOGS_PREFIX = f"{BUCKET_BASE}/logs"

DCellar_HEADERS = {
    "X-Gnfd-Date": "",
    "X-Gnfd-Expiry-Timestamp": "",
}

DCELLAR_SP_ADDRESS = os.getenv("DCELLAR_SP_ADDRESS", "")
BUCKET_ID = os.getenv("BUCKET_ID", "")


class GreenfieldClient:
    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None
        self._cache: Dict[str, Tuple[float, Any]] = {}
        self._cache_lock = asyncio.Lock()
        self._nonce_lock = asyncio.Lock()
        self._nonce: Optional[int] = None
        self._auth_token: Optional[str] = None
        self._auth_token_expiry: float = 0.0

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=TIMEOUT,
                limits=httpx.Limits(max_keepalive_connections=20, max_connections=50),
                verify=True,
            )
        return self._client

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    def _sign_message(self, message: str) -> str:
        encoded = encode_defunct(text=message)
        signed = ACCOUNT.sign_message(encoded)
        return signed.signature.hex()

    def _hash_uid(self, uid: int) -> str:
        raw = f"{SALT_UID}{uid}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]

    async def _ensure_auth_token(self) -> str:
        now = time.time()
        if self._auth_token and self._auth_token_expiry > now + 60:
            return self._auth_token

        client = await self._get_client()
        nonce_response = await client.get(
            f"{SP_ENDPOINT}{DELEGATE_PREFIX}/auth/request_nonce",
            params={"address": ADDRESS},
        )
        nonce_response.raise_for_status()
        nonce_data = nonce_response.json()
        auth_nonce = nonce_data.get("nonce", "")

        if not auth_nonce:
            raise Exception("No se pudo obtener el nonce de autenticación.")

        message = f"Sign this message to authenticate with Greenfield SP: {auth_nonce}"
        signature = self._sign_message(message)

        auth_response = await client.post(
            f"{SP_ENDPOINT}{DELEGATE_PREFIX}/auth/verify",
            json={
                "address": ADDRESS,
                "message": message,
                "signature": signature,
            },
            headers=DEFAULT_HEADERS,
        )
        auth_response.raise_for_status()
        auth_result = auth_response.json()

        self._auth_token = auth_result.get("token", "")
        self._auth_token_expiry = now + 3600

        return self._auth_token

    async def _get_auth_headers(self) -> Dict[str, str]:
        token = await self._ensure_auth_token()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    async def _get_nonce(self) -> int:
        async with self._nonce_lock:
            client = await self._get_client()
            response = await client.get(
                f"{GREENFIELD_RPC_URL}/cosmos/auth/v1beta1/accounts/{ADDRESS}"
            )
            response.raise_for_status()
            data = response.json()
            account = data.get("account", {})
            sequence = int(account.get("sequence", "0"))
            self._nonce = sequence
            return sequence

    async def _send_tx(self, messages: List[Dict], memo: str = "", gas_wanted: int = 200000) -> Dict:
        nonce = await self._get_nonce()

        tx_body = {
            "messages": messages,
            "memo": memo,
            "timeout_height": "0",
            "extension_options": [],
            "non_critical_extension_options": [],
        }

        auth_info = {
            "signer_infos": [
                {
                    "public_key": {
                        "type_url": "/cosmos.crypto.eth.ethsecp256k1.PubKey",
                        "value": base64.b64encode(
                            HexBytes(ACCOUNT._key_obj.public_key.to_bytes()).to_bytes(33, "big")
                        ).decode(),
                    },
                    "mode_info": {"single": {"mode": "SIGN_MODE_EIP712"}},
                    "sequence": str(nonce),
                }
            ],
            "fee": {
                "amount": [{"denom": "BNB", "amount": "50000000000000"}],
                "gas_limit": str(gas_wanted),
            },
        }

        sign_doc = {
            "body_bytes": base64.b64encode(
                json.dumps(tx_body, separators=(",", ":")).encode()
            ).decode(),
            "auth_info_bytes": base64.b64encode(
                json.dumps(auth_info, separators=(",", ":")).encode()
            ).decode(),
            "account_number": "0",
            "chain_id": str(GREENFIELD_CHAIN_ID),
        }

        sign_bytes = json.dumps(sign_doc, separators=(",", ":"), sort_keys=True).encode()
        signature = ACCOUNT.sign_message(encode_defunct(sign_bytes)).signature.hex()

        tx_bytes = {
            "tx_bytes": base64.b64encode(
                json.dumps(
                    {
                        "body": tx_body,
                        "auth_info": auth_info,
                        "signatures": [
                            base64.b64encode(
                                bytes.fromhex(
                                    signature[2:] if signature.startswith("0x") else signature
                                )
                            ).decode()
                        ],
                    },
                    separators=(",", ":"),
                ).encode()
            ).decode(),
            "mode": "BROADCAST_MODE_SYNC",
        }

        client = await self._get_client()
        response = await client.post(
            f"{GREENFIELD_RPC_URL}/cosmos/tx/v1beta1/txs",
            json=tx_bytes,
            headers=DEFAULT_HEADERS,
        )
        response.raise_for_status()
        result = response.json()

        if result.get("tx_response", {}).get("code", 0) != 0:
            raise Exception(
                f"TX falló: {result.get('tx_response', {}).get('raw_log', 'Desconocido')}"
            )

        self._nonce = nonce + 1
        return result

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
    )
    async def create_object(
        self,
        object_name: str,
        payload: bytes,
        content_type: str = "text/plain",
        tags: Optional[Dict[str, str]] = None,
    ) -> Dict:
        client = await self._get_client()
        auth_headers = await self._get_auth_headers()

        sanitized_tags = {}
        if tags:
            for key, value in tags.items():
                safe_value = str(value).replace("/", "\\/").replace(",", "\\,")
                sanitized_tags[key] = safe_value

        headers = {
            **auth_headers,
            "Content-Type": content_type,
        }

        for key, value in sanitized_tags.items():
            headers[f"X-Gnfd-Tag-{key}"] = value

        headers["X-Gnfd-Object-Checksum"] = hashlib.sha256(payload).hexdigest()

        sp_url = f"{SP_ENDPOINT}/greenfield/sp/put_object"

        params = {
            "object_name": object_name,
            "bucket_name": BUCKET_NAME,
            "content_type": content_type,
            "expect_checksum": hashlib.sha256(payload).hexdigest(),
        }

        if BUCKET_ID:
            params["bucket_id"] = BUCKET_ID

        response = await client.post(
            sp_url,
            params=params,
            content=payload,
            headers=headers,
        )
        response.raise_for_status()
        return response.json()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
    )
    async def get_object(self, object_name: str) -> Tuple[bytes, Dict[str, str]]:
        client = await self._get_client()

        sp_url = f"{SP_ENDPOINT}/view/{BUCKET_NAME}/{object_name}"

        response = await client.get(sp_url)

        if response.status_code == 404:
            raise FileNotFoundError(f"Objeto no encontrado: {object_name}")
        response.raise_for_status()

        tags = {}
        for header, value in response.headers.items():
            if header.lower().startswith("x-gnfd-tag-"):
                key = header[len("x-gnfd-tag-"):]
                tags[key] = value.replace("\\/", "/").replace("\\,", ",")

        return response.content, tags

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
    )
    async def update_object_tags(self, object_name: str, tags: Dict[str, str]) -> Dict:
        client = await self._get_client()
        auth_headers = await self._get_auth_headers()

        sanitized_tags = []
        for k, v in tags.items():
            safe_value = str(v).replace("/", "\\/").replace(",", "\\,")
            sanitized_tags.append({"key": k, "value": safe_value})

        metadata_update = {
            "object_name": object_name,
            "bucket_name": BUCKET_NAME,
            "tags": {"tags": sanitized_tags},
        }

        sp_url = f"{SP_ENDPOINT}/greenfield/sp/update_object_metadata"

        response = await client.post(
            sp_url,
            json=metadata_update,
            headers=auth_headers,
        )
        response.raise_for_status()
        return response.json()

    async def get_object_tags(self, object_name: str) -> Dict[str, str]:
        try:
            _, tags = await self.get_object(object_name)
            return tags
        except FileNotFoundError:
            return {}

    async def upload_user_profile(self, uid: int, tags: Dict[str, str]) -> str:
        uid_hash = self._hash_uid(uid)
        object_name = f"{USERS_PREFIX}/{uid_hash}"

        await self.create_object(
            object_name=object_name,
            payload=b"",
            content_type="application/octet-stream",
            tags={**tags, "uid_hash": uid_hash},
        )
        return uid_hash

    async def get_user_tags(self, uid: int) -> Dict[str, str]:
        uid_hash = self._hash_uid(uid)
        object_name = f"{USERS_PREFIX}/{uid_hash}"
        return await self.get_object_tags(object_name)

    async def update_user_tags(self, uid: int, tags: Dict[str, str]) -> Dict:
        uid_hash = self._hash_uid(uid)
        object_name = f"{USERS_PREFIX}/{uid_hash}"
        return await self.update_object_tags(object_name, tags)

    async def user_exists(self, uid: int) -> bool:
        uid_hash = self._hash_uid(uid)
        object_name = f"{USERS_PREFIX}/{uid_hash}"
        try:
            await self.get_object_tags(object_name)
            return True
        except FileNotFoundError:
            return False

    async def upload_contribution(
        self, uid: int, content: str, tags: Dict[str, str]
    ) -> Tuple[str, str, str]:
        uid_hash = self._hash_uid(uid)
        now = datetime.now(timezone.utc)
        year_month = now.strftime("%Y-%m")
        ts = now.strftime("%Y%m%d_%H%M%S_%f")

        filename = f"{uid_hash}_{ts}.txt"
        object_name = f"{APORTES_PREFIX}/{year_month}/{filename}"

        result = await self.create_object(
            object_name=object_name,
            payload=content.encode("utf-8"),
            content_type="text/plain; charset=utf-8",
            tags={"author_uid": uid_hash, **tags},
        )

        cid = result.get("object_info", {}).get("id", "")
        return object_name, cid, uid_hash

    async def get_contribution_text(self, object_name: str) -> str:
        data, _ = await self.get_object(object_name)
        return data.decode("utf-8")

    async def list_user_contributions(
        self, uid: int, limit: int = 20
    ) -> List[Dict[str, Any]]:
        uid_hash = self._hash_uid(uid)
        client = await self._get_client()

        sp_url = f"{SP_ENDPOINT}/greenfield/sp/list_objects"

        params = {
            "bucket_name": BUCKET_NAME,
            "prefix": f"{APORTES_PREFIX}/",
            "max_keys": 1000,
        }

        if BUCKET_ID:
            params["bucket_id"] = BUCKET_ID

        response = await client.get(sp_url, params=params)
        response.raise_for_status()
        data = response.json()

        contributions = []
        for obj in data.get("objects", []):
            obj_name = obj.get("object_name", "")
            obj_tags = obj.get("tags", {})

            if obj_tags.get("author_uid") == uid_hash:
                contributions.append(
                    {
                        "object_name": obj_name,
                        "size": obj.get("object_size", 0),
                        "tags": obj_tags,
                    }
                )

        contributions.sort(key=lambda x: x["object_name"], reverse=True)
        return contributions[:limit]

    async def list_recent_contributions_from_bucket(
        self, minutes: int = 10
    ) -> List[Dict[str, Any]]:
        client = await self._get_client()
        sp_url = f"{SP_ENDPOINT}/greenfield/sp/list_objects"

        params = {
            "bucket_name": BUCKET_NAME,
            "prefix": f"{APORTES_PREFIX}/",
            "max_keys": 500,
        }

        if BUCKET_ID:
            params["bucket_id"] = BUCKET_ID

        response = await client.get(sp_url, params=params)
        response.raise_for_status()
        data = response.json()

        cutoff = datetime.now(timezone.utc).timestamp() - (minutes * 60)
        recent = []

        for obj in data.get("objects", []):
            obj_name = obj.get("object_name", "")
            created_at = obj.get("create_at", 0)

            if created_at >= cutoff:
                recent.append(
                    {
                        "object_name": obj_name,
                        "tags": obj.get("tags", {}),
                        "created_at": created_at,
                    }
                )

        return recent

    async def get_or_create_brain_pointer(self) -> str:
        object_name = f"{DATA_PREFIX}/brain_pointer"
        try:
            data, tags = await self.get_object(object_name)
            return tags.get("latest_v", "")
        except FileNotFoundError:
            await self.create_object(
                object_name=object_name,
                payload=json.dumps(
                    {"version": "v0", "created": datetime.now(timezone.utc).isoformat()}
                ).encode(),
                content_type="application/json",
                tags={"latest_v": "v0"},
            )
            return "v0"

    async def update_brain_pointer(self, version: str) -> Dict:
        object_name = f"{DATA_PREFIX}/brain_pointer"
        return await self.update_object_tags(object_name, {"latest_v": version})

    async def update_top10_leaderboard(self, leaderboard_data: List[Dict]) -> Dict:
        object_name = f"{DATA_PREFIX}/top10.json"
        payload = json.dumps(leaderboard_data, ensure_ascii=False, indent=2).encode("utf-8")

        try:
            await self.get_object(object_name)
            await self.create_object(
                object_name=object_name,
                payload=payload,
                content_type="application/json",
            )
        except FileNotFoundError:
            await self.create_object(
                object_name=object_name,
                payload=payload,
                content_type="application/json",
            )

        return {"status": "ok", "count": len(leaderboard_data)}

    async def get_leaderboard(self) -> List[Dict]:
        object_name = f"{DATA_PREFIX}/top10.json"
        try:
            data, _ = await self.get_object(object_name)
            return json.loads(data.decode("utf-8"))
        except FileNotFoundError:
            return []

    async def get_system_config(self) -> Dict:
        object_name = f"{DATA_PREFIX}/system_config.json"
        try:
            data, _ = await self.get_object(object_name)
            return json.loads(data.decode("utf-8"))
        except FileNotFoundError:
            default_config = {
                "thinker_prompt": "Eres Synergix, una inteligencia colectiva descentralizada...",
                "judge_prompt": "Analiza el siguiente aporte y devuelve JSON estricto...",
                "gas_params": {"max_gas": 200000},
                "version": "1.0.0",
            }
            return default_config

    async def upload_log(self, date_str: str, compressed_data: bytes) -> Dict:
        object_name = f"{LOGS_PREFIX}/{date_str}.log.gz"
        return await self.create_object(
            object_name=object_name,
            payload=compressed_data,
            content_type="application/gzip",
        )

    async def create_challenge(self, challenge_id: str, challenge_data: Dict) -> Dict:
        object_name = f"{DATA_PREFIX}/challenges/{challenge_id}.json"
        payload = json.dumps(challenge_data, ensure_ascii=False, indent=2).encode("utf-8")
        return await self.create_object(
            object_name=object_name,
            payload=payload,
            content_type="application/json",
        )

    async def get_current_challenge(self) -> Optional[Dict]:
        client = await self._get_client()
        sp_url = f"{SP_ENDPOINT}/greenfield/sp/list_objects"

        params = {
            "bucket_name": BUCKET_NAME,
            "prefix": f"{DATA_PREFIX}/challenges/",
            "max_keys": 100,
        }

        if BUCKET_ID:
            params["bucket_id"] = BUCKET_ID

        response = await client.get(sp_url, params=params)
        response.raise_for_status()
        data = response.json()

        objects = sorted(
            data.get("objects", []), key=lambda x: x.get("create_at", 0), reverse=True
        )

        if objects:
            latest = objects[0]
            obj_data, _ = await self.get_object(latest["object_name"])
            return json.loads(obj_data.decode("utf-8"))
        return None

    async def list_all_users(self) -> List[str]:
        client = await self._get_client()
        sp_url = f"{SP_ENDPOINT}/greenfield/sp/list_objects"

        params = {
            "bucket_name": BUCKET_NAME,
            "prefix": f"{USERS_PREFIX}/",
            "max_keys": 10000,
        }

        if BUCKET_ID:
            params["bucket_id"] = BUCKET_ID

        response = await client.get(sp_url, params=params)
        response.raise_for_status()
        data = response.json()

        user_ids = []
        for obj in data.get("objects", []):
            obj_name = obj.get("object_name", "")
            uid_hash = obj_name.split("/")[-1]
            if uid_hash and len(uid_hash) == 12:
                user_ids.append(uid_hash)

        return user_ids

    async def reset_daily_counts(self) -> int:
        users = await self.list_all_users()
        reset_count = 0

        for uid_hash in users:
            object_name = f"{USERS_PREFIX}/{uid_hash}"
            try:
                await self.update_object_tags(object_name, {"daily_aportes_count": "0"})
                reset_count += 1
            except Exception:
                continue

        return reset_count

    async def get_user_stats(self, uid: int) -> Dict[str, Any]:
        tags = await self.get_user_tags(uid)
        contributions = await self.list_user_contributions(uid, limit=50)

        return {
            "points": int(tags.get("points", 0)),
            "rank": tags.get("rank", "🌱 Iniciado"),
            "daily_aportes_count": int(tags.get("daily_aportes_count", 0)),
            "total_uses_count": int(tags.get("total_uses_count", 0)),
            "language": tags.get("language", "es"),
            "contribution_count": len(contributions),
            "uid_hash": self._hash_uid(uid),
        }

    async def add_residual_points(self, uid_hash: str) -> Optional[int]:
        object_name = f"{USERS_PREFIX}/{uid_hash}"

        try:
            tags = await self.get_object_tags(object_name)
            if not tags:
                return None

            current_points = int(tags.get("points", 0))
            current_uses = int(tags.get("total_uses_count", 0))

            new_points = current_points + 1
            new_uses = current_uses + 1

            await self.update_object_tags(
                object_name,
                {
                    "points": str(new_points),
                    "total_uses_count": str(new_uses),
                },
            )

            return new_points
        except FileNotFoundError:
            return None

    async def check_bucket_access(self) -> Dict[str, Any]:
        client = await self._get_client()
        auth_headers = await self._get_auth_headers()

        sp_url = f"{SP_ENDPOINT}{DELEGATE_PREFIX}/buckets/{BUCKET_NAME}"

        response = await client.get(sp_url, headers=auth_headers)
        response.raise_for_status()
        data = response.json()

        return {
            "bucket_name": data.get("bucket_name", BUCKET_NAME),
            "bucket_id": data.get("bucket_id", ""),
            "owner": data.get("owner", ""),
            "status": data.get("bucket_status", ""),
            "create_at": data.get("create_at", 0),
        }


_greenfield_instance: Optional[GreenfieldClient] = None
_instance_lock = asyncio.Lock()


async def get_greenfield_client() -> GreenfieldClient:
    global _greenfield_instance

    async with _instance_lock:
        if _greenfield_instance is None:
            _greenfield_instance = GreenfieldClient()

        return _greenfield_instance


import base64


__all__ = [
    "GreenfieldClient",
    "get_greenfield_client",
    "BUCKET_NAME",
    "BUCKET_BASE",
    "USERS_PREFIX",
    "APORTES_PREFIX",
    "DATA_PREFIX",
    "LOGS_PREFIX",
    "SALT_UID",
    "SP_ENDPOINT",
]
