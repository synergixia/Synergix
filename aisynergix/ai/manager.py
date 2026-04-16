"""
manager.py — Orquestador de Concurrencia y Gestor de Estado para Synergix.
Protege los recursos del servidor (Hetzner ARM64 8GB) usando Semáforos y
evita condiciones de carrera en BNB Greenfield usando Locks asíncronos por UID.
"""

import asyncio
import logging
from typing import Any, Coroutine, Dict

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# CONTROL DE CONCURRENCIA PARA IA (Protección de RAM)
# Limita a 2 el número de inferencias simultáneas (Pensador o Juez)
# Esto previene OOM (Out Of Memory) en contenedores Docker limitados.
# ─────────────────────────────────────────────────────────────────────────────
GLOBAL_AI_SEMAPHORE = asyncio.Semaphore(2)

# ─────────────────────────────────────────────────────────────────────────────
# CONTROL DE CONCURRENCIA PARA GREENFIELD (Locks por UID)
# Evita que múltiples peticiones del mismo usuario sobreescriban su archivo
# de 0-bytes de manera concurrente, protegiendo la integridad de sus Tags.
# ─────────────────────────────────────────────────────────────────────────────
_uid_locks: Dict[str, asyncio.Lock] = {}
_locks_mutex = asyncio.Lock()

async def get_uid_lock(uid: str) -> asyncio.Lock:
    """Obtiene o crea un Lock específico para un UID garantizando thread-safety."""
    async with _locks_mutex:
        if uid not in _uid_locks:
            _uid_locks[uid] = asyncio.Lock()
        return _uid_locks[uid]

async def manage_ai_call(uid: str, rank: str, coroutine: Coroutine) -> Any:
    """
    Envuelve la llamada a la IA local usando el Semáforo global.
    Asegura que el nodo no colapse por exceso de peticiones concurrentes.
    
    Args:
        uid: El UID (ofuscado o real) del usuario que hace la petición.
        rank: El rango del usuario (útil para futuras prioridades).
        coroutine: La corrutina de IA a ejecutar (ej. ask_thinker(...))
    """
    logger.debug(f"[Manager] Esperando slot de IA para UID: {uid} (Rank: {rank})...")
    
    async with GLOBAL_AI_SEMAPHORE:
        logger.debug(f"[Manager] Slot asignado. Ejecutando inferencia para UID: {uid}")
        try:
            result = await coroutine
            return result
        except Exception as e:
            logger.error(f"[Manager] Fallo en la corrutina de IA para UID {uid}: {e}")
            raise
        finally:
            logger.debug(f"[Manager] Slot liberado para UID: {uid}")

async def lazy_points_update(uid: str, points_to_add: int):
    """
    Tarea Fire-and-Forget para actualizar puntos de forma residual.
    Se usa cuando el RAG utiliza un aporte y el creador original gana puntos.
    Garantiza consistencia usando el Lock del UID correspondiente.
    
    IMPORTANTE: Esta función se importa en tiempo de ejecución o se
    acopla a services/greenfield.py e identity.py para evitar ciclos de importación.
    """
    from aisynergix.bot.identity import hydrate_user, dehydrate_user
    
    uid_lock = await get_uid_lock(uid)
    
    async with uid_lock:
        logger.info(f"[Lazy Update] Iniciando adición de {points_to_add} puntos para UID: {uid}")
        try:
            # 1. Hidratar usuario (bajar tags de Greenfield o Caché)
            user_ctx = await hydrate_user(uid)
            
            # 2. Modificar en RAM
            user_ctx.points += points_to_add
            
            # 3. Deshidratar (Subir Tags actualizados a Greenfield)
            await dehydrate_user(user_ctx)
            
            logger.info(f"[Lazy Update] ✅ Puntos actualizados con éxito. Total: {user_ctx.points}")
            
        except Exception as e:
            logger.error(f"[Lazy Update] ❌ Error actualizando puntos para UID {uid}: {e}")
