import asyncio
import os
import faiss
import json
import urllib.parse
import hashlib
import numpy as np
import httpx
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from sentence_transformers import SentenceTransformer
from aisynergix.services.greenfield import SP_URL, BUCKET_NAME, _generate_v4_signature

BRAIN_DIR = "aisynergix/data/brains"
EMBEDDING_MODEL = 'all-MiniLM-L6-v2'

async def fetch_object_content(path: str) -> tuple[str, dict]:
    """Obtiene el contenido de un aporte y sus metadatos (Tags)."""
    url = f"{SP_URL}{path}"
    domain = urllib.parse.urlparse(SP_URL).netloc
    headers = {"Host": domain}
    auth, amz_date = _generate_v4_signature("GET", path, headers, b"")
    if auth:
        headers["Authorization"] = auth
        headers["x-amz-date"] = amz_date
    
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                tags_raw = resp.headers.get("x-amz-meta-tags", "{}")
                tags = json.loads(urllib.parse.unquote(tags_raw)) if tags_raw else {}
                return resp.text, tags
        except Exception as e:
            print(f"[FUSION] Error leyendo {path}: {e}")
    return "", {}

async def list_aportes(prefix: str) -> list:
    """Lista objetos parseando la respuesta XML de Greenfield S3."""
    path = f"/{BUCKET_NAME}/?prefix={urllib.parse.quote(prefix)}"
    url = f"{SP_URL}{path}"
    domain = urllib.parse.urlparse(SP_URL).netloc
    headers = {"Host": domain}
    auth, amz_date = _generate_v4_signature("GET", path, headers, b"")
    if auth:
        headers["Authorization"] = auth
        headers["x-amz-date"] = amz_date
    
    keys = []
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                root = ET.fromstring(resp.text)
                # Parsear XML de S3 (namespace suele ser necesario dependiendo de la config)
                for contents in root.findall('.//{http://s3.amazonaws.com/doc/2006-03-01/}Contents') or root.findall('Contents'):
                    key = contents.find('{http://s3.amazonaws.com/doc/2006-03-01/}Key')
                    if key is None:
                        key = contents.find('Key')
                    if key is not None:
                        keys.append(key.text)
        except Exception as e:
            print(f"[FUSION] Error listando aportes: {e}")
    return keys

async def fusion_brain():
    print("[FUSION] Iniciando consolidación de aportes (El Legado)...")
    os.makedirs(BRAIN_DIR, exist_ok=True)
    
    month = datetime.now(timezone.utc).strftime('%Y-%m')
    prefix = f"aisynergix/aportes/{month}/"
    
    objects = await list_aportes(prefix)
    print(f"[FUSION] Encontrados {len(objects)} aportes este mes.")
    
    textos_aprobados = []
    
    for obj_key in objects:
        content, tags = await fetch_object_content(f"/{BUCKET_NAME}/{obj_key}")
        score = float(tags.get("score", 0))
        # Filtro de Calidad: Solo integramos aportes de alto valor
        if score > 7.0 and content:
            textos_aprobados.append(content)
    
    if not textos_aprobados:
        print("[FUSION] No hay aportes de alta calidad suficientes para fusionar.")
        return

    print(f"[FUSION] Vectorizando {len(textos_aprobados)} conocimientos...")
    embedder = SentenceTransformer(EMBEDDING_MODEL)
    vectors = embedder.encode(textos_aprobados).astype(np.float32)
    
    dimension = vectors.shape[1]
    index = faiss.IndexFlatL2(dimension)
    index.add(vectors)
    
    ts = int(datetime.now().timestamp())
    version = f"v{ts}"
    index_path = f"{BRAIN_DIR}/Synergix_ia_{version}.index"
    txt_path = f"{BRAIN_DIR}/Synergix_ia_{version}.txt"
    
    # Guardar localmente
    faiss.write_index(index, index_path)
    with open(txt_path, "w", encoding="utf-8") as f:
        for text in textos_aprobados:
            f.write(text.replace("\n", " ") + "\n")
            
    print(f"[FUSION] Nuevo cerebro {version} consolidado. Listo para publicación.")
    # Nota: Aquí se implementaría el código para subir index_path y txt_path 
    # de vuelta a aisynergix/data/brains/ en Greenfield y actualizar el brain_pointer.

if __name__ == "__main__":
    asyncio.run(fusion_brain())
