"""
main.py — Punto de Entrada de Synergix.
Levanta la infraestructura asíncrona: Inicialización desde Greenfield, 
arranque del bot Aiogram y programador de tareas (APScheduler).
"""

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# Importación de Módulos Locales
from aisynergix.config.constants import (
    TELEGRAM_BOT_TOKEN,
    FUSION_INTERVAL_MINUTES,
    DAILY_NOTIFICATION_TIME
)
from aisynergix.bot.bot import register_handlers, bot
from scripts.sync_brain import sync_initial_state
from scripts.fusion_brain import run_fusion

# Configuración estricta de Logs para la terminal de Hetzner
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("Synergix.Main")

async def notification_task():
    """Tarea programada a las 23:59 para eventos globales."""
    logger.info("🌙 [Scheduler] Ejecutando cierre de ciclo diario...")
    # Aquí iría la lógica de broadcast o limpieza de colas.

async def main():
    logger.info("=============================================")
    logger.info("🚀 INICIANDO SYNERGIX - NODO FANTASMA (ARM64)")
    logger.info("=============================================")

    # 1. Sincronizar estado base (Evita iniciar en blanco si el contenedor reinicia)
    await sync_initial_state()

    # 2. Configurar el Dispatcher (Stateless, FSM en Memoria Efímera)
    dp = Dispatcher(storage=MemoryStorage())
    register_handlers(dp)

    # 3. Configurar Tareas Asíncronas (Evolución y Notificaciones)
    scheduler = AsyncIOScheduler()
    scheduler.add_job(run_fusion, 'interval', minutes=FUSION_INTERVAL_MINUTES)
    
    # Parsear hora de notificación (Ej: 23:59)
    hour, minute = map(int, DAILY_NOTIFICATION_TIME.split(":"))
    scheduler.add_job(notification_task, 'cron', hour=hour, minute=minute)
    
    scheduler.start()
    logger.info(f"⏰ [Scheduler] Activo. Fusión configurada cada {FUSION_INTERVAL_MINUTES}m.")

    # 4. Iniciar Long-Polling
    try:
        logger.info("🤖 [Bot] Conectando a la red de Telegram...")
        # Eliminar Webhook si existiera previamente para evitar conflictos
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    except Exception as e:
        logger.critical(f"❌ Error crítico en la ejecución del bot: {e}")
    finally:
        logger.info("🛑 [Bot] Apagando sistemas. Desconectando de Greenfield...")
        await bot.session.close()

if __name__ == "__main__":
    # Optimizaciones de Loop para ARM64 y alta concurrencia
    if sys.platform == "linux":
        try:
            import uvloop
            uvloop.install()
            logger.info("⚡ [Sistema] uvloop instalado para máximo rendimiento en ARM64.")
        except ImportError:
            pass

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrupción manual recibida. Cerrando nodo.")
