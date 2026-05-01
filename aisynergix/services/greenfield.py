"""
greenfield.py — Cliente Web3 para BNB Greenfield Mainnet                                             ================================================================                                     Stateless absoluto. Única fuente de verdad: bucket synergix-v1.

Autenticación:
  - GREENFIELD-ECDSA vía eth-account (wallet)
  - MsgToSign con formato canónico de Greenfield
  - X-Gnfd-App-Domain obligatorio
  - X-Gnfd-Expiry-Timestamp en RFC3339
  - Para CreateObject: X-Gnfd-Unsigned-Msg con MsgCreateObject en protobuf                                                                                                                                Endpoints:                                                                                             - RPC Chain:  https://greenfield-chain.bnbchain.org
  - SP:         https://greenfield-sp.bnbchain.org

Referencias:
  https://github.com/bnb-chain/greenfield-storage-provider/blob/master/docs/storage-provider-rest-api/README.md
  https://github.com/bnb-chain/greenfield-common/blob/master/go/http/gen_sign_str.go
"""
                                                                                                     import hashlib                                                                                       import json                                                                                          import logging
import os                                                                                            import re
import struct                                                                                        import time                                                                                          from datetime import datetime, timedelta, timezone
from typing import Any, Optional                                                                     from urllib.parse import quote, unquote

import httpx
from eth_account import Account
from eth_account.messages import encode_defunct
from eth_utils import keccak                                                                         from tenacity import (                                                                                   retry,                                                                                               retry_if_exception_type,                                                                             stop_after_attempt,                                                                                  wait_exponential,                                                                                )                                                                                                    
logger = logging.getLogger(__name__)                                                                                                                                                                      # ──────────────────────────────────────────────────────────────
# Configuración
# ──────────────────────────────────────────────────────────────

BUCKET_NAME = os.getenv("GREENFIELD_BUCKET", "synergix-v1")
RPC_ENDPOINT = os.getenv("GREENFIELD_RPC", "https://greenfield-chain.bnbchain.org")
SP_ENDPOINT = os.getenv("GREENFIELD_SP", "https://greenfield-sp.bnbchain.org")                       PRIVATE_KEY = os.getenv("GREENFIELD_PRIVATE_KEY", "")
CREATOR_ADDRESS = os.getenv("GREENFIELD_CREATOR", "")                                                APP_DOMAIN = os.getenv("GREENFIELD_APP_DOMAIN", "greenfield-sp.bnbchain.org")

if PRIVATE_KEY and not CREATOR_ADDRESS:
    try:                                                                                                     acct = Account.from_key(PRIVATE_KEY)                                                                 CREATOR_ADDRESS = acct.address
    except Exception as e:                                                                                   logger.error("No se pudo derivar dirección de GREENFIELD_PRIVATE_KEY: %s", e)                        CREATOR_ADDRESS = ""                                                                                                                                                                                                                                                                                   # ──────────────────────────────────────────────────────────────                                     # Sanitización de headers (RFC 7230 §3.2.6)
# ──────────────────────────────────────────────────────────────                                                                                                                                          def sanitize_header_value(value: str) -> str:                                                            """URL-encode non-ASCII: '🌱 Iniciado' → '%F0%9F%8C%B1%20Iniciado'"""
    if not isinstance(value, str):
        return str(value)                                                                                try:                                                                                                     value.encode("ascii")
        return value                                                                                     except UnicodeEncodeError:                                                                               return quote(value, safe="")

                                                                                                     def unsanitize_header_value(value: str) -> str:
    """Reverse: '%F0%9F%8C%B1%20Iniciado' → '🌱 Iniciado'"""                                             if not isinstance(value, str):
        return str(value)                                                                                if "%" in value:                                                                                         try:
            return unquote(value)                                                                            except Exception:
            return value
    return value                                                                                                                                                                                          
