"""
fusion_brain.py — Tarea de 10m de Evolución del Cerebro.
Filtra aportes de Greenfield con calidad > 7 y reconstruye el índice FAISS.
Sube el nuevo índice a DCellar (Greenfield) y actualiza el Brain Pointer.
"""

import os
import json
import logging
import asyncio
from typing import List, Dict, Any

from aisynergix.services.greenfield import (
    list_objects,
    get_object,
    put_object,
)
from aisynergix.services.rag_engine import rag_engine
from aisynergix.config.constants import (
    APORTES_PREFIX,
    BRAIN_PREFIX,
    BRAIN_POINTER_OBJECT,
    LOCAL_BRAIN_DIR,
    LOCAL_INDEX_FILE,
    LOCAL_INDEX_META,
    RAG_MIN_QUALITY_SCORE
)

logger = logging.getLogger(__name__)

async def run_fusion():
    """Ejecuta el ciclo de fusión cerebral."""
    logger.info("[Fusion] Iniciando ciclo de fusión de conocimiento...")

    # 1. Listar todos los aportes en el bucket
    all_aportes_keys = await list_objects(APORTES_PREFIX)
    if not all_aportes_keys:
        logger.info("[Fusion] No se encontraron aportes en Greenfield.")
        return

    high_quality_data = []

    # 2. Filtrar aportes por calidad > 7
    # Nota: Los aportes se guardan como JSON con metadata.score
    for key in all_aportes_keys:
        try:
            raw_data = await get_object(key)
            if not raw_data:
                continue
            
            aporte_json = json.loads(raw_data.decode('utf-8'))
            content = aporte_json.get("content")
            metadata = aporte_json.get("metadata", {})
            score = float(metadata.get("quality_score", 0))
            author = metadata.get("author_uid", "unknown")

            if score >= RAG_MIN_QUALITY_SCORE:
                high_quality_data.append({
                    "text": content,
                    "author_uid": author,
                    "score": score
                })
        except Exception as e:
            logger.error(f"[Fusion] Error procesando aporte {key}: {e}")

    if not high_quality_data:
        logger.info("[Fusion] Ningún aporte nuevo superó el umbral de calidad.")
        return

    logger.info(f"[Fusion] Fusionando {len(high_quality_data)} aportes de alta calidad.")

    # 3. Reconstruir índice local
    rag_engine.rebuild_index(high_quality_data)

    # 4. Subir archivos de índice a Greenfield (DCellar)
    idx_path = os.path.join(LOCAL_BRAIN_DIR, LOCAL_INDEX_FILE)
    meta_path = os.path.join(LOCAL_BRAIN_DIR, LOCAL_INDEX_META)

    try:
        with open(idx_path, 'rb') as f:
            await put_object(f"{BRAIN_PREFIX}/{LOCAL_INDEX_FILE}", f.read(), content_type="application/octet-stream")
        
        with open(meta_path, 'rb') as f:
            await put_object(f"{BRAIN_PREFIX}/{LOCAL_INDEX_META}", f.read(), content_type="application/json")

        # 5. Actualizar Brain Pointer (indicando la versión/timestamp actual)
        timestamp = str(int(asyncio.get_event_loop().time()))
        await put_object(BRAIN_POINTER_OBJECT, timestamp.encode('utf-8'), content_type="text/plain")
        
        logger.info("[Fusion] ✅ Cerebro sincronizado con éxito en Greenfield.")
    except Exception as e:
        logger.error(f"[Fusion] ❌ Error subiendo el cerebro evolucionado: {e}")

if __name__ == "__main__":
    # Configuración de logging para ejecución manual
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_fusion())
