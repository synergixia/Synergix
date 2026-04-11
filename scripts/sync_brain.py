import asyncio
import os
import json
import urllib.parse
import httpx
from aisynergix.services.greenfield import SP_URL, BUCKET_NAME, _sign_v4

BRAIN_DIR = "aisynergix/data/brains"

async def download_file(path: str, local_path: str) -> bool:
    url = f"{SP_URL}{path}"
    domain = urllib.parse.urlparse(SP_URL).netloc
    headers = {"Host": domain}
    
    auth, date = _sign_v4("GET", path, headers)
    headers.update({"Authorization": auth, "x-amz-date": date})
    
    async with httpx.AsyncClient() as client:
        try:
            print(f"[SYNC] Descargando {path}...")
            resp = await client.get(url, headers=headers, timeout=120.0)
            if resp.status_code == 200:
                with open(local_path, "wb") as f:
                    f.write(resp.content)
                return True
        except Exception as e:
            print(f"[SYNC] Error de red: {e}")
    return False

async def get_latest_version_tag() -> str:
    path = f"/{BUCKET_NAME}/aisynergix/data/brain_pointer"
    headers = {"Host": urllib.parse.urlparse(SP_URL).netloc}
    auth, date = _sign_v4("HEAD", path, headers)
    headers.update({"Authorization": auth, "x-amz-date": date})
    
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.head(f"{SP_URL}{path}", headers=headers, timeout=15.0)
            if resp.status_code == 200:
                tags_raw = resp.headers.get("x-amz-meta-tags", "{}")
                try:
                    tags = json.loads(urllib.parse.unquote(tags_raw))
                    return tags.get("latest_v", "v0")
                except: return "v0"
        except: pass
    return "v0"

async def run_sync():
    print("🧬 Resucitando Memoria Inmortal desde BNB Greenfield...")
    os.makedirs(BRAIN_DIR, exist_ok=True)
    
    version = await get_latest_version_tag()
    if version == "v0":
        print("[SYNC] Nodo Virgen. Se creará memoria tras el primer aporte validado.")
        return

    remote_txt = f"/{BUCKET_NAME}/aisynergix/data/brains/Synergix_ia_{version}.txt"
    remote_idx = f"/{BUCKET_NAME}/aisynergix/data/brains/Synergix_ia_{version}.index"
    
    txt_ok = await download_file(remote_txt, f"{BRAIN_DIR}/Synergix_ia.txt")
    idx_ok = await download_file(remote_idx, f"{BRAIN_DIR}/Synergix_ia.index")
    
    if txt_ok and idx_ok: print(f"✅ Sincronización completa ({version}).")
    else: print("⚠️ Advertencia: Error parcial. Operando con caché local si existe.")

if __name__ == "__main__":
    asyncio.run(run_sync())
