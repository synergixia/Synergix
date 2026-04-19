#!/usr/bin/env python3
"""
Synergix - Punto de Entrada de Synergix (Nodo Fantasma).
Levanta la infraestructura asíncrona: Inicialización desde Greenfield (sync_brain),
sistema FSM con Write-Behind Cache, arranque del bot Aiogram 3, 
y programador de tareas (APScheduler).
"""

import asyncio
import logging
import sys
import signal

from aiogram import Bot, Dispatcher
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# Importación de Módulos Locales Asíncronos
from aisynergix.bot.bot import dp, bot, router
from aisynergix.bot.fsm import init_fsm_system, get_cache
from aisynergix.services.greenfield import close_greenfield_client
from aisynergix.ai.local_ia import close_ia_clients
from scripts.sync_brain import sync_brain
from scripts.fusion_brain import fusion_brain

# Configuración estricta de Logs
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("Synergix.Main")

async def notification_task():
    """Tarea programada a las 23:59 para notificar regalías a los usuarios."""
    logger.info("🌙 [Scheduler] Ejecutando cierre de ciclo diario y procesando regalías...")

async def shutdown(dispatcher: Dispatcher, scheduler=None):
    """Apagado elegante de todos los sistemas."""
    logger.info("🛑 Iniciando apagado seguro del Nodo Fantasma...")
    
    if scheduler:
        scheduler.shutdown(wait=False)
    
    # Detener Write-Behind Cache L1
    try:
        cache = await get_cache()
        await cache.stop()
        logger.info("✅ Write-Behind Cache detenido y sincronizado en Greenfield.")
    except Exception as e:
        logger.error(f"Error deteniendo caché: {e}")
        
    try:
        await close_greenfield_client()
        logger.info("✅ Cliente Greenfield Web3 cerrado de forma segura.")
    except Exception as e:
        logger.error(f"Error cerrando Greenfield: {e}")

    try:
        await close_ia_clients()
        logger.info("✅ Clientes IA locales (Pensador/Juez) liberados.")
    except Exception as e:
        logger.error(f"Error cerrando clientes IA: {e}")

    await bot.session.close()
    logger.info("✅ Sesión del Bot Aiogram terminada. Adiós.")

async def main():
    logger.info("=============================================")
    logger.info("🚀 INICIANDO SYNERGIX - NODO FANTASMA (ARM64)")
    logger.info("=============================================")

    # 1. Sincronizar estado base (Descargar Cerebro desde DCellar)
    logger.info("🧠 Solicitando Sincronización Inicial (sync_brain). Descargando de Greenfield...")
    try:
        sync_engine = await sync_brain()
        if not sync_engine:
            logger.warning("⚠️ No se pudo sincronizar el cerebro desde DCellar, iniciando Falla en Modo Base.")
        else:
            logger.info("✅ Sincronización (RAG VectorIAL + Metadatos) completada.")
    except Exception as e:
            logger.error(f"Error en la sincronización del cerebro: {e}")

    # 2. Inicializar sistema FSM y Caché L1
    logger.info("💾 Inicializando Write-Behind Cache L1 y Máquina de Estados...")
    try:
        await init_fsm_system()
    except Exception as e:
        logger.error(f"Error arrancando Caché L1: {e}")

    # 3. Configurar Tareas Asíncronas (APScheduler)
    scheduler = AsyncIOScheduler()
    scheduler.add_job(fusion_brain, 'interval', minutes=10, id='fusion_brain_job')
    scheduler.add_job(notification_task, 'cron', hour=23, minute=59, id='daily_notifications')
    scheduler.start()
    logger.info(f"⏰ [Scheduler] Activo. Fusión de conocimiento programada cada 10m.")

    # 4. Iniciar Bot
    try:
        logger.info("🤖 [Bot] Conectando a la red de Telegram (Stateless Long-Polling)...")
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot, handle_signals=False)
    except Exception as e:
        logger.critical(f"❌ Error crítico en la ejecución del bot: {e}", exc_info=True)
    finally:
        await shutdown(dp, scheduler)

if __name__ == "__main__":
    if sys.platform == "linux":
        try:
            import uvloop
            uvloop.install()
            logger.info("⚡ [Sistema] uvloop instalado.")
        except ImportError:
            pass

    loop = asyncio.get_event_loop()
    main_task = asyncio.ensure_future(main())
    
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, main_task.cancel)
    
    try:
        loop.run_until_complete(main_task)
    except asyncio.CancelledError:
        logger.info("🛡️ Interrupción recibida (SIGINT/SIGTERM), purgando nodo limpiamente.")

