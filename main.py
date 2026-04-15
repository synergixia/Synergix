"""
main.py — Punto de entrada de Synergix Ghost Node.
Orquestación del Bot de Telegram, Motor RAG y Manager de IA.
"""

import asyncio
import logging
import signal
import sys
from aisynergix.bot.bot import start_bot

# Configuración Global de Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("aisynergix.log", encoding='utf-8')
    ]
)

logger = logging.getLogger("Synergix.Main")

async def shutdown(loop, signal=None):
    """Cierre limpio del nodo."""
    if signal:
        logger.info(f"Recibida señal de apagado {signal.name}...")
    
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    [task.cancel() for task in tasks]
    
    logger.info(f"Cancelando {len(tasks)} tareas activas...")
    await asyncio.gather(*tasks, return_exceptions=True)
    loop.stop()
    logger.info("Synergix Ghost Node detenido.")

def main():
    """Arranque del sistema."""
    logger.info("=== SYNERGIX GHOST NODE — ARM64 IGNITION ===")
    
    loop = asyncio.get_event_loop()
    
    # Manejo de señales (SIGINT, SIGTERM)
    signals = (signal.SIGHUP, signal.SIGTERM, signal.SIGINT)
    for s in signals:
        loop.add_signal_handler(
            s, lambda s=s: asyncio.create_task(shutdown(loop, signal=s))
        )

    try:
        # 1. Iniciar Bot (que internamente inicia Scheduler y RAG)
        loop.run_until_complete(start_bot())
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.critical(f"Error fatal durante el arranque: {e}", exc_info=True)
    finally:
        loop.close()

if __name__ == "__main__":
    main()
