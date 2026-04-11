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
                tags_raw = resp.headers.get("x-amz-tagging", "")
                tags = {k: v[0] for k, v in urllib.parse.parse_qs(tags_raw).items()}
                return resp.text, tags
        except: pass
    return "", {}

async def update_brain_pointer(version: str):
    path = f"/{BUCKET_NAME}/aisynergix/data/brain_pointer"
    domain = urllib.parse.urlparse(SP_URL).netloc
    
    h_put = {"Host": domain, "Content-Type": "application/octet-stream"}
    auth_p, date_p = _sign_v4("PUT", path, h_put, b"")
    h_put.update({"Authorization": auth_p, "x-amz-date": date_p})
    
    async with httpx.AsyncClient() as client:
        try: await client.put(f"{SP_URL}{path}", headers=h_put, content=b"", timeout=15.0)
        except: pass

        tag_path = f"{path}?tagging"
        tags_json = urllib.parse.quote(json.dumps({"latest_v": version}))
        xml_tag = f'<Tagging xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><TagSet><Tag><Key>data</Key><Value>{tags_json}</Value></Tag></TagSet></Tagging>'
        payload = xml_tag.encode('utf-8')
        
        h_tag = {"Host": domain, "Content-Type": "application/xml"}
        auth_t, date_t = _sign_v4("PUT", tag_path, h_tag, payload)
        h_tag.update({"Authorization": auth_t, "x-amz-date": date_t})
        await client.put(f"{SP_URL}{tag_path}", headers=h_tag, content=payload)

async def fusion_brain() -> bool:
    os.makedirs(BRAIN_DIR, exist_ok=True)
    os.makedirs(os.path.dirname(TRACK_FILE), exist_ok=True)
    
    processed = set()
    if os.path.exists(TRACK_FILE):
        with open(TRACK_FILE, "r") as f: processed = set(json.load(f))
            
    month = datetime.now(timezone.utc).strftime('%Y-%m')
    prefix = f"aisynergix/aportes/{month}/"
    all_objs = await list_objects(prefix)
    new_objs = [o for o in all_objs if o not in processed]
    
    if not new_objs: return False
        
    print(f"[FUSION] Evaluando {len(new_objs)} fragmentos en DCellar.")
    current_texts = []
    if os.path.exists(f"{BRAIN_DIR}/Synergix_ia.txt"):
        with open(f"{BRAIN_DIR}/Synergix_ia.txt", "r", encoding="utf-8") as f:
            current_texts = [line.strip() for line in f.readlines()]
            
    found_valid = 0
    for key in new_objs:
        content, tags = await fetch_object_content(f"/{BUCKET_NAME}/{key}")
        if content and float(tags.get("score", 0)) >= 5.0:
            current_texts.append(content.replace("\n", " "))
            found_valid += 1
        processed.add(key)
        
    if found_valid == 0:
        with open(TRACK_FILE, "w") as f: json.dump(list(processed), f)
        return False
        
    print(f"[FUSION] Vectorizando {len(current_texts)} conocimientos...")
    embedder = SentenceTransformer(EMBEDDING_MODEL)
    vectors = embedder.encode(current_texts).astype(np.float32)
    index = faiss.IndexFlatL2(vectors.shape[1])
    index.add(vectors)
    
    ts_version = f"v{int(datetime.now().timestamp())}"
    faiss.write_index(index, f"{BRAIN_DIR}/Synergix_ia.index")
    with open(f"{BRAIN_DIR}/Synergix_ia.txt", "w", encoding="utf-8") as f:
        for t in current_texts: f.write(t + "\n")
        
    await update_brain_pointer(ts_version)
    with open(TRACK_FILE, "w") as f: json.dump(list(processed), f)
    
    print(f"✅ Memoria consolidada en la versión: {ts_version}")
    return True

if __name__ == "__main__":
    asyncio.run(fusion_brain())
