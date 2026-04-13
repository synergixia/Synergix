import asyncio
import os
import logging
from aisynergix.services.greenfield import greenfield

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("SyncBrain")

async def sync_on_startup():
    logger.info("Iniciando conexión con DCellar...")
    os.makedirs("/app/data/brains", exist_ok=True)
    
    # 1. Leer el Tag del puntero
    meta_pointer = await greenfield.get_user_metadata("brain_pointer")
    if not meta_pointer:
        logger.warning("No hay cerebro previo. Generando perfil génesis.")
        return
        
    version = meta_pointer.get("latest_v", "latest")
    
    try:
        index_data = await greenfield.get_object(f"aisynergix/data/brains/{version}.index")
        meta_data = await greenfield.get_object(f"aisynergix/data/brains/{version}.json")

        if index_data and meta_data:
            with open("/app/data/brains/current.index", "wb") as f:
                f.write(index_data)
            with open("/app/data/brains/current.json", "wb") as f:
                f.write(meta_data)
            logger.info("🧠 Conocimiento inyectado en la RAM local.")
    except Exception as e:
        logger.error(f"Error sincronizando Boot: {e}")

if __name__ == "__main__":
    asyncio.run(sync_on_startup())
