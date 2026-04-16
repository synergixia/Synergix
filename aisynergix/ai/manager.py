"""
manager.py — Orquestador de recursos y cola de prioridad del Nodo Fantasma.
Protege la RAM de 8GB mediante Semaphore(2), Lock por UID para condiciones de carrera,
y gestiona puntos residuales con actualizaciones lazy a Greenfield.
"""

import asyncio
import logging
import psutil
from typing import Any, Dict, List, Coroutine, Optional
from collections import defaultdict

from aisynergix.config.constants import RANK_ORDER
from aisynergix.services.greenfield import update_user_metadata
from aisynergix.bot.identity import unmask_uid  # Para desofuscación interna

logger = logging.getLogger(__name__)


class UIDLockManager:
    """
    Gestiona locks asíncronos por UID para evitar condiciones de carrera
    en actualizaciones concurrentes a Greenfield.
    """
    
    def __init__(self):
        self._locks: Dict[str, asyncio.Lock] = {}
        self._lock_creation_lock = asyncio.Lock()
    
    async def get_lock(self, uid: str) -> asyncio.Lock:
        """
        Obtiene un lock único para un UID específico.
        
        Args:
            uid: UID del usuario (ofuscado o no)
        
        Returns:
            asyncio.Lock: Lock exclusivo para ese UID
        """
        async with self._lock_creation_lock:
            if uid not in self._locks:
                self._locks[uid] = asyncio.Lock()
            return self._locks[uid]
    
    def cleanup_unused_locks(self, max_age_seconds: int = 3600):
        """
        Limpia locks que no han sido usados en un tiempo (ejecutar periódicamente).
        
        Args:
            max_age_seconds: Tiempo máximo de inactividad antes de eliminar
        """
        # Implementación básica - en producción se necesitaría tracking de uso
        if len(self._locks) > 1000:  # Límite arbitrario
            logger.warning(f"UIDLockManager tiene {len(self._locks)} locks, considerando limpieza")
            # En una implementación completa, se haría tracking de último uso


