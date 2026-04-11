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
SALT = os.getenv("SALT", "default_salt_synergix")
BUCKET = os.getenv("BUCKET_NAME", "synergixai")
SP_URL = os.getenv("SP_URL", "https://greenfield-chain.bnbchain.org")

account = Account.from_key(PRIVATE_KEY) if PRIVATE_KEY else None
ADDRESS = account.address if account else ""

def get_ghost_id(uid: str) -> str:
    """GHOST PROTOCOL: Convierte el ID de Telegram en un Hash irreversible para anonimato on-chain."""
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
    """Consulta los tags actuales sin modificar ni sobrescribir el objeto 0-bytes."""
    gid = get_ghost_id(uid)
    path = f"/{BUCKET}/aisynergix/users/{gid}"
    domain = urllib.parse.urlparse(SP_URL).netloc
    
    async with httpx.AsyncClient() as client:
        # 1. Verificar existencia del objeto Ghost
        headers_head = {"Host": domain}
        auth_h, date_h = _sign_v4("HEAD", path, headers_head)
        headers_head.update({"Authorization": auth_h, "x-amz-date": date_h})
        try:
            r_head = await client.head(f"{SP_URL}{path}", headers=headers_head, timeout=10.0)
            if r_head.status_code != 200: return None
            
            # 2. Descargar XML de Tags reales desde DCellar
            tag_path = f"{path}?tagging"
            auth_t, date_t = _sign_v4("GET", tag_path, {"Host": domain})
            r_tags = await client.get(f"{SP_URL}{tag_path}", headers={"Host": domain, "Authorization": auth_t, "x-amz-date": date_t}, timeout=10.0)
            
            if r_tags.status_code == 200:
                root = ET.fromstring(r_tags.text)
                tags = {}
                for tag in root.findall('.//{http://s3.amazonaws.com/doc/2006-03-01/}Tag') or root.findall('.//Tag'):
                    k = tag.find('{http://s3.amazonaws.com/doc/2006-03-01/}Key') or tag.find('Key')
                    v = tag.find('{http://s3.amazonaws.com/doc/2006-03-01/}Value') or tag.find('Value')
                    if k is not None and v is not None: tags[k.text] = v.text
                return tags
        except: pass
    return None

async def update_user_metadata(uid: str, updates: dict):
    """Suma Atómica: Mezcla tags nuevos con el historial antiguo y lo sube como XML a Greenfield."""
    gid = get_ghost_id(uid)
    path = f"/{BUCKET}/aisynergix/users/{gid}"
    domain = urllib.parse.urlparse(SP_URL).netloc
    current = await get_user_metadata(uid)
    
    async with httpx.AsyncClient() as client:
        # Crear archivo 0-bytes SOLAMENTE si el usuario es totalmente nuevo (evita borrar historial)
        if current is None:
            headers_put = {"Host": domain, "Content-Type": "application/octet-stream"}
            auth_p, date_p = _sign_v4("PUT", path, headers_put, b"")
            headers_put.update({"Authorization": auth_p, "x-amz-date": date_p})
            await client.put(f"{SP_URL}{path}", headers=headers_put, content=b"")
            current = {}

        current.update(updates)
        
        # Inyección de XML Tagging nativo compatible con Greenfield/S3
        tag_path = f"{path}?tagging"
        xml_body = '<Tagging xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><TagSet>'
        for k, v in current.items():
            xml_body += f'<Tag><Key>{k}</Key><Value>{str(v)}</Value></Tag>'
        xml_body += '</TagSet></Tagging>'
        
        payload = xml_body.encode('utf-8')
        headers_tag = {"Host": domain, "Content-Type": "application/xml"}
        auth_t, date_t = _sign_v4("PUT", tag_path, headers_tag, payload)
        headers_tag.update({"Authorization": auth_t, "x-amz-date": date_t})
        await client.put(f"{SP_URL}{tag_path}", headers=headers_tag, content=payload)

async def upload_aporte(uid: str, content: str, tags: dict):
    """Subida de fragmentos de conocimiento crudo (.txt) con tags de puntuación."""
    gid = get_ghost_id(uid)
    month = datetime.now(timezone.utc).strftime('%Y-%m')
    ts = int(time.time())
    path = f"/{BUCKET}/aisynergix/aportes/{month}/{gid}_{ts}.txt"
    payload = content.encode('utf-8')
    domain = urllib.parse.urlparse(SP_URL).netloc
    
    tag_str = "&".join([f"{k}={v}" for k, v in tags.items()])
    headers = {
        "Host": domain,
        "Content-Type": "text/plain",
        "x-amz-tagging": tag_str
    }
    
    auth, date = _sign_v4("PUT", path, headers, payload)
    headers.update({"Authorization": auth, "x-amz-date": date})
    
    async with httpx.AsyncClient() as client:
        await client.put(f"{SP_URL}{path}", headers=headers, content=payload, timeout=30.0)

async def list_objects(prefix: str) -> list:
    """Busca en el bucket las listas de objetos para los demonios de auditoría y evolución."""
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
                ns = {'s3': 'http://s3.amazonaws.com/doc/2006-03-01/'}
                for contents in root.findall('.//s3:Contents', ns) or root.findall('.//Contents'):
                    key = contents.find('s3:Key', ns) or contents.find('Key')
                    if key is not None: keys.append(key.text)
        except: pass
    return keys
