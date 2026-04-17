"""
Máquina de estados (FSM) con Write‑Behind Cache para Synergix.
Implementa caché L1 en RAM para evitar rate limits en Greenfield, sincronizando cada 2 minutos.
"""

import asyncio
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Tuple

from aisynergix.services.greenfield import update_user_metadata

logger = logging.getLogger("synergix.fsm")

# ──────────────────────────────────────────────────────────────────────────────
# CACHÉ L1 (WRITE‑BEHIND)
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class CacheEntry:
    uid_ofuscado: str
    tags: Dict[str, str]
    last_update: float
    dirty: bool = True


class WriteBehindCache:
    """
    Caché en RAM que agrupa actualizaciones de usuarios y las sincroniza
    a Greenfield en lotes cada 2 minutos.
    """

    def __init__(self, sync_interval: int = 120):  # 2 minutos
        self.cache: Dict[str, CacheEntry] = {}
        self.sync_interval = sync_interval
        self._lock = asyncio.Lock()
        self._sync_task: Optional[asyncio.Task] = None
        self._running = False

    async def start(self):
        """Inicia el cron de sincronización en background."""
        if self._running:
            return
        self._running = True
        self._sync_task = asyncio.create_task(self._sync_loop())
        logger.info("🔄 Write‑Behind Cache iniciado (sync cada %d segundos)", self.sync_interval)

    async def stop(self):
        """Detiene el cron y sincroniza cualquier cambio pendiente."""
        self._running = False
        if self._sync_task:
            self._sync_task.cancel()
            try:
                await self._sync_task
            except asyncio.CancelledError:
                pass
        # Sincronizar cambios restantes
        await self._sync_now()
        logger.info("🛑 Write‑Behind Cache detenido")

    async def enqueue_update(self, uid_ofuscado: str, tags: Dict[str, str]):
        """
        Encola una actualización de tags para un usuario.
        La sincronización real ocurrirá en el próximo ciclo.
        """
        async with self._lock:
            now = time.time()
            if uid_ofuscado in self.cache:
                # Merge: preservar campos no actualizados
                existing = self.cache[uid_ofuscado].tags
                existing.update(tags)
                self.cache[uid_ofuscado].tags = existing
                self.cache[uid_ofuscado].last_update = now
                self.cache[uid_ofuscado].dirty = True
            else:
                self.cache[uid_ofuscado] = CacheEntry(
                    uid_ofuscado=uid_ofuscado,
                    tags=tags.copy(),
                    last_update=now,
                    dirty=True,
                )
        logger.debug("📝 Cache L1: enqueued update for %s", uid_ofuscado)

    async def get_pending_count(self) -> int:
        """Retorna cuántas entradas tienen cambios no sincronizados."""
        async with self._lock:
            return sum(1 for entry in self.cache.values() if entry.dirty)

    async def _sync_loop(self):
        """Loop principal: sincroniza cada sync_interval segundos."""
        while self._running:
            try:
                await asyncio.sleep(self.sync_interval)
                await self._sync_now()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error en sync loop del cache: %s", e)
                await asyncio.sleep(30)  # backoff en caso de error

    async def _sync_now(self):
        """Sincroniza todos los cambios sucios a Greenfield."""
        async with self._lock:
            dirty_entries = [e for e in self.cache.values() if e.dirty]
            if not dirty_entries:
                return
            logger.info("🔄 Sincronizando cache L1 → Greenfield (%d usuarios)", len(dirty_entries))
            tasks = []
            for entry in dirty_entries:
                task = update_user_metadata(entry.uid_ofuscado, entry.tags)
                tasks.append(task)
            # Ejecutar en paralelo con límite de concurrencia
            results = await asyncio.gather(*tasks, return_exceptions=True)
            success = 0
            for entry, res in zip(dirty_entries, results):
                if isinstance(res, Exception):
                    logger.error("❌ Error sincronizando %s: %s", entry.uid_ofuscado, res)
                    # Mantener dirty = True para reintentar luego
                else:
                    entry.dirty = False
                    success += 1
            logger.info("✅ Cache sincronizado: %d/%d éxitos", success, len(dirty_entries))
            # Limpiar entradas no sucias muy antiguas (> 1 hora)
            now = time.time()
            to_delete = []
            for uid, entry in self.cache.items():
                if not entry.dirty and (now - entry.last_update) > 3600:
                    to_delete.append(uid)
            for uid in to_delete:
                del self.cache[uid]
            if to_delete:
                logger.debug("🧹 Cache limpió %d entradas antiguas", len(to_delete))


