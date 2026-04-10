import asyncio
import os
import hashlib
import urllib.parse
from datetime import datetime, timezone
import httpx
from aisynergix.services.greenfield import SP_URL, BUCKET_NAME, _generate_v4_signature

BRAIN_DIR = "aisynergix/data/brains"

async def download_and_verify(path: str, save_path: str, expected_hash: str = None) -> bool:
    """Descarga un archivo desde Greenfield usando firmas V4 y verifica su SHA-256."""
    url = f"{SP_URL}{path}"
    domain = urllib.parse.urlparse(SP_URL).netloc
    headers = {"Host": domain}
    auth, amz_date = _generate_v4_signature("GET", path, headers, b"")
    if auth:
        headers["Authorization"] = auth
        headers["x-amz-date"] = amz_date
    
    async with httpx.AsyncClient() as client:
        try:
            print(f"[SYNC] Descargando {path}...")
            response = await client.get(url, headers=headers, timeout=60.0)
            if response.status_code == 200:
                content = response.content
                
                # Verificación de integridad si se provee el hash esperado
                if expected_hash:
                    file_hash = hashlib.sha256(content).hexdigest()
                    if file_hash != expected_hash:
                        print(f"[SYNC] Error de integridad en {path}. Hash mismatch.")
                        return False
                        
                with open(save_path, "wb") as f:
                    f.write(content)
                return True
            else:
                print(f"[SYNC] Fallo al descargar {path} - HTTP {response.status_code}")
        except Exception as e:
            print(f"[SYNC] Excepción descargando {path}: {e}")
    return False

async def sync_brain():
    print("[SYNC] Iniciando secuencia de ignición del Nodo Fantasma...")
    os.makedirs(BRAIN_DIR, exist_ok=True)
    
    pointer_path = f"/{BUCKET_NAME}/aisynergix/data/brain_pointer"
    pointer_url = f"{SP_URL}{pointer_path}"
    
    domain = urllib.parse.urlparse(SP_URL).netloc
    headers = {"Host": domain}
    auth, amz_date = _generate_v4_signature("HEAD", pointer_path, headers, b"")
    if auth:
        headers["Authorization"] = auth
        headers["x-amz-date"] = amz_date
    
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.head(pointer_url, headers=headers)
            if resp.status_code == 200:
                tags_raw = resp.headers.get("x-amz-meta-tags", "{}")
                tags = json.loads(urllib.parse.unquote(tags_raw)) if tags_raw else {}
                version = tags.get("latest_v", "v0")
                txt_hash = tags.get("txt_hash", None)
                idx_hash = tags.get("idx_hash", None)
                
                print(f"[SYNC] Puntero detectado: Versión {version}")
                
                txt_path = f"/{BUCKET_NAME}/aisynergix/data/brains/Synergix_ia_{version}.txt"
                idx_path = f"/{BUCKET_NAME}/aisynergix/data/brains/Synergix_ia_{version}.index"
                
                txt_saved = await download_and_verify(txt_path, f"{BRAIN_DIR}/Synergix_ia.txt", txt_hash)
                idx_saved = await download_and_verify(idx_path, f"{BRAIN_DIR}/Synergix_ia.index", idx_hash)
                
                if txt_saved and idx_saved:
                    print("[SYNC] Descarga completada y verificada. El cerebro está listo.")
                else:
                    print("[SYNC] Advertencia: No se pudo verificar la descarga. Se usará el estado local si existe.")
            else:
                print("[SYNC] Puntero no encontrado en Greenfield. Se iniciará un cerebro en blanco.")
    except Exception as e:
        print(f"[SYNC] Error de conexión general: {e}")

if __name__ == "__main__":
    import json
    asyncio.run(sync_brain())