def _hash_uid(uid: int | str) -> str:                                                                    """SHA-256 + salt 'Synergix_' truncado a 12 chars."""
    raw = f"Synergix_{uid}"
    return hashlib.sha256(raw.encode()).hexdigest()[:12]                                             
                                                                                                     def _format_rfc3339(ts: int) -> str:                                                                     """Unix timestamp → RFC3339: 1746053206 → '2026-04-30T22:46:46Z'"""                                  dt = datetime.fromtimestamp(ts, tz=timezone.utc)                                                     return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

                                                                                                     # ──────────────────────────────────────────────────────────────                                     # Protobuf mínimo para MsgCreateObject                                                               # ──────────────────────────────────────────────────────────────                                     # Greenfield usa protobuf para MsgCreateObject en X-Gnfd-Unsigned-Msg.                               # Como no tenemos las definiciones .proto compiladas, construimos                                    # manualmente los campos con wire types estándar de protobuf.                                        #                                                                                                    # MsgCreateObject (campos):                                                                          #   1: creator          (string, wire=2)                                                             #   2: bucket_name      (string, wire=2)                                                             #   3: object_name      (string, wire=2)                                                             #   4: payload_size     (uint64, wire=0)                                                             #   5: visibility       (int32 enum, wire=0)
#   6: content_type     (string, wire=2)
#   7: primary_sp_approval (message, wire=2)                                                         #   8: expect_checksums (bytes, wire=2)                                                              #   9: redundancy_type  (int32 enum, wire=0)                                                         #                                                                                                    # Approval:                                                                                          #   1: expired_height   (uint64, wire=0)                                                             #   2: sig              (bytes, wire=2)                                                              #                                                                                                    # VisibilityType: PRIVATE=1, PUBLIC_READ=2                                                           # RedundancyType: EC=1, REPLICA=2                                                                    # ──────────────────────────────────────────────────────────────                                                                                                                                          def _proto_varint(value: int) -> bytes:                                                                  """Codifica un entero como varint (protobuf)."""                                                     result = b""                                                                                         while value > 127:                                                                                       result += bytes([(value & 0x7F) | 0x80])
        value >>= 7
    result += bytes([value & 0x7F])                                                                      return result                                                                                                                                                                                                                                                                                              def _proto_field_string(field_num: int, value: str) -> bytes:                                            """Campo protobuf tipo string: tag + length-delimited."""                                            encoded = value.encode("utf-8")                                                                      tag = (field_num << 3) | 2  # wire type 2 = length-delimited                                         return _proto_varint(tag) + _proto_varint(len(encoded)) + encoded                                                                                                                                     
def _proto_field_uint64(field_num: int, value: int) -> bytes:
    """Campo protobuf tipo uint64: tag + varint."""                                                      tag = (field_num << 3) | 0  # wire type 0 = varint                                                   return _proto_varint(tag) + _proto_varint(value)                                                 
                                                                                                     def _proto_field_int32(field_num: int, value: int) -> bytes:                                             """Campo protobuf tipo int32 (enum): tag + varint."""                                                tag = (field_num << 3) | 0                                                                           return _proto_varint(tag) + _proto_varint(value)                                                                                                                                                                                                                                                           def _proto_field_bytes(field_num: int, value: bytes) -> bytes:                                           """Campo protobuf tipo bytes: tag + length-delimited."""                                             tag = (field_num << 3) | 2                                                                           return _proto_varint(tag) + _proto_varint(len(value)) + value                                                                                                                                         
