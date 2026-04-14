import asyncio
import logging
from typing import List, Dict

from aisynergix.ai.local_ia import LocalIA
from aisynergix.services.greenfield import GreenfieldClient

logger = logging.getLogger("Synergix.Manager")

class AIManager:
    """
    Orquestador de recursos IA y recompensas residuales.
    """
    def __init__(self, ai_client: LocalIA, greenfield: GreenfieldClient):
        self.ai = ai_client
        self.greenfield = greenfield
        self.semaphore = asyncio.Semaphore(2) # Protege los 4 núcleos del ARM64

    async def process_chat(self, user_query: str, context: str, author_uids: List[int]) -> str:
        """
        Procesa una consulta usando RAG y paga regalías a los autores originales.
        """
        async with self.semaphore:
            system_prompt = f"Eres Synergix, una IA soberana y técnica. Usa el contexto para responder de forma precisa.\nContexto:\n{context}"
            response = await self.ai.ask_thinker(user_query, system_prompt)
            
            # Tarea en segundo plano para pagar puntos residuales (Lazy Update)
            if author_uids:
                asyncio.create_task(self._pay_residual_points(author_uids))
                
            return response

    async def _pay_residual_points(self, uids: List[int]):
        """Pago de regalías de baja prioridad."""
        for uid in uids:
            try:
                # Cada vez que un aporte es útil, sumamos 1 punto residual
                await self.greenfield.add_residual_points(uid, 1)
                await asyncio.sleep(0.5) # Evitar saturar el rate limit del Nodo Web3
            except Exception as e:
                logger.warning(f"Error en pago residual para {uid}: {e}")

    async def evaluate_contribution(self, content: str) -> Dict:
        """Usa el Juez para validar aportes de conocimiento."""
        async with self.semaphore:
            return await self.ai.ask_judge(content)