class AIOrchestrator:
    """
    Orquestador de tareas de IA con prioridad por rango y límites estrictos de RAM.
    
    Características:
    - Semaphore(2) para limitar concurrencia en ARM64 de 8GB
    - Colas separadas por rango (prioridad: Oráculo > Mente Colmena > ... > Iniciado)
    - Monitoreo de RAM para evitar OOM (Out Of Memory)
    - Manejo elegante de tareas fallidas
    """
    
    def __init__(self, max_concurrent: int = 2):
        """
        Inicializa el orquestador de IA.
        
        Args:
            max_concurrent: Número máximo de tareas concurrentes (por defecto 2 para 8GB RAM)
        """
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.queues: Dict[str, asyncio.Queue] = {rank: asyncio.Queue() for rank in RANK_ORDER}
        self.active_tasks = 0
        self.uid_locks = UIDLockManager()
        self._shutdown = False
        
        # Estadísticas
        self.stats = {
            "total_processed": 0,
            "by_rank": defaultdict(int),
            "errors": 0,
            "ram_throttles": 0
        }
    
    def _get_ram_usage(self) -> float:
        """
        Obtiene el porcentaje de uso de RAM.
        
        Returns:
            float: Porcentaje de RAM en uso (0-100)
        """
        return psutil.virtual_memory().percent
    
    def _is_ram_critical(self) -> bool:
        """
        Verifica si el uso de RAM es crítico (>90%).
        
        Returns:
            bool: True si la RAM está en estado crítico
        """
        return self._get_ram_usage() > 90
    
    async def enqueue_task(self, uid: str, rank: str, coro: Coroutine) -> Any:
        """
        Añade una tarea a la cola de su rango y espera su ejecución.
        
        Args:
            uid: UID del usuario (para logging)
            rank: Rango del usuario (determina prioridad)
            coro: Corrutina a ejecutar
        
        Returns:
            Any: Resultado de la corrutina
        
        Raises:
            Exception: Si la tarea falla
        """
        if rank not in self.queues:
            logger.warning(f"Rango '{rank}' no válido para UID {uid}, usando 'Iniciado'")
            rank = "Iniciado"
        
        future = asyncio.get_event_loop().create_future()
        
        # Añadir a la cola correspondiente
        await self.queues[rank].put((uid, coro, future))
        logger.debug(f"Tarea encolada para UID {uid} (rango: {rank})")
        
        # Disparar procesador si no está ya procesando
        asyncio.create_task(self._process_queues())
        
        # Esperar resultado
        return await future
    
    async def _process_queues(self):
        """
        Procesa tareas de las colas respetando:
        1. Límite de concurrencia (Semaphore)
        2. Prioridad por rango (mayor rango primero)
        3. Salud de RAM (throttling si es necesario)
        """
        if self.active_tasks >= 2 or self._shutdown:
            return
        
        async with self.semaphore:
            self.active_tasks += 1
            try:
                # Buscar tarea de mayor prioridad disponible
                task_data = None
                for rank in reversed(RANK_ORDER):
                    if not self.queues[rank].empty():
                        task_data = await self.queues[rank].get()
                        break
                
                if not task_data:
                    return
                
                uid, coro, future = task_data
                logger.debug(f"Procesando tarea para UID {uid} (rango: {RANK_ORDER.index(rank)})")
                
                # Verificar RAM antes de ejecutar
                if self._is_ram_critical():
                    logger.warning(f"RAM crítica ({self._get_ram_usage():.1f}%). Throttling para UID {uid}...")
                    self.stats["ram_throttles"] += 1
                    await asyncio.sleep(5)  # Backoff por RAM
                
                # Ejecutar tarea
                try:
                    result = await coro
                    future.set_result(result)
                    self.stats["total_processed"] += 1
                    self.stats["by_rank"][rank] += 1
                    logger.debug(f"Tarea completada para UID {uid}")
                    
                except asyncio.CancelledError:
                    future.cancel()
                    logger.warning(f"Tarea cancelada para UID {uid}")
                    
                except Exception as e:
                    future.set_exception(e)
                    self.stats["errors"] += 1
                    logger.error(f"Error en tarea para UID {uid}: {e}", exc_info=True)
                    
            finally:
                self.active_tasks -= 1
                
                # Procesar siguiente tarea si hay cola
                if not self._shutdown:
                    asyncio.create_task(self._process_queues())
    
    async def add_residual_points(
        self,
        uid_ofuscado: str,
        current_metadata: Dict[str, str],
        points: int = 1,
        reason: str = "uso_en_rag"
    ) -> bool:
        """
        Lazy update asíncrono para sumar puntos residuales cuando un aporte es usado en RAG.
        
        Args:
            uid_ofuscado: UID ofuscado del autor
            current_metadata: Metadatos actuales del usuario (desde caché)
            points: Puntos a añadir (por defecto 1)
            reason: Razón del punto residual
        
        Returns:
            bool: True si la actualización fue exitosa
        """
        try:
            # Desofuscar UID internamente para logging (solo internamente)
            uid_real = unmask_uid(uid_ofuscado) if hasattr(unmask_uid, '__call__') else uid_ofuscado
            
            # Obtener lock para este UID específico
            lock = await self.uid_locks.get_lock(uid_ofuscado)
            
            async with lock:
                # Leer puntos actuales
                old_points = int(current_metadata.get("points", 0))
                new_points = old_points + points
                
                # Actualizar metadatos
                current_metadata["points"] = str(new_points)
                
                # Actualización lazy a Greenfield (fire-and-forget)
                update_task = asyncio.create_task(
                    update_user_metadata(uid_ofuscado, current_metadata)
                )
                
                # Añadir callback para logging
                def log_result(task):
                    try:
                        success = task.result()
                        if success:
                            logger.info(f"[Residual] +{points} pts para {uid_real[:8]}... ({reason}). Total: {new_points}")
                        else:
                            logger.error(f"[Residual] Fallo actualizando puntos para {uid_real[:8]}...")
                    except Exception as e:
                        logger.error(f"[Residual] Error en callback para {uid_real[:8]}: {e}")
                
                update_task.add_done_callback(log_result)
                
                return True
                
        except Exception as e:
            logger.error(f"Error en add_residual_points para {uid_ofuscado}: {e}", exc_info=True)
            return False
    
    async def reward_contributor(
        self,
        uid_ofuscado: str,
        metadata: Dict[str, str],
        points: int = 1,
        context: str = "rag_usage"
    ):
        """
        Premia a un autor cuyo conocimiento fue usado por el RAG.
        Wrapper para add_residual_points con logging específico.
        
        Args:
            uid_ofuscado: UID ofuscado del autor
            metadata: Metadatos del autor
            points: Puntos a asignar
            context: Contexto del premio (rag_usage, daily_bonus, etc.)
        """
        asyncio.create_task(
            self.add_residual_points(uid_ofuscado, metadata, points, f"contribucion_{context}")
        )
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Obtiene estadísticas del orquestador.
        
        Returns:
            Dict: Estadísticas de procesamiento
        """
        queue_sizes = {rank: q.qsize() for rank, q in self.queues.items()}
        
        return {
            "active_tasks": self.active_tasks,
            "queue_sizes": queue_sizes,
            "total_processed": self.stats["total_processed"],
            "by_rank": dict(self.stats["by_rank"]),
            "errors": self.stats["errors"],
            "ram_throttles": self.stats["ram_throttles"],
            "ram_usage_percent": self._get_ram_usage(),
            "uid_locks_count": len(self.uid_locks._locks)
        }
    
    async def shutdown(self):
        """Apagado limpio del orquestador."""
        self._shutdown = True
        logger.info("Orquestador de IA apagándose...")
        
        # Esperar a que las tareas activas terminen
        while self.active_tasks > 0:
            await asyncio.sleep(0.1)
        
        logger.info("Orquestador de IA apagado correctamente")


# Instancia global del orquestador
orchestrator = AIOrchestrator(max_concurrent=2)


async def manage_ai_call(uid: str, rank: str, task: Coroutine) -> Any:
    """
    Punto de entrada principal para cualquier llamada a la IA que consuma RAM.
    
    Args:
        uid: UID del usuario (para logging y prioridad)
        rank: Rango del usuario
        task: Corrutina a ejecutar (ej: ask_judge, ask_thinker)
    
    Returns:
        Any: Resultado de la tarea
    """
    return await orchestrator.enqueue_task(uid, rank, task)


async def add_residual_points(
    uid_ofuscado: str,
    metadata: Dict[str, str],
    points: int = 1,
    reason: str = "uso_en_rag"
) -> bool:
    """
    Función helper para añadir puntos residuales.
    
    Args:
        uid_ofuscado: UID ofuscado del autor
        metadata: Metadatos actuales
        points: Puntos a añadir
        reason: Razón del punto residual
    
    Returns:
        bool: True si se programó la actualización
    """
    return await orchestrator.add_residual_points(uid_ofuscado, metadata, points, reason)


async def get_orchestrator_stats() -> Dict[str, Any]:
    """
    Obtiene estadísticas del orquestador global.
    
    Returns:
        Dict: Estadísticas actuales
    """
    return orchestrator.get_stats()


async def shutdown_orchestrator():
    """Apaga el orquestador global."""
    await orchestrator.shutdown()
