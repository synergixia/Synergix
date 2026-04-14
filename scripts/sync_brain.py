import os
import logging
from aisynergix.services.greenfield import GreenfieldClient
from aisynergix.services.rag_engine import RAGEngine

logger = logging.getLogger("Synergix.Sync")

async def sync_now(greenfield: GreenfieldClient, rag: RAGEngine):
    """
    Sincronización de Ignición: Descarga el cerebro al encender el servidor.
    """
    logger.info("Sincronizando cerebro con Greenfield...")
    
    # 1. Obtener la versión actual desde el Brain Pointer
    # Greenfield: brain_pointer es un objeto de 0 bytes con Tag latest_v
    pointer_data = await greenfield.get_user_metadata(0)
    
    if not pointer_data:
        logger.warning("No se encontró puntero cerebral. Iniciando cerebro v0.")
        last_v = "v0"
    else:
        last_v = pointer_data.get("latest_v", "v0")

    index_path = f"data/Synergix_ia_{last_v}.index"
    txt_path = f"data/Synergix_ia_{last_v}.txt"

    # 2. Descargar archivos si no existen localmente
    if not os.path.exists(index_path):
        logger.info(f"Descargando {index_path}...")
        index_data = await greenfield.get_object(f"aisynergix/cerebros/Synergix_ia_{last_v}.index")
        if index_data:
            os.makedirs("data", exist_ok=True)
            with open(index_path, "wb") as f:
                f.write(index_data)

    if not os.path.exists(txt_path):
        logger.info(f"Descargando {txt_path}...")
        txt_data = await greenfield.get_object(f"aisynergix/cerebros/Synergix_ia_{last_v}.txt")
        if txt_data:
            with open(txt_path, "wb") as f:
                f.write(txt_data)

    # 3. Cargar en el motor RAG
    if os.path.exists(index_path) and os.path.exists(txt_path):
        rag.load_brain(index_path, txt_path)
    else:
        logger.warning("No se pudieron descargar los archivos del cerebro.")
