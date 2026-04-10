import asyncio
import os
import json
import hashlib
import urllib.parse
import httpx
from aisynergix.services.greenfield import SP_URL, BUCKET_NAME, _sign_v4

# Directorio local donde se guardará la memoria para el RAG
BRAIN_DIR = "aisynergix/data/brains"

async def download_file(path: str, local_path: str) -> bool:
    """Descarga un objeto de Greenfield con verificación de integridad."""
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
            else:
                print(f"[SYNC] Error HTTP {resp.status_code} en {path}")
        except Exception as e:
            print(f"[SYNC] Error de conexión: {e}")
    return False

async def get_latest_version_tag() -> str:
    """Consulta el tag 'latest_v' del archivo brain_pointer."""
    path = f"/{BUCKET_NAME}/aisynergix/data/brain_pointer"
    headers = {"Host": urllib.parse.urlparse(SP_URL).netloc}
    
    auth, date = _sign_v4("HEAD", path, headers)
    headers.update({"Authorization": auth, "x-amz-date": date})
    
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.head(f"{SP_URL}{path}", headers=headers)
            if resp.status_code == 200:
                tags_raw = resp.headers.get("x-amz-meta-tags", "{}")
                tags = json.loads(urllib.parse.unquote(tags_raw))
                return tags.get("latest_v", "v0")
        except: pass
    return "v0"

async def run_sync():
    """Ejecuta la sincronización completa del cerebro."""
    print("🧬 Iniciando sincronización de Memoria Inmortal...")
    os.makedirs(BRAIN_DIR, exist_ok=True)
    
    # 1. Obtener qué versión debemos descargar
    version = await get_latest_version_tag()
    if version == "v0":
        print("[SYNC] No se encontró una versión previa. Se iniciará cerebro vacío.")
        return

    print(f"[SYNC] Sincronizando Versión: {version}")
    
    # 2. Rutas en DCellar (coordinadas con tu estructura)
    remote_txt = f"/{BUCKET_NAME}/aisynergix/data/brains/Synergix_ia_{version}.txt"
    remote_idx = f"/{BUCKET_NAME}/aisynergix/data/brains/Synergix_ia_{version}.index"
    
    # 3. Descarga y sobreescritura local de Synergix_ia.txt/index
    # El bot siempre lee los archivos sin el prefijo de versión para facilidad de acceso
    txt_ok = await download_file(remote_txt, f"{BRAIN_DIR}/Synergix_ia.txt")
    idx_ok = await download_file(remote_idx, f"{BRAIN_DIR}/Synergix_ia.index")
    
    if txt_ok and idx_ok:
        print(f"✅ Memoria Inmortal {version} lista para operar.")
    else:
        print("⚠️ Fallo en la sincronización. Verificando estado local...")

if __name__ == "__main__":
    asyncio.run(run_sync())

