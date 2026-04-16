"""
main.py — Punto de entrada principal del Nodo Fantasma Synergix.
Orquesta la sincronización inicial, el bot de Telegram y el scheduler.
"""

import asyncio
import logging
import signal
import sys
import os
from datetime import datetime

from aisynergix.bot.bot import start_bot
from aisynergix.scripts.sync_brain import sync_brain, emergency_sync_users
from aisynergix.config.constants import LOCAL_DATA_DIR, LOCAL_BRAIN_DIR

# Configuración Global de Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("synergix_node.log", encoding='utf-8')
    ]
)

logger = logging.getLogger("Synergix.Main")


async def startup_sync() -> bool:
    """
    Sincronización inicial al arrancar el nodo.
    
    Returns:
        bool: True si la sincronización fue exitosa
    """
    logger.info("=== SYNERGIX GHOST NODE — INICIALIZACIÓN ===")
    
    # Crear directorios necesarios
    os.makedirs(LOCAL_DATA_DIR, exist_ok=True)
    os.makedirs(LOCAL_BRAIN_DIR, exist_ok=True)
    
    logger.info(f"Directorios configurados: {LOCAL_DATA_DIR}, {LOCAL_BRAIN_DIR}")
    
    # Intentar sincronización normal
    logger.info("Iniciando sincronización cerebral desde Greenfield...")
    sync_success = await sync_brain()
    
    if not sync_success:
        logger.warning("Sincronización normal falló, intentando sincronización de emergencia...")
        
        # Sincronización de emergencia para top10.json
        emergency_data = await emergency_sync_users()
        
        # Guardar top10.json de emergencia
        import json
        top10_path = os.path.join(LOCAL_DATA_DIR, "top10.json")
        with open(top10_path, "w", encoding="utf-8") as f:
            json.dump(emergency_data, f, ensure_ascii=False, indent=2)
        
        logger.info(f"Top10.json de emergencia guardado: {emergency_data.get('total_users', 0)} usuarios")
    
    # Verificar que el motor RAG está cargado
    from aisynergix.services.rag_engine import rag_engine
    stats = rag_engine.get_stats()
    
    if stats["loaded"]:
        logger.info(f"✅ Motor RAG cargado: {stats['total_contributions']} aportes")
    else:
        logger.warning("⚠️ Motor RAG cargado pero vacío o con errores")
    
    return True  # Continuar incluso si la sincronización falló parcialmente


async def shutdown(signal_name: str = None):
    """
    Cierre limpio del nodo.
    
    Args:
        signal_name: Nombre de la señal recibida
    """
    if signal_name:
        logger.info(f"Recibida señal de apagado {signal_name}...")
    
    logger.info("Iniciando apagado limpio del Nodo Fantasma...")
    
    # Cancelar todas las tareas pendientes
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    
    if tasks:
        logger.info(f"Cancelando {len(tasks)} tareas activas...")
        for task in tasks:
            task.cancel()
        
        # Esperar a que las tareas se cancelen
        await asyncio.gather(*tasks, return_exceptions=True)
    
    # Apagar orquestador de IA si está disponible
    try:
        from aisynergix.ai.manager import shutdown_orchestrator
        await shutdown_orchestrator()
        logger.info("Orquestador de IA apagado")
    except Exception as e:
        logger.debug(f"Error apagando orquestador: {e}")
    
    logger.info("✅ Synergix Ghost Node detenido correctamente")


def signal_handler(signal_name: str):
    """
    Manejador de señales del sistema.
    
    Args:
        signal_name: Nombre de la señal
    """
    logger.info(f"Señal {signal_name} recibida, iniciando apagado...")
    asyncio.create_task(shutdown(signal_name))


async def main_async():
    """
    Función principal asíncrona.
    """
    # 1. Sincronización inicial
    sync_ok = await startup_sync()
    
    if not sync_ok:
        logger.error("Fallo crítico en sincronización inicial. Abortando.")
        return
    
    # 2. Iniciar Bot de Telegram (que internamente inicia Scheduler)
    logger.info("=== INICIANDO BOT SYNERGIX ===")
    try:
        await start_bot()
    except asyncio.CancelledError:
        logger.info("Bot cancelado durante ejecución")
    except Exception as e:
        logger.critical(f"Error fatal en el bot: {e}", exc_info=True)
        raise
    finally:
        logger.info("Bot finalizado")


def main():
    """
    Punto de entrada síncrono.
    """
    logger.info("""
    ╔══════════════════════════════════════════════════════════╗
    ║               SYNERGIX GHOST NODE — ARM64                ║
    ║           BNB Greenfield • Stateless • Web3 Puro         ║
    ║                 Iniciando Nodo Fantasma...               ║
    ╚══════════════════════════════════════════════════════════╝
    """)
    
    # Configurar manejo de señales
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    # Registrar manejadores de señales
    for sig_name in ('SIGINT', 'SIGTERM', 'SIGHUP'):
        try:
            sig = getattr(signal, sig_name)
            loop.add_signal_handler(
                sig,
                lambda s=sig_name: signal_handler(s)
            )
            logger.debug(f"Manejador de señal registrado: {sig_name}")
        except (AttributeError, NotImplementedError):
            pass
    
    try:
        # Ejecutar aplicación principal
        loop.run_until_complete(main_async())
    except KeyboardInterrupt:
        logger.info("Interrupción por teclado recibida")
    except Exception as e:
        logger.critical(f"Error fatal no manejado: {e}", exc_info=True)
        sys.exit(1)
    finally:
        # Cierre limpio
        logger.info("Cerrando event loop...")
        loop.run_until_complete(shutdown("finalización"))
        loop.close()
        
        logger.info("""
    ╔══════════════════════════════════════════════════════════╗
    ║             SYNERGIX GHOST NODE — APAGADO                ║
    ║           Conocimiento inmortalizado en DCellar          ║
    ╚══════════════════════════════════════════════════════════╝
        """)


if __name__ == "__main__":
    main()
