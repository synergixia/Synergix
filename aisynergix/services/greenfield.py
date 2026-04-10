import os
import time
import json
import hashlib
import urllib.parse
from datetime import datetime, timezone
import httpx
from eth_account import Account
from eth_account.messages import encode_defunct
from dotenv import load_dotenv

load_dotenv()

PRIVATE_KEY = os.getenv("PRIVATE_KEY")
BUCKET_NAME = os.getenv("BUCKET_NAME", "synergixai")
SP_URL = os.getenv("SP_URL", "https://greenfield-chain.bnbchain.org")
SALT = os.getenv("SALT", "synergix_ghost_protocol_v1")

if PRIVATE_KEY:
    account = Account.from_key(PRIVATE_KEY)
    WALLET_ADDRESS = account.address
else:
    account = None
    WALLET_ADDRESS = ""

def get_ghost_id(uid: str) -> str:
    """Aplica Hashing + Salting irreversible para garantizar privacidad Zero-Knowledge."""
    return hashlib.sha256(f"{uid}{SALT}".encode('utf-8')).hexdigest()

def _generate_v4_signature(method: str, path: str, headers: dict, payload: bytes) -> tuple[str, str]:
    """Genera firma ECDSA nativa V4 para BNB Greenfield."""
    if not account:
        return "", ""
    
    t = datetime.now(timezone.utc)
    amz_date = t.strftime('%Y%m%dT%H%M%SZ')
    datestamp = t.strftime('%Y%m%d')
    
    canonical_uri = urllib.parse.quote(path, safe='/~')
    canonical_querystring = ''
    
    domain = urllib.parse.urlparse(SP_URL).netloc
    canonical_headers = f"host:{domain}\nx-amz-date:{amz_date}\n"
    signed_headers = "host;x-amz-date"
    
    payload_hash = hashlib.sha256(payload).hexdigest()
    
    canonical_request = f"{method}\n{canonical_uri}\n{canonical_querystring}\n{canonical_headers}\n{signed_headers}\n{payload_hash}"
    
    algorithm = "AWS4-ECDSA-SHA256"
    credential_scope = f"{datestamp}/greenfield/s3/aws4_request"
    string_to_sign = f"{algorithm}\n{amz_date}\n{credential_scope}\n{hashlib.sha256(canonical_request.encode('utf-8')).hexdigest()}"
    
    signable_message = encode_defunct(text=string_to_sign)
    signed_message = Account.sign_message(signable_message, private_key=PRIVATE_KEY)
    
    signature = signed_message.signature.hex()
    
    authorization_header = f"{algorithm} Credential={WALLET_ADDRESS}/{credential_scope}, SignedHeaders={signed_headers}, Signature={signature}"
    return authorization_header, amz_date

async def get_user_metadata(uid: str) -> dict:
    """Obtiene los tags del archivo fantasma del usuario en 0 Bytes."""
    ghost_id = get_ghost_id(uid)
    path = f"/{BUCKET_NAME}/aisynergix/users/{ghost_id}"
    url = f"{SP_URL}{path}"
    
    domain = urllib.parse.urlparse(SP_URL).netloc
    headers = {"Host": domain}
    auth, amz_date = _generate_v4_signature("HEAD", path, headers, b"")
    if auth:
        headers["Authorization"] = auth
        headers["x-amz-date"] = amz_date
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.head(url, headers=headers)
            if response.status_code == 200:
                tags = response.headers.get("x-amz-meta-tags", "{}")
                if tags:
                    return json.loads(urllib.parse.unquote(tags))
            return None
        except Exception as e:
            print(f"[GF] Error HEAD metadata: {e}")
            return None

async def update_user_metadata(uid: str, updates: dict):
    """Actualiza los tags del archivo fantasma sin modificar su contenido."""
    ghost_id = get_ghost_id(uid)
    current = await get_user_metadata(uid) or {}
    current.update(updates)
    
    path = f"/{BUCKET_NAME}/aisynergix/users/{ghost_id}?tagging"
    url = f"{SP_URL}{path}"
    
    tags_str = urllib.parse.quote(json.dumps(current))
    payload = f"<Tagging><TagSet><Tag><Key>data</Key><Value>{tags_str}</Value></Tag></TagSet></Tagging>".encode('utf-8')
    
    domain = urllib.parse.urlparse(SP_URL).netloc
    headers = {
        "Host": domain,
        "Content-Type": "application/xml"
    }
    auth, amz_date = _generate_v4_signature("PUT", path, headers, payload)
    if auth:
        headers["Authorization"] = auth
        headers["x-amz-date"] = amz_date
    
    async with httpx.AsyncClient() as client:
        try:
            await client.put(url, headers=headers, content=payload)
        except Exception as e:
            print(f"[GF] Error PUT metadata: {e}")

async def upload_aporte(uid: str, content: str, tags: dict):
    """Sube un archivo de conocimiento asociándolo al hash del usuario."""
    ghost_id = get_ghost_id(uid)
    ts = int(time.time())
    month = datetime.now(timezone.utc).strftime('%Y-%m')
    path = f"/{BUCKET_NAME}/aisynergix/aportes/{month}/{ghost_id}_{ts}.txt"
    url = f"{SP_URL}{path}"
    
    payload = content.encode('utf-8')
    tags_str = urllib.parse.quote(json.dumps(tags))
    
    domain = urllib.parse.urlparse(SP_URL).netloc
    headers = {
        "Host": domain,
        "Content-Type": "text/plain",
        "x-amz-meta-tags": tags_str
    }
    
    auth, amz_date = _generate_v4_signature("PUT", path, headers, payload)
    if auth:
        headers["Authorization"] = auth
        headers["x-amz-date"] = amz_date
    
    async with httpx.AsyncClient() as client:
        try:
            await client.put(url, headers=headers, content=payload)
        except Exception as e:
            print(f"[GF] Error upload_aporte: {e}")

async def list_objects(prefix: str) -> list:
    """Lista objetos en el bucket para la consolidación del RAG."""
    path = f"/{BUCKET_NAME}/?prefix={urllib.parse.quote(prefix)}"
    url = f"{SP_URL}{path}"
    
    domain = urllib.parse.urlparse(SP_URL).netloc
    headers = {"Host": domain}
    auth, amz_date = _generate_v4_signature("GET", path, headers, b"")
    if auth:
        headers["Authorization"] = auth
        headers["x-amz-date"] = amz_date
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, headers=headers)
            if response.status_code == 200:
                # Aquí iría el parseo real del XML para obtener las keys de los objetos
                return [] 
            return []
        except Exception as e:
            print(f"[GF] Error list_objects: {e}")
            return []
