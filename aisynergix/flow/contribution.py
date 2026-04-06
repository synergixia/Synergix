# aisynergix/flow/contribution.py
import asyncio
import logging
from aisynergix.engine.llm import engine
from aisynergix.core.db import soberania

logger = logging.getLogger("synergix.flow")

class ContributionFlow:
    """Flujo de contribución asíncrono con gamificación."""
    def __init__(self):
        self.queue = asyncio.Queue()
        self.worker_task = None

    async def start(self):
        self.worker_task = asyncio.create_task(self._worker())

    async def add(self, uid_str: str, content: str, lang: str):
        """Añade un aporte a la cola de procesamiento."""
        if len(content) < 20:
            return "El conocimiento debe ser profundo (mínimo 20 caracteres). 🧠"
        
        await self.queue.put({"uid": uid_str, "content": content, "lang": lang})
        return "¡Recibido! Procesando tu sabiduría en segundo plano... 🔗🚀"

    async def _worker(self):
        while True:
            job = await self.queue.get()
            try:
                # 1. Evaluar aporte
                eval_res = await engine.judge(job["content"])
                score = eval_res.get("score", 5)
                
                # 2. Gamificación
                pts = 0
                if score >= 8: pts = 20
                elif score >= 5: pts = 10
                
                # Bonus Challenge (Placeholder para detección de keywords)
                if "challenge" in job["content"].lower():
                    pts += 5

                # 3. Guardar puntos localmente
                soberania.update_user_points(job["uid"], pts)
                
                logger.info(f"✅ Aporte procesado uid={job['uid']} score={score} pts={pts}")
            except Exception as e:
                logger.error(f"❌ Worker error: {e}")
            finally:
                self.queue.task_done()

flow = ContributionFlow()
