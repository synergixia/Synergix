import logging
import faiss
import numpy as np
import json
import os
from datetime import datetime
from sentence_transformers import SentenceTransformer
from aisynergix.services.greenfield import greenfield
from aisynergix.services.rag_engine import rag_engine

logger = logging.getLogger("FusionBrain")
EMBEDDING_MODEL = 'all-MiniLM-L6-v2'

async def fusion_loop():
    try:
        # En Greenfield puro, implementaríamos un paginador XML aquí para el prefijo de mes.
        # Simulamos la extracción de los objetos recientes:
        nuevos_aportes = [] # <- Aquí el cliente list_objects debe poblar los diccionarios {content, author_uid}
        
        if not nuevos_aportes:
            return False

        logger.info(f"Vectorizando {len(nuevos_aportes)} conocimientos en ARM64...")
        embedder = SentenceTransformer(EMBEDDING_MODEL, device='cpu')
        textos = [ap['content'] for ap in nuevos_aportes]
        vectores = embedder.encode(textos).astype(np.float32)

        dimension = vectores.shape[1]
        index = faiss.IndexFlatL2(dimension)
        index.add(vectores)

        version_id = f"v{int(datetime.now().timestamp())}"
        index_path = f"/tmp/{version_id}.index"
        meta_path = f"/tmp/{version_id}.json"
        
        faiss.write_index(index, index_path)
        with open(meta_path, 'w', encoding='utf-8') as f:
            json.dump(nuevos_aportes, f)

        # Upload atómico
        with open(index_path, 'rb') as f:
            await greenfield.put_object(f"aisynergix/data/brains/{version_id}.index", f.read())
        with open(meta_path, 'rb') as f:
            await greenfield.put_object(f"aisynergix/data/brains/{version_id}.json", f.read())

        # Actualiza el puntero de verdad global
        await greenfield.update_user_metadata("brain_pointer", {"latest_v": version_id}) 
        
        # Recarga RAM
        rag_engine.hot_reload(index_path, meta_path)
        
        os.remove(index_path)
        os.remove(meta_path)
        logger.info(f"🧬 Evolución FAISS completada: {version_id}.")
        return True
    except Exception as e:
        logger.error(f"Fallo en Fusión: {e}")
        return False
