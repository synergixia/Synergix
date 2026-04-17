#!/usr/bin/env python3
"""
Synergix - Nodo Fantasma
Punto de entrada principal para el bot de Telegram con arquitectura stateless.
"""
import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from aisynergix.bot.bot import register_handlers
from aisynergix.bot.fsm import write_behind_sync
from aisynergix.services.greenfield import GreenfieldClient
from aisynergix.services.rag_engine import RAGEngine
from scripts.sync_brain import ensure_brain_synced
from scripts.fusion_brain import FusionBrain

# Configuración de logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('/aisynergix/data/synergix.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

class SynergixNode:
    """Nodo Fantasma principal."""
    
    def __init__(self):
        self.bot = None
        self.dp = None
        self.greenfield = None
        self.rag_engine = None
        self.scheduler = AsyncIOScheduler()
        self.fusion_brain = None
        self.running = False
        
    async def initialize(self):
        """Inicializa todos los componentes del nodo."""
        logger.info("🚀 Iniciando Nodo Fantasma Synergix...")
        
        # 1. Sincronizar cerebro desde DCellar
        logger.info("🔄 Sincronizando cerebro desde Greenfield...")
        await ensure_brain_synced()
        
        # 2. Inicializar cliente Greenfield
        logger.info("🔗 Conectando a BNB Greenfield...")
        self.greenfield = GreenfieldClient()
        await self.greenfield.initialize()
        
        # 3. Inicializar RAG Engine
        logger.info("🧠 Inicializando motor RAG multilingüe...")
        self.rag_engine = RAGEngine()
        await self.rag_engine.initialize()
        
        # 4. Inicializar bot de Telegram
        logger.info("🤖 Inicializando bot de Telegram...")
        token = self._get_telegram_token()
        self.bot = Bot(
            token=token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML)
        )
        self.dp = Dispatcher()
        
        # 5. Registrar handlers
        logger.info("📝 Registrando handlers de comandos...")
        await register_handlers(self.dp, self.greenfield, self.rag_engine)
        
        # 6. Configurar schedulers
        self._setup_schedulers()
        
        logger.info("✅ Nodo Fantasma inicializado correctamente")
        
    def _get_telegram_token(self) -> str:
        """Obtiene el token de Telegram desde variables de entorno."""
        token = os.getenv("TELEGRAM_TOKEN")
        if not token:
            logger.critical("❌ TELEGRAM_TOKEN no configurado")
            sys.exit(1)
        return token
    
    def _setup_schedulers(self):
        """Configura las tareas programadas."""
        # FusionBrain cada 10 minutos
        self.fusion_brain = FusionBrain(self.greenfield, self.rag_engine)
        self.scheduler.add_job(
            self.fusion_brain.run,
            'interval',
            minutes=10,
            id='fusion_brain',
            max_instances=1,
            replace_existing=True
        )
        
        # Write-Behind Cache cada 2 minutos
        self.scheduler.add_job(
            write_behind_sync,
            'interval',
            minutes=2,
            id='write_behind_sync',
            max_instances=1,
            replace_existing=True
        )
        
        # Limpieza diaria a las 00:00 UTC
        self.scheduler.add_job(
            self._daily_cleanup,
            'cron',
            hour=0,
            minute=0,
            id='daily_cleanup',
            max_instances=1,
            replace_existing=True
        )
        
        # Auditoría diaria a las 02:00 UTC
        self.scheduler.add_job(
            self._daily_audit,
            'cron',
            hour=2,
            minute=0,
            id='daily_audit',
            max_instances=1,
            replace_existing=True
        )
        
        # Retos semanales los lunes a las 00:00 UTC
        self.scheduler.add_job(
            self._weekly_challenge,
            'cron',
            day_of_week='mon',
            hour=0,
            minute=0,
            id='weekly_challenge',
            max_instances=1,
            replace_existing=True
        )
        
        logger.info(f"⏰ Schedulers configurados: {len(self.scheduler.get_jobs())} tareas")
    
    async def _daily_cleanup(self):
        """Resetea daily_aportes_count para todos los usuarios."""
        logger.info("🧹 Ejecutando limpieza diaria...")
        try:
            # Implementación en greenfield.py
            await self.greenfield.reset_daily_counts()
            logger.info("✅ Limpieza diaria completada")
        except Exception as e:
            logger.error(f"❌ Error en limpieza diaria: {e}")
    
    async def _daily_audit(self):
        """Sube logs comprimidos a DCellar."""
        logger.info("📊 Ejecutando auditoría diaria...")
        try:
            # Implementación en greenfield.py
            await self.greenfield.upload_compressed_logs()
            logger.info("✅ Auditoría diaria completada")
        except Exception as e:
            logger.error(f"❌ Error en auditoría diaria: {e}")
    
    async def _weekly_challenge(self):
        """Genera y publica reto semanal."""
        logger.info("🎯 Generando reto semanal...")
        try:
            # Implementación en fusion_brain.py
            await self.fusion_brain.generate_weekly_challenge()
            logger.info("✅ Reto semanal generado")
        except Exception as e:
            logger.error(f"❌ Error generando reto semanal: {e}")
    
    async def start(self):
        """Inicia el nodo."""
        if self.running:
            return
        
        await self.initialize()
        
        # Configurar manejo de señales
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(self.stop()))
        
        # Iniciar schedulers
        self.scheduler.start()
        logger.info("⏰ Schedulers iniciados")
        
        # Iniciar bot
        self.running = True
        logger.info("🤖 Bot iniciado. Esperando mensajes...")
        
        try:
            await self.dp.start_polling(self.bot)
        except asyncio.CancelledError:
            logger.info("👋 Polling cancelado")
        except Exception as e:
            logger.critical(f"❌ Error crítico en polling: {e}")
            raise
    
    async def stop(self):
        """Detiene el nodo de forma controlada."""
        if not self.running:
            return
        
        logger.info("🛑 Deteniendo Nodo Fantasma...")
        self.running = False
        
        # Detener schedulers
        if self.scheduler.running:
            self.scheduler.shutdown()
            logger.info("⏰ Schedulers detenidos")
        
        # Cerrar sesión del bot
        if self.bot:
            await self.bot.session.close()
            logger.info("🤖 Sesión del bot cerrada")
        
        # Sincronizar caché write-behind final
        try:
            await write_behind_sync(force=True)
            logger.info("💾 Caché write-behind sincronizado")
        except Exception as e:
            logger.error(f"❌ Error sincronizando caché: {e}")
        
        logger.info("👋 Nodo Fantasma detenido correctamente")
        sys.exit(0)

async def main():
    """Función principal."""
    node = SynergixNode()
    
    try:
        await node.start()
    except KeyboardInterrupt:
        await node.stop()
    except Exception as e:
        logger.critical(f"❌ Error fatal: {e}")
        await node.stop()
        sys.exit(1)

if __name__ == "__main__":
    # Asegurar que existan directorios necesarios
    Path("/aisynergix/data").mkdir(parents=True, exist_ok=True)
    Path("/aisynergix/ai/models").mkdir(parents=True, exist_ok=True)
    
    asyncio.run(main())
