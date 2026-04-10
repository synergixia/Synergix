import asyncio
import os
import faiss
import json
import urllib.parse
import numpy as np
import httpx
from datetime import datetime, timezone
from sentence_transformers import SentenceTransformer
from aisynergix.services.greenfield import SP_URL, BUCKET_NAME, _sign_v4, list_objects

BRAIN_DIR = "aisynergix/data/brains"
EMBEDDING_MODEL = 'all-MiniLM-L6-v2'
TRACK_FILE = "aisynergix/data/last_fusion.json"

async def fetch_object_content(path: str) -> tuple[str, dict]:
    url = f"{SP_URL}{path}"
    headers = {"Host": urllib.parse.urlparse(SP_URL).netloc}
    auth, date = _sign_v4("GET", path, headers)
    headers.update({"Authorization": auth, "x-amz-date": date})
    
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(url, headers=headers, timeout=20.0)
            if resp.status_code == 200:
                tags_raw = resp.headers.get("x-amz-meta-tags", "{}")
                tags = json.loads(urllib.parse.unquote(tags_raw)) if tags_raw else {}
                return resp.text, tags
        except Exception: pass
    return "", {}

async def update_brain_pointer(version: str):
    path = f"/{BUCKET_NAME}/aisynergix/data/brain_pointer?tagging"
    tags_json = urllib.parse.quote(json.dumps({"latest_v": version}))
    payload = f"<Tagging><TagSet><Tag><Key>data</Key><Value>{tags_json}</Value></Tag></TagSet></Tagging>".encode()
    headers = {"Host": urllib.parse.urlparse(SP_URL).netloc, "Content-Type": "application/xml"}
    auth, date = _sign_v4("PUT", path, headers, payload)
    headers.update({"Authorization": auth, "x-amz-date": date})
    
    async with httpx.AsyncClient() as client:
        await client.put(f"{SP_URL}{path}", headers=headers, content=payload)

async def fusion_brain() -> bool:
    os.makedirs(BRAIN_DIR, exist_ok=True)
    os.makedirs(os.path.dirname(TRACK_FILE), exist_ok=True)
    
    processed_files = set()
    if os.path.exists(TRACK_FILE):
        with open(TRACK_FILE, "r") as f:
            processed_files = set(json.load(f))
            
    month = datetime.now(timezone.utc).strftime('%Y-%m')
    prefix = f"aisynergix/aportes/{month}/"
    
    all_objects = await list_objects(prefix)
    new_objects = [obj for obj in all_objects if obj not in processed_files]
    
    if not new_objects: return False
        
    print(f"[FUSION] Evaluando {len(new_objects)} aportes nuevos.")
    textos_aprobados = []
    
    if os.path.exists(f"{BRAIN_DIR}/Synergix_ia.txt"):
        with open(f"{BRAIN_DIR}/Synergix_ia.txt", "r", encoding="utf-8") as f:
            textos_aprobados = [line.strip() for line in f.readlines()]
    
    added_count = 0
    for obj_key in new_objects:
        content, tags = await fetch_object_content(f"/{BUCKET_NAME}/{obj_key}")
        score = float(tags.get("score", 0))
        if score > 7.0 and content:
            textos_aprobados.append(content.replace("\n", " "))
            added_count += 1
            processed_files.add(obj_key)
            
    if added_count == 0:
        with open(TRACK_FILE, "w") as f: json.dump(list(processed_files), f)
        return False
        
    print(f"[FUSION] Generando índices FAISS para {len(textos_aprobados)} conocimientos...")
    embedder = SentenceTransformer(EMBEDDING_MODEL)
    vectors = embedder.encode(textos_aprobados).astype(np.float32)
    
    index = faiss.IndexFlatL2(vectors.shape[1])
    index.add(vectors)
    
    ts_version = f"v{int(datetime.now().timestamp())}"
    
    faiss.write_index(index, f"{BRAIN_DIR}/Synergix_ia.index")
    with open(f"{BRAIN_DIR}/Synergix_ia.txt", "w", encoding="utf-8") as f:
        for t in textos_aprobados: f.write(t + "\n")
        
    with open(TRACK_FILE, "w") as f:
        json.dump(list(processed_files), f)
        
    await update_brain_pointer(ts_version)
    print(f"[FUSION] Memoria Inmortal consolidada exitosamente en {ts_version}.")
    return True

if __name__ == "__main__":
    asyncio.run(fusion_brain())