# ──────────────────────────────────────────────────────────────────────────────
# INSTANCIA GLOBAL DEL CACHÉ
# ──────────────────────────────────────────────────────────────────────────────

_cache: Optional[WriteBehindCache] = None


async def get_cache() -> WriteBehindCache:
    global _cache
    if _cache is None:
        _cache = WriteBehindCache(sync_interval=120)  # 2 minutos
        await _cache.start()
    return _cache


def enqueue_cache_update(uid_ofuscado: str, tags: Dict[str, str]):
    """
    Función de conveniencia para que otros módulos encolen actualizaciones.
    Nota: Esta función NO es async porque se llama desde contextos donde
    no se puede await (ej. middlewares). Crea una tarea en background.
    """
    async def _async_enqueue():
        cache = await get_cache()
        await cache.enqueue_update(uid_ofuscado, tags)
    asyncio.create_task(_async_enqueue())


# ──────────────────────────────────────────────────────────────────────────────
# ESTADOS FSM (Finite State Machine)
# ──────────────────────────────────────────────────────────────────────────────

# Estados posibles del usuario (coinciden con los tags fsm_state en Greenfield)
FSM_STATES = {
    "menu_principal": "🏠 Menú Principal",
    "chat_libre": "💭 Chat Libre",
    "esperando_aporte": "📝 Esperando Aporte",
    "seleccion_idioma": "🌐 Selección de Idioma",
    "viendo_estado": "📊 Viendo Estado",
    "viendo_memoria": "🧠 Viendo Memoria",
}

# Transiciones permitidas (desde → [hacia])
ALLOWED_TRANSITIONS = {
    "menu_principal": ["chat_libre", "esperando_aporte", "seleccion_idioma", "viendo_estado", "viendo_memoria"],
    "chat_libre": ["menu_principal"],
    "esperando_aporte": ["menu_principal"],
    "seleccion_idioma": ["menu_principal"],
    "viendo_estado": ["menu_principal"],
    "viendo_memoria": ["menu_principal"],
}


async def set_user_state(telegram_uid: int, new_state: str, sync_now: bool = False) -> bool:
    """
    Cambia el estado FSM de un usuario.
    Si sync_now=False, se encola en el Write‑Behind Cache.
    Retorna True si la transición es válida.
    """
    from aisynergix.bot.identity import hydrate_user, update_user_field
    # Validar estado
    if new_state not in FSM_STATES:
        logger.warning("Estado FSM inválido: %s", new_state)
        return False
    # Obtener estado actual (hidratando si es necesario)
    user = await hydrate_user(telegram_uid)
    current = user.get("fsm_state", "menu_principal")
    # Verificar transición permitida
    if new_state != current and new_state not in ALLOWED_TRANSITIONS.get(current, []):
        logger.warning("Transición FSM no permitida: %s → %s", current, new_state)
        return False
    # Actualizar
    await update_user_field(telegram_uid, "fsm_state", new_state, sync_now=sync_now)
    logger.debug("🔄 FSM: usuario %d %s → %s", telegram_uid, current, new_state)
    return True


async def get_user_state(telegram_uid: int) -> str:
    """Retorna el estado FSM actual del usuario."""
    from aisynergix.bot.identity import hydrate_user
    user = await hydrate_user(telegram_uid)
    return user.get("fsm_state", "menu_principal")


async def ensure_menu_state(telegram_uid: int) -> None:
    """
    Asegura que el usuario esté en menu_principal.
    Útil después de completar una acción que debería devolver al menú.
    """
    current = await get_user_state(telegram_uid)
    if current != "menu_principal":
        await set_user_state(telegram_uid, "menu_principal", sync_now=False)


# ──────────────────────────────────────────────────────────────────────────────
# INTEGRACIÓN CON AIOGRAM (helpers para handlers)
# ──────────────────────────────────────────────────────────────────────────────

async def fsm_middleware(handler, event, data):
    """
    Middleware para Aiogram que actualiza automáticamente last_seen_ts
    y maneja transiciones de estado basadas en botones.
    """
    from aiogram.types import Message, CallbackQuery
    from aisynergix.bot.identity import hydrate_user
    
    event_obj = data.get("event")
    if isinstance(event_obj, (Message, CallbackQuery)):
        telegram_uid = event_obj.from_user.id
        # Hidratar usuario (actualiza last_seen_ts automáticamente)
        await hydrate_user(telegram_uid)
    
    # Continuar con el handler
    return await handler(event, data)


async def init_fsm_system():
    """Inicializa el sistema FSM y el Write‑Behind Cache."""
    cache = await get_cache()
    logger.info("✅ Sistema FSM + Write‑Behind Cache inicializado")
