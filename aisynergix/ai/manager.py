import asyncio
import logging
from aisynergix.services.greenfield import greenfield

logger = logging.getLogger("BrainManager")

class BrainManager:
    def __init__(self):
        self.sem = asyncio.Semaphore(2) # Protección de CPU 4 núcleos
        self.reward_queue = asyncio.Queue()

    async def process_residual_rewards(self):
        """Tarea en segundo plano para actualizar puntos residuales sin bloquear el chat"""
        while True:
            author_uid = await self.reward_queue.get()
            try:
                await greenfield.add_residual_points(author_uid, 1) # +1 punto residual
                logger.info(f"Regalía residual otorgada a {author_uid}")
            except Exception as e:
                logger.error(f"Error procesando regalía: {e}")
            finally:
                self.reward_queue.task_done()
                await asyncio.sleep(0.5) # Evitar spam al RPC

brain_manager = BrainManager()