def _proto_field_message(field_num: int, msg: bytes) -> bytes:
    """Campo protobuf tipo message embedded: tag + length-delimited."""                                  tag = (field_num << 3) | 2                                                                           return _proto_varint(tag) + _proto_varint(len(msg)) + msg                                                                                                                                                                                                                                                  def _build_msg_approval(expired_height: int = 0, sig: bytes = b"") -> bytes:                             """                                                                                                  Construye el mensaje Approval de protobuf.                                                           """                                                                                                  result = b""                                                                                         if expired_height > 0:                                                                                   result += _proto_field_uint64(1, expired_height)                                                 if sig:
        result += _proto_field_bytes(2, sig)
    return result                                                                                                                                                                                                                                                                                              def _build_msg_create_object(                                                                            creator: str,                                                                                        bucket_name: str,                                                                                    object_name: str,                                                                                    payload_size: int,                                                                                   visibility: int = 1,       # PRIVATE                                                                 content_type: str = "application/octet-stream",                                                      redundancy_type: int = 1,  # EC                                                                  ) -> bytes:                                                                                              """                                                                                                  Construye el mensaje MsgCreateObject de protobuf para usar en                                        X-Gnfd-Unsigned-Msg.
                                                                                                         Visibility: 1=PRIVATE, 2=PUBLIC_READ                                                                 RedundancyType: 1=EC_TYPE (Erasure Coding), 2=REPLICA_TYPE                                           """                                                                                                  result = b""                                                                                         result += _proto_field_string(1, creator)                                                            result += _proto_field_string(2, bucket_name)
    result += _proto_field_string(3, object_name)                                                        result += _proto_field_uint64(4, payload_size)                                                       result += _proto_field_int32(5, visibility)                                                          result += _proto_field_string(6, content_type)                                                       # primary_sp_approval: Approval con sig vacía, expired_height=0                                      approval = _build_msg_approval(expired_height=0, sig=b"")                                            result += _proto_field_message(7, approval)
    # expect_checksums: vacío
    result += _proto_field_bytes(8, b"")                                                                 result += _proto_field_int32(9, redundancy_type)                                                     return result                                                                                                                                                                                                                                                                                              def _build_create_object_unsigned_msg(                                                                   creator: str,                                                                                        bucket_name: str,
    object_name: str,                                                                                    payload_size: int,
    content_type: str = "application/octet-stream",
    visibility: str = "VISIBILITY_TYPE_PRIVATE",
) -> str:                                                                                                """                                                                                                  Construye el X-Gnfd-Unsigned-Msg para CreateObject.                                                  Retorna string hex (lowercase) como espera el SP.                                                                                                                                                         El SP espera MsgCreateObject codificado en protobuf JSON y                                           convertido a caracteres hexadecimales en minúscula.                                                  """                                                                                                  vis_int = 1 if visibility == "VISIBILITY_TYPE_PRIVATE" else 2                                        proto_bytes = _build_msg_create_object(                                                                  creator=creator,
        bucket_name=bucket_name,
        object_name=object_name,                                                                             payload_size=payload_size,                                                                           visibility=vis_int,                                                                                  content_type=content_type,                                                                       )
    return proto_bytes.hex().lower()
                                                                                                                                                                                                          # ──────────────────────────────────────────────────────────────                                     # GREENFIELD-ECDSA Signing (MsgToSign canónico)                                                      # ──────────────────────────────────────────────────────────────

def _build_msg_to_sign(                                                                                  app_domain: str,                                                                                     method: str,                                                                                         url_path: str,                                                                                       query_string: str,                                                                                   canonical_headers: str,
    sorted_signed_headers: str,
    expiry_timestamp: str,                                                                           ) -> bytes:                                                                                              """                                                                                                  Construye el MsgToSign según el formato de Greenfield.                                                                                                                                                    Formato exacto (de greenfield-common/go/http/gen_sign_str.go):                                           Greenfield Signed Message:
        {app_domain}                                                                                         {method}
        {url_path}                                                                                           {query_string}                                                                                       {canonical_headers}                                                                                  {sorted_signed_headers}
        {expiry_timestamp}
                                                                                                         canonical_headers: "header1:value1\nheader2:value2\n"                                                sorted_signed_headers: "header1;header2"                                                             """                                                                                                  msg = (
        f"Greenfield Signed Message:\n"                                                                      f"{app_domain}\n"                                                                                    f"{method}\n"                                                                                        f"{url_path}\n"                                                                                      f"{query_string}\n"                                                                                  f"{canonical_headers}\n"                                                                             f"{sorted_signed_headers}\n"                                                                         f"{expiry_timestamp}"                                                                            )                                                                                                    return msg.encode("utf-8")                                                                       
                                                                                                     def _sign_ecdsa(msg_bytes: bytes, private_key: str) -> str:                                              """                                                                                                  Firma un mensaje con ECDSA usando la wallet key.                                                     Usa personal_sign (EIP-191) sobre Keccak256 del mensaje.                                             """                                                                                                  # Greenfield usa Keccak256 hash del MsgToSign, luego EIP-191 personal_sign
    msg_hash = keccak(msg_bytes)                                                                         encoded = encode_defunct(primitive=msg_hash)
    signed = Account.sign_message(encoded, private_key=private_key)                                      return signed.signature.hex()
                                                                                                                                                                                                          # ──────────────────────────────────────────────────────────────
# Cliente principal                                                                                  # ──────────────────────────────────────────────────────────────                                                                                                                                          class GreenfieldClient:
    """
    Cliente asíncrono para BNB Greenfield con autenticación ECDSA.                                   
    Uso:
        gf = GreenfieldClient()
        await gf.initialize()
        await gf.touch_user("abc123")
    """

    def __init__(
        self,
        bucket: str = BUCKET_NAME,
        rpc: str = RPC_ENDPOINT,
        sp: str = SP_ENDPOINT,
        private_key: str = PRIVATE_KEY,
        creator: str = CREATOR_ADDRESS,
        app_domain: str = APP_DOMAIN,
    ):
        self.bucket = bucket
        self.rpc = rpc
        self.sp = sp.rstrip("/")
        self.private_key = private_key
        self.creator = creator
        self.app_domain = app_domain
        self.chain_id: Optional[int] = None
        self._initialized = False
        self.timeout = httpx.Timeout(25.0, connect=10.0)

    # ── Inicialización ───────────────────────────────────────

    async def initialize(self) -> dict:
        chain_id = await self._get_chain_id()
        self.chain_id = chain_id
        self._initialized = True
        logger.info(
            "GreenfieldClient inicializado: chain_id=%s, bucket=%s, sp=%s, creator=%s",
            chain_id, self.bucket, self.sp, self.creator,
        )
        return {
            "chain_id": chain_id,
            "bucket": self.bucket,
            "sp": self.sp,
            "creator": self.creator,
        }

    async def _get_chain_id(self) -> int:
        payload = {"jsonrpc": "2.0", "method": "eth_chainId", "params": [], "id": 1}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(self.rpc, json=payload)
            resp.raise_for_status()
            data = resp.json()
            return int(data["result"], 16)

    # ── Firmado GREENFIELD-ECDSA ─────────────────────────────

    def _build_auth_headers(
        self,
        method: str,
        url_path: str,
        query_string: str,
        extra_headers: dict,
        expiry_ts: int,
    ) -> dict:
        """
        Construye los headers de autenticación GREENFIELD-ECDSA.

        Flujo:
        1. Seleccionar qué headers se firman (signed headers)
        2. Construir canonical_headers (key:value\n, ordenado)
        3. Construir sorted_signed_headers (key1;key2, ordenado)
        4. Construir MsgToSign
        5. Firmar con ECDSA (Keccak256 + EIP-191)
        6. Armar Authorization: GREENFIELD-ECDSA SignedHeaders=..., Signature=...

        Returns:
            dict con X-Gnfd-App-Domain, X-Gnfd-Expiry-Timestamp,
            X-Gnfd-User-Address, Authorization
        """
        expiry_str = _format_rfc3339(expiry_ts)

        # Headers base que siempre se firman
        signed = {
            "x-gnfd-app-domain": self.app_domain,
            "x-gnfd-expiry-timestamp": expiry_str,
            "x-gnfd-user-address": self.creator,
        }

        # Agregar headers extra que queremos firmar
        for k, v in extra_headers.items():
            key_lower = k.lower()
            if key_lower.startswith("x-gnfd-") or key_lower in ("content-type",):                                    signed[key_lower] = sanitize_header_value(str(v))

        # Ordenar alfabéticamente
        sorted_keys = sorted(signed.keys())

        # Construir canonical_headers: "key:value\nkey:value\n"
        canonical = ""
        for k in sorted_keys:
            canonical += f"{k}:{signed[k]}\n"

        # Construir sorted_signed_headers: "key1;key2"
        sorted_headers_str = ";".join(sorted_keys)

        # Construir MsgToSign
        msg_bytes = _build_msg_to_sign(
            app_domain=self.app_domain,
            method=method,
            url_path=url_path,
            query_string=query_string,
            canonical_headers=canonical,
            sorted_signed_headers=sorted_headers_str,
            expiry_timestamp=expiry_str,
        )

        # Firmar
        signature = ""
        if self.private_key:
            signature = _sign_ecdsa(msg_bytes, self.private_key)

        # Armar Authorization
        auth_value = (
            f"GREENFIELD-ECDSA SignedHeaders={sorted_headers_str}, "
            f"Signature={signature}"
        )

        logger.debug(
            "Auth headers: domain=%s, signed=%s, sig=%s...",
            self.app_domain, sorted_headers_str, signature[:16] if signature else "NONE",
        )

        return {
            "X-Gnfd-App-Domain": self.app_domain,
            "X-Gnfd-Expiry-Timestamp": expiry_str,
            "X-Gnfd-User-Address": self.creator,
            "Authorization": auth_value,
        }

    # ── Petición HTTP con retry ──────────────────────────────

    @retry(
        retry=retry_if_exception_type(
            (httpx.HTTPStatusError, httpx.ConnectError, httpx.TimeoutException)
        ),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def _sp_request(
        self,
        method: str,
        url_path: str,
        body: Optional[bytes] = None,
        headers: Optional[dict] = None,
        query_string: str = "",
        expiry_ts: Optional[int] = None,
    ) -> httpx.Response:
        """
        Petición autenticada al Storage Provider.

        Args:
            method: GET, PUT, POST, DELETE, HEAD
            url_path: ruta absoluta (ej: /object/synergix-v1/aisynergix/users/abc)
            body: bytes del contenido
            headers: headers adicionales
            query_string: query string (ej: prefix=aisynergix/users/&max-keys=1000)
            expiry_ts: timestamp de expiración
        """
        url = f"{self.sp}{url_path}"
        exp_ts = expiry_ts or int(time.time()) + 1800

        # Headers extra que van en la petición
        extra = {}
        if headers:
            extra.update(headers)

        # Construir headers de autenticación
        auth_headers = self._build_auth_headers(
            method=method,
            url_path=url_path,
            query_string=query_string,
            extra_headers=extra,
            expiry_ts=exp_ts,
        )

        # Merge: auth primero, luego los extra sanitizados
        all_headers = dict(auth_headers)
        if headers:
            for k, v in headers.items():
                all_headers[k] = sanitize_header_value(str(v))

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.request(
                method=method,
                url=url,
                content=body,
                headers=all_headers,
            )
            return resp

    # ── Operaciones CRUD ─────────────────────────────────────

    async def object_exists(self, path: str) -> bool:
        sp_path = f"/object/{self.bucket}/{path}"
        try:
            resp = await self._sp_request("GET", sp_path)
            return resp.status_code == 200
        except Exception:
            return False

    async def get_object(self, path: str) -> Optional[bytes]:
        sp_path = f"/object/{self.bucket}/{path}"
        resp = await self._sp_request("GET", sp_path)                                                        if resp.status_code == 200:                                                                              return resp.content                                                                              elif resp.status_code == 404:                                                                            return None                                                                                      else:                                                                                                    resp.raise_for_status()                                                                              return None                                                                                                                                                                                       async def get_object_tags(self, path: str) -> dict:                                                      sp_path = f"/object/{self.bucket}/{path}"                                                            resp = await self._sp_request("GET", sp_path)                                                        if resp.status_code not in (200, 206):                                                                   return {}                                                                                        tags = {}                                                                                            for key, value in resp.headers.items():                                                                  key_lower = key.lower()                                                                              if key_lower.startswith("x-gnfd-tag-"):                                                                  clean_key = key_lower.replace("x-gnfd-tag-", "")                                                     tags[clean_key] = unsanitize_header_value(value)                                             return tags                                                                                                                                                                                           async def create_object(                                                                                 self,                                                                                                path: str,                                                                                           content: bytes,                                                                                      tags: Optional[dict] = None,                                                                         content_type: str = "application/octet-stream",                                                      visibility: str = "VISIBILITY_TYPE_PRIVATE",                                                     ) -> bool:                                                                                               """                                                                                                  Crea un objeto en el bucket.                                                                                                                                                                              Flujo:                                                                                               1. Construir MsgCreateObject en protobuf                                                             2. Enviarlo como X-Gnfd-Unsigned-Msg                                                                 3. El SP valida la firma ECDSA y crea el objeto on-chain                                             4. El contenido se almacena                                                                          """                                                                                                  sp_path = f"/object/{self.bucket}/{path}"                                                            object_name = path  # El nombre del objeto es la ruta completa                                                                                                                                            # Construir MsgCreateObject como protobuf hex                                                        unsigned_msg = _build_create_object_unsigned_msg(                                                        creator=self.creator,                                                                                bucket_name=self.bucket,                                                                             object_name=object_name,                                                                             payload_size=len(content),                                                                           content_type=content_type,                                                                           visibility=visibility,                                                                           )                                                                                                                                                                                                         req_headers = {
            "Content-Type": content_type,
            "X-Gnfd-Visibility": visibility,                                                                     "X-Gnfd-Unsigned-Msg": unsigned_msg,                                                             }                                                                                                                                                                                                         if tags:                                                                                                 for k, v in tags.items():                                                                                req_headers[f"X-Gnfd-Tag-{k}"] = str(v)                                                                                                                                                           try:                                                                                                     resp = await self._sp_request(                                                                           "PUT", sp_path, body=content, headers=req_headers                                                )                                                                                                    if resp.status_code in (200, 201):                                                                       logger.info(                                                                                             "Objeto creado: %s (tags=%s, size=%d)",                                                              path,                                                                                                list(tags.keys()) if tags else [],                                                                   len(content),                                                                                    )                                                                                                    return True                                                                                      elif resp.status_code == 404:                                                                            logger.error(                                                                                            "Bucket '%s' no encontrado al crear %s. ¿Existe en mainnet?",                                        self.bucket, path,
                )                                                                                                    resp.raise_for_status()                                                                          else:                                                                                                    logger.error(                                                                                            "Error al crear objeto %s: HTTP %s — %s",                                                            path, resp.status_code, resp.text[:500],                                                         )                                                                                                    resp.raise_for_status()
        except Exception as e:
            logger.exception("Excepción al crear objeto %s", path)
            raise

        return False

    async def update_object_tags(self, path: str, tags: dict) -> bool:
        """Actualiza tags de un objeto existente (requiere que el objeto ya exista)."""
        sp_path = f"/object/{self.bucket}/{path}"
        current = await self.get_object(path)
        if current is None:
            logger.warning("update_object_tags: objeto %s no existe", path)
            return False

        body = current
        req_headers = {
            "Content-Type": "application/octet-stream",
            "X-Gnfd-Visibility": "VISIBILITY_TYPE_PRIVATE",
        }
        for k, v in tags.items():
            req_headers[f"X-Gnfd-Tag-{k}"] = str(v)

        # Para update, también necesitamos X-Gnfd-Unsigned-Msg
        unsigned_msg = _build_create_object_unsigned_msg(
            creator=self.creator,
            bucket_name=self.bucket,
            object_name=path,
            payload_size=len(body),
            content_type="application/octet-stream",
            visibility="VISIBILITY_TYPE_PRIVATE",
        )
        req_headers["X-Gnfd-Unsigned-Msg"] = unsigned_msg

        try:
            resp = await self._sp_request(
                "PUT", sp_path, body=body, headers=req_headers
            )
            if resp.status_code in (200, 201):
                logger.info("Tags actualizados: %s → %s", path, list(tags.keys()))
                return True
            else:
                logger.warning("Error actualizando tags %s: HTTP %s — %s", path, resp.status_code, resp.text[:300])
                return False                                                                                 except Exception as e:
            logger.exception("Excepción al actualizar tags de %s", path)
            return False

    async def list_objects(self, prefix: str, max_keys: int = 1000) -> list[str]:
        sp_path = f"/list/{self.bucket}"
        qs = f"prefix={prefix}&max-keys={max_keys}"
        try:
            resp = await self._sp_request("GET", sp_path, query_string=qs)                                       if resp.status_code == 404:
                logger.debug("list_objects: 404 para prefix=%s", prefix)
                return []                                                                                        if resp.status_code != 200:
                logger.warning("list_objects: HTTP %s para prefix=%s", resp.status_code, prefix)                     return []
            keys = re.findall(r"<Key>(.*?)</Key>", resp.text)                                                    logger.debug("list_objects: %d objetos bajo prefix=%s", len(keys), prefix)
            return list(keys)                                                                                except Exception as e:
            logger.exception("Excepción en list_objects para prefix=%s", prefix)                                 return []
                                                                                                         # ── Usuarios ─────────────────────────────────────────────
                                                                                                         async def touch_user(self, uid: str) -> dict:
        """Crea o actualiza un usuario fantasma. Retorna sus tags."""                                        path = f"aisynergix/users/{uid}"
        tags = await self.get_object_tags(path)                                                      
        if not tags:                                                                                             logger.info("Creando nuevo usuario fantasma: %s", uid)
            default_tags = {                                                                                         "fsm_state": "idle",
                "points": "0",                                                                                       "rank": "🌱 Iniciado",
                "daily_aportes_count": "0",                                                                          "total_uses_count": "0",
                "language": "es",                                                                                    "last_seen_ts": str(int(time.time())),
            }                                                                                                    try:
                await self.create_object(path, b"", tags=default_tags)                                               return default_tags
            except Exception as e:
                logger.error("No se pudo crear usuario fantasma %s: %s", uid, e)                                     return default_tags
                                                                                                             tags["last_seen_ts"] = str(int(time.time()))
        try:                                                                                                     await self.update_object_tags(path, {"last_seen_ts": tags["last_seen_ts"]})
        except Exception:
            pass
        return tags                                                                                  
    async def get_user_tags(self, uid: str) -> dict:                                                         return await self.touch_user(uid)
                                                                                                         async def set_user_tags(self, uid: str, tags: dict) -> bool:
        path = f"aisynergix/users/{uid}"                                                                     existing = await self.get_object_tags(path)
                                                                                                             if not existing:
            defaults = {                                                                                             "fsm_state": "idle", "points": "0", "rank": "🌱 Iniciado",
                "daily_aportes_count": "0", "total_uses_count": "0",                                                 "language": "es", "last_seen_ts": str(int(time.time())),
            }                                                                                                    defaults.update(tags)
            try:
                return await self.create_object(path, b"", tags=defaults)                                        except Exception:
                return False

        existing.update(tags)                                                                                existing["last_seen_ts"] = str(int(time.time()))
        return await self.update_object_tags(path, existing)                                         
    # ── Aportes ──────────────────────────────────────────────                                      
    async def save_contribution(                                                                             self, author_uid: str, content: str, tags: dict,
        year_month: Optional[str] = None,                                                                ) -> Optional[str]:
        ts = int(time.time())                                                                                if not year_month:
            year_month = datetime.utcnow().strftime("%Y-%m")                                                 object_name = f"{author_uid}_{ts}.txt"
        path = f"aisynergix/aportes/{year_month}/{object_name}"                                              final_tags = {"author_uid": author_uid, "timestamp": str(ts)}
        final_tags.update(tags)                                                                              try:
            success = await self.create_object(                                                                      path, content.encode("utf-8"), tags=final_tags,
                content_type="text/plain; charset=utf-8",                                                        )
            return path if success else None                                                                 except Exception as e:
            logger.exception("Error guardando aporte %s", path)                                                  return None
                                                                                                         async def get_contributions_by_user(
        self, author_uid: str, limit: int = 20                                                           ) -> list[dict]:
        results = []                                                                                         now = datetime.utcnow()
        months = [                                                                                               now.strftime("%Y-%m"),
            (now - timedelta(days=30)).strftime("%Y-%m"),                                                        (now - timedelta(days=60)).strftime("%Y-%m"),
        ]                                                                                                    for month in set(months):
            prefix = f"aisynergix/aportes/{month}/{author_uid}_"
            objects = await self.list_objects(prefix)                                                            for obj_path in objects:
                if len(results) >= limit:
                    break
                try:                                                                                                     content = await self.get_object(obj_path)
                    tags = await self.get_object_tags(obj_path)
                    if content is not None:
                        results.append({                                                                                         "path": obj_path,
                            "content": content.decode("utf-8", errors="replace"),
                            "tags": tags,
                        })                                                                                           except Exception:
                    continue
            if len(results) >= limit:
                break                                                                                        return results

    # ── Brain / Leaderboard / Config ─────────────────────────
                                                                                                         async def get_brain_pointer(self) -> dict:
        path = "aisynergix/data/brain_pointer"
        tags = await self.get_object_tags(path)
        return tags if tags else {"latest_v": ""}                                                    
    async def set_brain_pointer(self, version: str) -> bool:
        path = "aisynergix/data/brain_pointer"
        exists = await self.object_exists(path)                                                              if exists:
            return await self.update_object_tags(path, {"latest_v": version})
        else:
            return await self.create_object(path, b"", tags={"latest_v": version})                   
    async def get_top10(self) -> list[dict]:
        path = "aisynergix/data/top10.json"
        content = await self.get_object(path)                                                                if content:
            try:
                return json.loads(content.decode("utf-8"))
            except json.JSONDecodeError:                                                                             return []
        return []

    async def set_top10(self, data: list[dict]) -> bool:                                                     path = "aisynergix/data/top10.json"
        content = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        exists = await self.object_exists(path)
        if exists:                                                                                               return await self.update_object_tags(path, {"updated_ts": str(int(time.time()))})
        else:
            return await self.create_object(path, content, content_type="application/json")

    async def get_active_challenges(self) -> list[dict]:
        prefix = "aisynergix/data/challenges/"
        objects = await self.list_objects(prefix)
        challenges = []
        for obj_path in objects:
            content = await self.get_object(obj_path)
            if content:
                try:
                    challenges.append(json.loads(content.decode("utf-8")))
                except json.JSONDecodeError:
                    continue
        return challenges

    async def save_challenge(self, challenge_id: str, data: dict) -> bool:
        path = f"aisynergix/data/challenges/{challenge_id}.json"
        content = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        return await self.create_object(path, content, content_type="application/json")              
    async def get_system_config(self) -> dict:
        path = "aisynergix/data/system_config.json"
        content = await self.get_object(path)                                                                if content:
            try:
                return json.loads(content.decode("utf-8"))
            except json.JSONDecodeError:                                                                             pass
        return {
            "judge_threshold": 5.0, "min_contribution_chars": 20,
            "max_daily_default": 5, "points_per_contribution": 10,                                               "challenge_bonus": 5, "residual_points": 1,
        }

    async def upload_log(self, date_str: str, log_content: str) -> bool:                                     path = f"aisynergix/logs/{date_str}.log"
        content = log_content.encode("utf-8")
        exists = await self.object_exists(path)
        if exists:
            existing = await self.get_object(path)
            if existing:
                content = existing + b"\n" + content                                                         return await self.create_object(path, content, content_type="text/plain; charset=utf-8")

    async def rebuild_leaderboard(self) -> list[dict]:                                                       prefix = "aisynergix/users/"
        user_objects = await self.list_objects(prefix)
        if not user_objects:
            logger.info("Leaderboard vacío: 0 usuarios encontrados")
            return []
        users = []
        for obj_path in user_objects:                                                                            uid = obj_path.split("/")[-1] if "/" in obj_path else obj_path
            if not uid:
                continue
            try:                                                                                                     tags = await self.get_object_tags(obj_path)
                points = int(tags.get("points", 0))
                rank = unsanitize_header_value(tags.get("rank", "🌱 Iniciado"))
                total_uses = int(tags.get("total_uses_count", 0))                                                    language = tags.get("language", "es")
                users.append({
                    "uid": uid, "points": points, "rank": rank,                                                          "total_uses": total_uses, "language": language,
                })
            except Exception as e:
                logger.warning("Error leyendo usuario %s: %s", uid, e)                                               continue
        users.sort(key=lambda u: u["points"], reverse=True)
        top10 = users[:10]
        await self.set_top10(top10)                                                                          logger.info("Leaderboard reconstruido: %d usuarios, top 10 guardado", len(users))
        return top10

    async def reset_daily_counts(self) -> int:                                                               prefix = "aisynergix/users/"
        user_objects = await self.list_objects(prefix)
        updated = 0                                                                                          for obj_path in user_objects:
            uid = obj_path.split("/")[-1]
            if not uid:
                continue
            try:
                await self.set_user_tags(uid, {"daily_aportes_count": "0"})
                updated += 1
            except Exception as e:
                logger.warning("Error reseteando daily count para %s: %s", uid, e)
        logger.info("Daily counts reseteados: %d usuarios", updated)
        return updated

    async def add_residual_points(self, author_uid: str, points: int = 1) -> bool:
        tags = await self.get_user_tags(author_uid)
        if not tags:
            return False
        current_points = int(tags.get("points", 0))
        current_uses = int(tags.get("total_uses_count", 0))
        return await self.set_user_tags(author_uid, {
            "points": str(current_points + points),
            "total_uses_count": str(current_uses + 1),
        })


# ──────────────────────────────────────────────────────────────
# Singleton
# ──────────────────────────────────────────────────────────────

_greenfield_instance: Optional[GreenfieldClient] = None


async def get_greenfield() -> GreenfieldClient:
    global _greenfield_instance
    if _greenfield_instance is None:
        _greenfield_instance = GreenfieldClient()
        await _greenfield_instance.initialize()
    return _greenfield_instance


def get_greenfield_sync() -> GreenfieldClient:
    global _greenfield_instance
    if _greenfield_instance is None:
        _greenfield_instance = GreenfieldClient()
    return _greenfield_instance


# ──────────────────────────────────────────────────────────────
# Instancia global para uso directo (soporta importación legacy)
# ──────────────────────────────────────────────────────────────

greenfield = GreenfieldClient()   # ← ESTA LÍNEA ES LA CLAVE
