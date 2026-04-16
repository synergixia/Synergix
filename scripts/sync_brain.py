"""
sync_brain.py — Secuencia de Arranque (Bootstrapping).
Descarga el estado más reciente de la Mente Colmena (RAG) y el ranking (Top 10)
desde BNB Greenfield hacia la RAM y disco efímero del nodo Hetzner antes de iniciar el bot.
"""

import os
import logging
import asyncio

from aisynergix.config.constants import (
    BRAIN_PREFIX,
    TOP10_OBJECT,
    LOCAL_BRAIN_DIR,
    TOP10_LOCAL_PATH
)
from aisynergix.services.greenfield import get_object

logger = logging.getLogger(__name__)

async def sync_initial_state():
    """Descarga los archivos críticos desde Greenfield al almacenamiento efímero local."""
    logger.info("[Sync] 🌐 Iniciando sincronización de estado desde BNB Greenfield...")
    
    os.makedirs(LOCAL_BRAIN_DIR, exist_ok=True)
    os.makedirs(os.path.dirname(TOP10_LOCAL_PATH), exist_ok=True)

    # 1. Descargar Top 10
    top10_data = await get_object(TOP10_OBJECT)
    if top10_data:
        with open(TOP10_LOCAL_PATH, "wb") as f:
            f.write(top10_data)
        logger.info("[Sync] ✅ top10.json descargado con éxito.")
    else:
        logger.warning("[Sync] ⚠️ top10.json no encontrado en la red. Se generará en la próxima fusión.")

    # 2. Descargar Índice FAISS (brain.index)
    index_path = os.path.join(LOCAL_BRAIN_DIR, "brain.index")
    index_data = await get_object(f"{BRAIN_PREFIX}/brain.index")
    if index_data:
        with open(index_path, "wb") as f:
            f.write(index_data)
        logger.info("[Sync] ✅ brain.index descargado.")
    else:
        logger.warning("[Sync] ⚠️ brain.index no encontrado. RAG iniciará vacío.")

    # 3. Descargar Metadatos del Índice (brain_meta.json)
    meta_path = os.path.join(LOCAL_BRAIN_DIR, "brain_meta.json")
    meta_data = await get_object(f"{BRAIN_PREFIX}/brain_meta.json")
    if meta_data:
        with open(meta_path, "wb") as f:
            f.write(meta_data)
        logger.info("[Sync] ✅ brain_meta.json descargado.")
    else:
        logger.warning("[Sync] ⚠️ brain_meta.json no encontrado.")

    logger.info("[Sync] 🚀 Sincronización inicial completada.")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(sync_initial_state())
