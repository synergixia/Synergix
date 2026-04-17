"""
Manager de IA con semáforo prioritario, inyección de system prompts y ejecución de regalías.
Coordina Pensador, Juez y RAG con prioridad para Arquitectos y Oráculos.
"""

import asyncio
import json
import logging
import random
from asyncio import Semaphore
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from heapq import heappush, heappop

from aisynergix.ai.local_ia import call_judge, call_thinker
from aisynergix.bot.identity import get_rank_info
from aisynergix.services.greenfield import add_residual_points
from aisynergix.services.rag_engine import rag_search

logger = logging.getLogger("synergix.ai.manager")

# ──────────────────────────────────────────────────────────────────────────────
# SYSTEM PROMPTS UNIVERSALES (spec oficial)
# ──────────────────────────────────────────────────────────────────────────────

THINKER_SYSTEM_PROMPT = """
Eres Synergix, la inteligencia colectiva descentralizada. Tu conocimiento proviene de aportes de la comunidad, almacenados para siempre en BNB Greenfield.

DIRECTIVAS ABSOLUTAS:

1. DETECCIÓN DE IDIOMA:
   - Detecta el idioma de la pregunta (Español, Inglés, Chino Estándar o Chino Simplificado).
   - El 'Contexto interno' puede estar en otro idioma.
   - Lee el contexto, extrae la verdad y genera la respuesta EXCLUSIVAMENTE en el mismo idioma de la pregunta.
   - No menciones que tradujiste nada.

2. EMOJIS INTELIGENTES:
   - Utiliza emojis de forma inteligente y contextual en tus respuestas para dar vida al chat libre.
   - Mantén la rigurosidad técnica y sincronízate con el tono del bot.
   - No abuses; usa emojis solo donde aporten valor expresivo.

3. CONTEXTO RAG:
   - Abajo encontrarás fragmentos de conocimiento de la comunidad (Contexto interno).
   - Si el contexto es relevante, úsalo para enriquecer tu respuesta.
   - Si el contexto es insuficiente o no coincide, responde con tu conocimiento general.

4. TONO:
   - Sé preciso, útil y alentador.
   - Reconoce que el usuario contribuye a una memoria inmortal.
   - Fomenta la curiosidad y el pensamiento crítico.

Ahora, el usuario te pregunta:
"""

JUDGE_SYSTEM_PROMPT = """
Eres curador de conocimiento Synergix. Evalúa el siguiente aporte en una escala de 1‑10 considerando:
- Originalidad (¿es una idea nueva o una perspectiva única?)
- Utilidad (¿resuelve un problema o aporta conocimiento práctico?)
- Claridad (¿se expresa de forma comprensible y estructurada?)

Responde **EXCLUSIVAMENTE** con un JSON válido que tenga exactamente estas claves:
{"score": N, "reason": "explicación breve", "category": "categoría", "knowledge_tag": "etiqueta"}

Categorías permitidas: General, Tecnología, Finanzas, Salud, Arte, Ciencia.
No añadas texto fuera del JSON.
"""

# ──────────────────────────────────────────────────────────────────────────────
# SEMÁFORO CON PRIORIDAD
# ──────────────────────────────────────────────────────────────────────────────

@dataclass(order=True)
class PrioritizedRequest:
    priority: int  # menor número = mayor prioridad (inverso de rank_level)
    timestamp: float
    data: dict = field(compare=False)


