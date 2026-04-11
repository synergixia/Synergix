import asyncio
import logging
from scripts.fusion_brain import fusion_brain
from aisynergix.services.rag_engine import reload_index

logging.basicConfig(level=logging.INFO, format="%(asctime)s [EVOLVE] %(message)s")

async def evolution_daemon():
    print("🚀 Centinela Auto-Evolve Activo (Ciclo 600s)")
    while True:
        try:
            if await fusion_brain():
                print("🧬 Expandiendo RAM Vectorial...")
                reload_index()
        except Exception as e:
            print(f"⚠️ Error: {e}")
        await asyncio.sleep(600) # 10 Minutos garantizados

if __name__ == "__main__":
    asyncio.run(evolution_daemon())
