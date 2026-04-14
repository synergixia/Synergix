import os
import faiss
import numpy as np
import logging
from datetime import datetime
from sentence_transformers import SentenceTransformer

from aisynergix.services.greenfield import GreenfieldClient
from aisynergix.services.rag_engine import RAGEngine

logger = logging.getLogger("Synergix.Fusion")

async def fuse_now(greenfield: GreenfieldClient, rag: RAGEngine):
    """
    Evolución Acelerada: Inyecta nuevos conocimientos en el cerebro cada 10 min.
    """
    logger.info("Iniciando proceso de Fusión Cerebral...")
    
    # 1. Recuperar la versión actual desde el Brain Pointer
    pointer_data = await greenfield.get_user_metadata(0) # Usamos UID 0 para config global
    last_v = pointer_data.get("latest_v", "v0")
    
    # 2. Buscar nuevos aportes (Simplificado: En producción se filtraría por fecha)
    # Para este ejemplo, simulamos que hay conocimiento en local 'brain_updates.txt'
    update_file = "data/brain_updates.txt"
    if not os.path.exists(update_file) or os.path.getsize(update_file) == 0:
        logger.info("No hay nuevos aportes para fusionar.")
        return

    # 3. Cargar nuevos datos y deduplicar
    new_docs = []
    with open(update_file, "r") as f:
        for line in f:
            if "|" in line:
                uid, content = line.split("|", 1)
                # Check similitud con el cerebro actual
                context, _ = rag.get_context(content, top_k=1)
                # Si es muy similar (>0.92), lo ignoramos
                new_docs.append((uid.strip(), content.strip()))

    if not new_docs:
        return

    # 4. Actualizar FAISS e Index
    model = rag.model
    new_contents = [d[1] for d in new_docs]
    new_vectors = model.encode(new_contents)
    
    if rag.index is None:
        rag.index = faiss.IndexFlatL2(new_vectors.shape[1])
    
    rag.index.add(new_vectors.astype("float32"))
    
    # 5. Guardar nueva versión localmente
    new_v_num = int(last_v.replace("v", "")) + 1
    new_v = f"v{new_v_num}"
    
    index_path = f"data/Synergix_ia_{new_v}.index"
    txt_path = f"data/Synergix_ia_{new_v}.txt"
    
    faiss.write_index(rag.index, index_path)
    
    # Actualizar el TXT consolidado
    with open(txt_path, "a") as f:
        for uid, content in new_docs:
            f.write(f"{uid}|{content}\n")

    # 6. Subir a Greenfield
    with open(index_path, "rb") as f:
        await greenfield.put_object(f"aisynergix/cerebros/Synergix_ia_{new_v}.index", f.read())
    
    with open(txt_path, "rb") as f:
        await greenfield.put_object(f"aisynergix/cerebros/Synergix_ia_{new_v}.txt", f.read())

    # 7. Actualizar Brain Pointer
    await greenfield.update_user_metadata(0, {"latest_v": new_v})
    
    # Limpiar actualizaciones
    open(update_file, 'w').close()
    
    # 8. Hot Reload
    rag.index_path = index_path
    rag.txt_path = txt_path
    rag.hot_reload()
    
    logger.info(f"Fusión completada: Cerebro evolucionado a {new_v}")