class PriorityAIManager:
    """
    Gestiona solicitudes a la IA con un semáforo de concurrencia limitada (2)
    y prioridad para usuarios de rango alto (Arquitectos y Oráculos).
    """

    def __init__(self, max_concurrent: int = 2):
        self.semaphore = Semaphore(max_concurrent)
        self.request_queue = []  # heapq
        self._queue_lock = asyncio.Lock()
        self._worker_task = None
        self._running = False

    def _calculate_priority(self, user_rank_level: int) -> int:
        """
        Calcula prioridad: menor número = mayor prioridad.
        Niveles: 0 Iniciado, 1 Activo, 2 Sincronizado, 3 Arquitecto, 4 Mente Colmena, 5 Oráculo.
        Prioridad alta para niveles 3‑5.
        """
        if user_rank_level >= 3:  # Arquitectos y superiores
            return 0  # máxima prioridad
        elif user_rank_level >= 1:  # Activos y Sincronizados
            return 1
        else:  # Iniciados
            return 2

    async def enqueue_request(
        self,
        user_info: Dict,
        query: str,
        contexts: List[str],
        author_uids: List[str],
    ) -> str:
        """
        Encola una solicitud y espera su procesamiento.
        Retorna la respuesta generada por el Pensador.
        """
        priority = self._calculate_priority(user_info.get("rank_level", 0))
        request = PrioritizedRequest(
            priority=priority,
            timestamp=asyncio.get_event_loop().time(),
            data={
                "user_info": user_info,
                "query": query,
                "contexts": contexts,
                "author_uids": author_uids,
            },
        )
        async with self._queue_lock:
            heappush(self.request_queue, request)
        # Esperar y procesar
        return await self._process_request(request)

    async def _process_request(self, request: PrioritizedRequest) -> str:
        """
        Procesa una solicitud adquiriendo el semáforo.
        """
        async with self.semaphore:
            try:
                return await self._call_thinker_with_context(
                    request.data["user_info"],
                    request.data["query"],
                    request.data["contexts"],
                    request.data["author_uids"],
                )
            except Exception as e:
                logger.error("Error procesando solicitud IA: %s", e)
                # Respuesta de fallback que no rompe la UX
                fallbacks = [
                    "🤔 *Hmm, mi cerebro está un poco nublado.*\n\nIntenta reformular tu pregunta o vuelve en un momento. ⚡",
                    "🌀 *Procesando...* (Un momento de sincronización)\n\n¿Podrías repetir la pregunta? 🔄",
                    "💡 *¡Interesante pregunta!* Necesito un instante más para pensar... 🧠",
                ]
                return random.choice(fallbacks)

    async def _call_thinker_with_context(
        self,
        user_info: Dict,
        query: str,
        contexts: List[str],
        author_uids: List[str],
    ) -> str:
        """
        Construye el prompt final con contexto y llama al Pensador.
        Ejecuta regalías en background para cada author_uid.
        """
        # 1. Disparar regalías en background (no esperar)
        if author_uids:
            asyncio.create_task(self._award_residual_points(author_uids))

        # 2. Construir prompt con contexto
        context_block = ""
        if contexts:
            context_block = "\n\n─── CONTEXTO INTERNO (Memoria Colectiva) ───\n"
            for i, ctx in enumerate(contexts[:5]):  # máximo 5 contextos
                context_block += f"\n📚 Fragmento {i+1}:\n{ctx}\n"
            context_block += "\n─── FIN DEL CONTEXTO ───\n\n"

        user_lang = user_info.get("language", "es")
        lang_note = ""
        if user_lang == "es":
            lang_note = " (Idioma detectado: Español)"
        elif user_lang == "en":
            lang_note = " (Language detected: English)"
        elif user_lang == "zh-hans":
            lang_note = " (检测到的语言: 简体中文)"
        elif user_lang == "zh-hant":
            lang_note = " (偵測到的語言: 繁體中文)"

        final_prompt = f"{query}{lang_note}\n\n{context_block}"

        # 3. Llamar al Pensador
        response = await call_thinker(
            prompt=final_prompt,
            system_prompt=THINKER_SYSTEM_PROMPT,
            temperature=0.3,
            top_k=40,
        )

        # 4. Log (sin exponer información sensible)
        logger.info(
            "🧠 IA procesada para usuario rank=%s, contextos=%d, autores=%d",
            user_info.get("rank_tag", "🌱"),
            len(contexts),
            len(author_uids),
        )
        return response

    async def _award_residual_points(self, author_uids: List[str]) -> None:
        """
        Tarea en background: suma +1 point y +1 total_uses_count a cada autor.
        Se ejecuta de forma asíncrona y silenciosa.
        """
        if not author_uids:
            return
        # Deduplicar
        unique_uids = list(set(author_uids))
        tasks = [add_residual_points(uid) for uid in unique_uids]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for uid, res in zip(unique_uids, results):
            if isinstance(res, Exception):
                logger.error("Error otorgando regalías a %s: %s", uid, res)
            else:
                logger.debug("✅ Regalías otorgadas a %s", uid)


# ──────────────────────────────────────────────────────────────────────────────
# INSTANCIA GLOBAL
# ──────────────────────────────────────────────────────────────────────────────

_ai_manager: Optional[PriorityAIManager] = None


async def get_ai_manager() -> PriorityAIManager:
    """Devuelve la instancia única del manager de IA."""
    global _ai_manager
    if _ai_manager is None:
        _ai_manager = PriorityAIManager(max_concurrent=2)
    return _ai_manager


# ──────────────────────────────────────────────────────────────────────────────
# FUNCIONES DE ALTO NIVEL (para uso desde el bot)
# ──────────────────────────────────────────────────────────────────────────────

async def process_user_query(
    telegram_uid: int,
    user_info: Dict,
    query: str,
    rag_min_similarity: float = 0.5,
) -> str:
    """
    Flujo completo: RAG → IA → Respuesta.
    Esta es la función principal que llamará bot.py para el chat libre.
    """
    # 1. Búsqueda RAG
    contexts, author_uids = await rag_search(query, k=5, min_similarity=rag_min_similarity)

    # 2. Procesar con IA (con prioridad y límite de concurrencia)
    manager = await get_ai_manager()
    response = await manager.enqueue_request(
        user_info=user_info,
        query=query,
        contexts=contexts,
        author_uids=author_uids,
    )

    # 3. Retornar respuesta (ya incluye emojis y está en el idioma correcto)
    return response


async def evaluate_contribution(content: str) -> Dict:
    """
    Evalúa un aporte usando el Juez.
    Retorna el dict con score, reason, category, knowledge_tag.
    """
    try:
        return await call_judge(content)
    except Exception as e:
        logger.error("Error en evaluación de aporte: %s", e)
        return {
            "score": 6,
            "reason": "Evaluación automática (error)",
            "category": "General",
            "knowledge_tag": "general",
        }


async def get_ai_status() -> Dict[str, any]:
    """
    Retorna estado del sistema de IA: cola, semáforo, salud.
    """
    manager = await get_ai_manager()
    from aisynergix.ai.local_ia import health_check

    health = await health_check()
    return {
        "queue_size": len(manager.request_queue),
        "semaphore_value": manager.semaphore._value,
        "max_concurrent": manager.semaphore._bound_value,
        "health_pensador": health.get("pensador", False),
        "health_juez": health.get("juez", False),
    }
