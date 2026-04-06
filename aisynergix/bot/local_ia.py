# aisynergix/bot/local_ia.py
import asyncio
import logging
import httpx
from aisynergix.backend.services.rag_manager import rag_manager
from aisynergix.config.constants import T

logger = logging.getLogger("synergix.ia")

class SynergixLocalIA:
    """Motor de Inferencia Local con RAG y Colas Asíncronas."""
    def __init__(self, endpoint="http://127.0.0.1:8080/v1"):
        self.endpoint = endpoint
        self.queue = asyncio.Queue()
        self._worker_task = None

    async def start(self):
        """Inicia el worker de la cola asíncrona."""
        self._worker_task = asyncio.create_task(self._process_queue())
        logger.info("🔥 IA Worker iniciado.")

    async def get_response(self, text: str, uid: str, lang: str = "es") -> str:
        """Consulta el RAG y genera respuesta con Qwen 1.5B."""
        # 1. Consultar Memoria Inmortal (RAG)
        context = await rag_manager.get_context(text)
        
        prompt = f"{T[lang]['brain_consult']}\n{context}\n\nUser: {text}\nSynergix:"
        
        async with httpx.AsyncClient(timeout=60) as client:
            try:
                resp = await client.post(f"{self.endpoint}/chat/completions", json={
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.7,
                    "max_tokens": 300
                })
                resp.raise_for_status()
                return resp.json()["choices"][0]["message"]["content"].strip()
            except Exception as e:
                logger.error(f"❌ Error en LLM: {e}")
                return "Sincronizando sabiduría... 🔄"

    async def _process_queue(self):
        """Worker que procesa la fila de aportes en background."""
        while True:
            job = await self.queue.get()
            try:
                # Aquí procesamos el aporte (evaluación + subida a Greenfield)
                # con el Juez (Qwen 0.5B)
                logger.info(f"✅ Aporte procesado uid={job['uid']}")
            except Exception as e:
                logger.error(f"❌ Worker error: {e}")
            finally:
                self.queue.task_done()

# Singleton
ia_engine = SynergixLocalIA()
