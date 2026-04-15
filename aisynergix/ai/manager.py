"""
manager.py — Orquestador de recursos y cola de prioridad.
Protege la RAM de 8GB mediante Semaphore(2) y prioriza usuarios según su Rango.
"""

import asyncio
import logging
import psutil
from typing import Any, Dict, List, Coroutine, Tuple

from aisynergix.config.constants import RANK_ORDER
from aisynergix.services.greenfield import update_user_metadata

logger = logging.getLogger(__name__)

class AIOrchestrator:
    """Gestiona la ejecución de tareas de IA con prioridad y límites de RAM."""

    def __init__(self, max_concurrent: int = 2):
        self.semaphore = asyncio.Semaphore(max_concurrent)
        # Diccionario de colas por rango: {rango: [tareas]}
        self.queues: Dict[str, asyncio.Queue] = {rank: asyncio.Queue() for rank in RANK_ORDER}
        self.active_tasks = 0
        self._monitor_task = None

    def _get_ram_usage(self) -> float:
        return psutil.virtual_memory().percent

    async def enqueue_task(self, rank: str, coro: Coroutine) -> Any:
        """Añade una tarea a la cola de su rango y espera ejecución."""
        if rank not in self.queues:
            rank = "Iniciado"
        
        future = asyncio.get_event_loop().create_future()
        await self.queues[rank].put((coro, future))
        
        # Disparar procesador si no hay tareas activas suficientes
        asyncio.create_task(self._process_queues())
        return await future

    async def _process_queues(self):
        """Procesa tareas de las colas respetando el semáforo y la prioridad de rango."""
        if self.active_tasks >= 2:
            return

        async with self.semaphore:
            self.active_tasks += 1
            try:
                # Buscar la tarea de mayor rango disponible
                task_data = None
                for rank in reversed(RANK_ORDER):
                    if not self.queues[rank].empty():
                        task_data = await self.queues[rank].get()
                        break
                
                if task_data:
                    coro, future = task_data
                    
                    # Verificar salud de RAM antes de ejecutar
                    if self._get_ram_usage() > 90:
                        logger.warning(f"RAM Crítica: {self._get_ram_usage()}%. Esperando liberación...")
                        await asyncio.sleep(5)
                    
                    try:
                        result = await coro
                        future.set_result(result)
                    except Exception as e:
                        future.set_exception(e)
            finally:
                self.active_tasks -= 1

    async def add_residual_points(self, uid: str, current_metadata: dict, points: int = 1):
        """Lazy update asíncrono para sumar puntos residuales (Uso en RAG)."""
        try:
            old_points = int(current_metadata.get("points", 0))
            new_points = old_points + points
            current_metadata["points"] = str(new_points)
            
            # Actualización en Greenfield
            success = await update_user_metadata(uid, current_metadata)
            if success:
                logger.info(f"[Residual] +{points} puntos para {uid}. Total: {new_points}")
        except Exception as e:
            logger.error(f"Error en residual points para {uid}: {e}")

# Instancia global del orquestador
orchestrator = AIOrchestrator(max_concurrent=2)

async def manage_ai_call(uid: str, rank: str, task: Coroutine) -> Any:
    """Punto de entrada para cualquier llamada a la IA que consuma RAM."""
    return await orchestrator.enqueue_task(rank, task)

async def reward_contributor(uid: str, metadata: dict, points: int = 1):
    """Premia a un autor cuyo conocimiento fue usado por el RAG."""
    asyncio.create_task(orchestrator.add_residual_points(uid, metadata, points))
