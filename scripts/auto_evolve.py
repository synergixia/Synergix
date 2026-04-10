import asyncio
import logging
from aisynergix.services.rag_engine import reload_index
from scripts.fusion_brain import fusion_brain 

logging.basicConfig(level=logging.INFO, format="%(asctime)s [EVOLVE] %(message)s")
logger = logging.getLogger("SynergixAutoEvolve")

async def evolution_loop():
    logger.info("Daemon de Evolución Iniciado (10 min / 600s)")
    while True:
        try:
            has_evolved = await fusion_brain()
            if has_evolved:
                logger.info("🧬 Nueva memoria generada. Recargando RAG...")
                reload_index()
        except Exception as e:
            logger.error(f"Fallo en evolución: {e}")
        await asyncio.sleep(600)

if __name__ == "__main__":
    asyncio.run(evolution_loop())
