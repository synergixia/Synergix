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
import xml.etree.ElementTree as ET

load_dotenv()

PRIVATE_KEY = os.getenv("PRIVATE_KEY")
SALT = os.getenv("SALT", "synergix_ghost_protocol_v1_super_secret")
BUCKET = os.getenv("BUCKET_NAME", "synergixai")
SP_URL = os.getenv("SP_URL", "https://greenfield-chain.bnbchain.org")

if PRIVATE_KEY:
    account = Account.from_key(PRIVATE_KEY)
    ADDRESS = account.address
else:
    account = None
    ADDRESS = ""

def get_ghost_id(uid: str) -> str:
    """GHOST PROTOCOL: Convierte el ID de Telegram en un Hash irreversible (Privacidad Total)."""
    return hashlib.sha256(f"{uid}{SALT}".encode('utf-8')).hexdigest()

def _sign_v4(method, path, headers, payload=b""):
    """Firma nativa ECDSA V4 para BNB Greenfield."""
    if not account: return "", ""
    t = datetime.now(timezone.utc)
    amz_date = t.strftime('%Y%m%dT%H%M%SZ')
    datestamp = t.strftime('%Y%m%d')
    domain = urllib.parse.urlparse(SP_URL).netloc
    
    canonical_uri = urllib.parse.quote(path, safe='/~')
    canonical_headers = f"host:{domain}\nx-amz-date:{amz_date}\n"
    signed_headers = "host;x-amz-date"
    payload_hash = hashlib.sha256(payload).hexdigest()
    
    canonical_request = f"{method}\n{canonical_uri}\n\n{canonical_headers}\n{signed_headers}\n{payload_hash}"
    scope = f"{datestamp}/greenfield/s3/aws4_request"
    string_to_sign = f"AWS4-ECDSA-SHA256\n{amz_date}\n{scope}\n{hashlib.sha256(canonical_request.encode()).hexdigest()}"
    
    signature = account.sign_message(encode_defunct(text=string_to_sign)).signature.hex()
    auth = f"AWS4-ECDSA-SHA256 Credential={ADDRESS}/{scope}, SignedHeaders={signed_headers}, Signature={signature}"
    return auth, amz_date

async def get_user_metadata(uid: str):
    """Accede a aisynergix/users/{ghost_id} (0 Bytes)."""
    gid = get_ghost_id(uid)
    path = f"/{BUCKET}/aisynergix/users/{gid}"
    headers = {"Host": urllib.parse.urlparse(SP_URL).netloc}
    auth, date = _sign_v4("HEAD", path, headers)
    headers.update({"Authorization": auth, "x-amz-date": date})
    
    async with httpx.AsyncClient() as client:
        try:
            r = await client.head(f"{SP_URL}{path}", headers=headers, timeout=10.0)
            if r.status_code == 200:
                tags = r.headers.get("x-amz-meta-tags", "{}")
                return json.loads(urllib.parse.unquote(tags))
        except: pass
    return None

async def update_user_metadata(uid: str, updates: dict):
    """Actualiza los Tags en la ruta de Identidades Fantasma."""
    gid = get_ghost_id(uid)
    current = await get_user_metadata(uid) or {}
    current.update(updates)
    
    path = f"/{BUCKET}/aisynergix/users/{gid}?tagging"
    tags_json = urllib.parse.quote(json.dumps(current))
    payload = f"<Tagging><TagSet><Tag><Key>data</Key><Value>{tags_json}</Value></Tag></TagSet></Tagging>".encode()
    
    headers = {"Host": urllib.parse.urlparse(SP_URL).netloc, "Content-Type": "application/xml"}
    auth, date = _sign_v4("PUT", path, headers, payload)
    headers.update({"Authorization": auth, "x-amz-date": date})
    
    async with httpx.AsyncClient() as client:
        try:
            await client.put(f"{SP_URL}{path}", headers=headers, content=payload, timeout=15.0)
        except Exception as e:
            print(f"[Greenfield] Error actualizando metadatos: {e}")

async def upload_aporte(uid: str, content: str, tags: dict):
    """Sube el Legado a aisynergix/aportes/YYYY-MM/"""
    gid = get_ghost_id(uid)
    month = datetime.now(timezone.utc).strftime('%Y-%m')
    ts = int(time.time())
    path = f"/{BUCKET}/aisynergix/aportes/{month}/{gid}_{ts}.txt"
    payload = content.encode('utf-8')
    
    headers = {
        "Host": urllib.parse.urlparse(SP_URL).netloc,
        "Content-Type": "text/plain",
        "x-amz-meta-tags": urllib.parse.quote(json.dumps(tags))
    }
    auth, date = _sign_v4("PUT", path, headers, payload)
    headers.update({"Authorization": auth, "x-amz-date": date})
    
    async with httpx.AsyncClient() as client:
        try:
            await client.put(f"{SP_URL}{path}", headers=headers, content=payload, timeout=30.0)
        except Exception as e:
            print(f"[Greenfield] Error subiendo aporte: {e}")

async def list_objects(prefix: str) -> list:
    """Lista objetos parseando XML para Evolución y Auditoría."""
    path = f"/{BUCKET}/?prefix={urllib.parse.quote(prefix)}"
    headers = {"Host": urllib.parse.urlparse(SP_URL).netloc}
    auth, date = _sign_v4("GET", path, headers)
    headers.update({"Authorization": auth, "x-amz-date": date})
    
    keys = []
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(f"{SP_URL}{path}", headers=headers, timeout=20.0)
            if resp.status_code == 200:
                root = ET.fromstring(resp.text)
                for contents in root.findall('.//{http://s3.amazonaws.com/doc/2006-03-01/}Contents') or root.findall('Contents'):
                    key = contents.find('{http://s3.amazonaws.com/doc/2006-03-01/}Key')
                    if key is None: key = contents.find('Key')
                    if key is not None: keys.append(key.text)
        except Exception as e:
            print(f"[Greenfield] Error listando objetos: {e}")
    return keys
