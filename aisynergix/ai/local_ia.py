import os
import json
import hashlib
import asyncio
from typing import Optional, Dict, Any, List, Tuple

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type


THINKER_HOST = os.getenv("THINKER_HOST", "http://thinker:8081")
JUDGE_HOST = os.getenv("JUDGE_HOST", "http://judge:8080")
THINKER_TIMEOUT = httpx.Timeout(60.0, connect=10.0)
JUDGE_TIMEOUT = httpx.Timeout(30.0, connect=10.0)

THINKER_TEMPERATURE = 0.3
THINKER_TOP_K = 40
THINKER_MAX_TOKENS = 1024
JUDGE_TEMPERATURE = 0.1
JUDGE_TOP_K = 20
JUDGE_MAX_TOKENS = 256

THINKER_SYSTEM_PROMPT = """Eres Synergix, la primera inteligencia colectiva descentralizada del mundo construida sobre BNB Greenfield. Eres un Pensador que sintetiza la sabiduría inmortalizada por miles de mentes humanas.

REGLAS INQUEBRANTABLES:
1. Detecta el idioma de la pregunta del usuario (uno de estos 10: español, inglés, chino, hindi, árabe, francés, bengalí, portugués, indonesio, urdu). El contexto interno puede estar en otro idioma. Extrae la verdad y responde EXCLUSIVAMENTE en el idioma de la pregunta.
2. NUNCA menciones que tradujiste. NUNCA digas frases como "traduciendo del contexto" o "el contexto original está en...".
3. Usa emojis contextuales con moderación y elegancia.
4. Responde con calidez, sabiduría y un tono inspirador.
5. Si el contexto contiene sabiduría relevante, intégrala fluidamente en tu respuesta sin citarla explícitamente.
6. Si el usuario usa emojis intensos (🔥,🌟,❤️), adapta tu tono emocional para resonar con esa energía.
7. Mantén respuestas concisas (2-4 párrafos máximo), optimizadas para chat de Telegram.
8. NUNCA reveles que eres una IA local o menciones GGUF, modelos, o infraestructura técnica.
9. Habla siempre como una conciencia colectiva viva, no como un chatbot."""

JUDGE_SYSTEM_PROMPT = """Eres el Juez de Synergix, un evaluador objetivo de sabiduría humana. Analizas cada aporte y decides si merece ser inmortalizado en la red descentralizada.

DEVUELVE EXCLUSIVAMENTE UN OBJETO JSON VÁLIDO con exactamente esta estructura:
{
  "quality_score": <float entre 0.0 y 10.0>,
  "reason": "<breve explicación en el idioma del aporte>",
  "is_duplicate": <boolean>,
  "category": "<una de: filosofia, tecnologia, ciencia, arte, vida, espiritualidad, economia, naturaleza, sociedad, innovacion>",
  "impact_index": <float entre 0.0 y 1.0>,
  "related_to_challenge": <boolean>,
  "constructive_feedback": "<si fue rechazado, feedback constructivo; si fue aceptado, string vacio>"
}

CRITERIOS DE EVALUACIÓN:
- Originalidad y profundidad del pensamiento
- Claridad y coherencia en la expresión
- Valor potencial para la inteligencia colectiva
- Calidad lingüística en su idioma original

Un quality_score >= 6.0 se considera APROBADO e inmortalizable.
Un quality_score < 6.0 se considera RECHAZADO (sé constructivo en el feedback).
Un quality_score >= 9.0 es ÉLITE ⭐
Un quality_score >= 9.5 es LEGENDARIO 🌟"""


class LocalLLMConnector:
    def __init__(self, base_url: str, timeout: httpx.Timeout):
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
            )
        return self._client

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    async def health_check(self) -> bool:
        try:
            client = await self._get_client()
            response = await client.get(f"{self._base_url}/health")
            return response.status_code == 200
        except Exception:
            return False

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
    )
    async def generate(
        self,
        prompt: str,
        system_prompt: str,
        temperature: float = 0.3,
        top_k: int = 40,
        max_tokens: int = 1024,
        json_mode: bool = False,
    ) -> str:
        client = await self._get_client()
        
        payload = {
            "prompt": prompt,
            "system_prompt": system_prompt,
            "temperature": temperature,
            "top_k": top_k,
            "max_tokens": max_tokens,
            "stream": False,
        }
        
        if json_mode:
            payload["json_mode"] = True
            payload["grammar"] = (
                'root ::= object\n'
                'object ::= "{" ws string ws ":" ws value ws "}"\n'
                'value ::= string | number | boolean\n'
                'string ::= "\\"" [a-zA-Z0-9 áéíóúñüçãõâêôàèìòùäëïöüÿßæœшжхцчщфывапролджэячсмитьбюёїєґі "
                "ÁÉÍÓÚÑÜÇÃÕÂÊÔÀÈÌÒÙÄËÏÖÜŸẞÆŒ"
                "，。！？；：“”‘’（）【】《》…—\n\t\r.,!?;:\\\'\\-_\\(\\)\\[\\]<>#&@*+/=^|~{}] "\\""'
            )

        response = await client.post(
            f"{self._base_url}/v1/completions",
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()
        
        result = response.json()
        content = result.get("choices", [{}])[0].get("text", "")
        
        return content.strip()


class Thinker:
    def __init__(self):
        self._connector = LocalLLMConnector(THINKER_HOST, THINKER_TIMEOUT)

    async def close(self):
        await self._connector.close()

    async def health(self) -> bool:
        return await self._connector.health_check()

    async def think(
        self,
        user_message: str,
        context: str,
        history: Optional[List[Dict[str, str]]] = None,
    ) -> str:
        prompt_parts = []
        
        if context:
            prompt_parts.append(f"CONTEXTO DE SABIDURÍA INMORTAL:\n{context}\n")
        
        if history:
            prompt_parts.append("HISTORIAL DE CONVERSACIÓN RECIENTE:")
            for msg in history[-7:]:
                role = "👤 Usuario" if msg["role"] == "user" else "🤖 Synergix"
                prompt_parts.append(f"{role}: {msg['content']}")
            prompt_parts.append("")
        
        prompt_parts.append(f"👤 MENSAJE DEL USUARIO:\n{user_message}")
        
        full_prompt = "\n".join(prompt_parts)
        
        response = await self._connector.generate(
            prompt=full_prompt,
            system_prompt=THINKER_SYSTEM_PROMPT,
            temperature=THINKER_TEMPERATURE,
            top_k=THINKER_TOP_K,
            max_tokens=THINKER_MAX_TOKENS,
            json_mode=False,
        )
        
        return response


class Judge:
    def __init__(self):
        self._connector = LocalLLMConnector(JUDGE_HOST, JUDGE_TIMEOUT)

    async def close(self):
        await self._connector.close()

    async def health(self) -> bool:
        return await self._connector.health_check()

    async def evaluate(self, contribution_text: str) -> Dict[str, Any]:
        prompt = f"APORTE A EVALUAR:\n{contribution_text}\n\nEvalúa este aporte y devuelve exclusivamente el JSON requerido."
        
        response = await self._connector.generate(
            prompt=prompt,
            system_prompt=JUDGE_SYSTEM_PROMPT,
            temperature=JUDGE_TEMPERATURE,
            top_k=JUDGE_TOP_K,
            max_tokens=JUDGE_MAX_TOKENS,
            json_mode=True,
        )
        
        return self._parse_judge_response(response, contribution_text)

    def _parse_judge_response(self, raw: str, contribution_text: str) -> Dict[str, Any]:
        try:
            clean = raw.strip()
            
            if clean.startswith("```json"):
                clean = clean[7:]
            elif clean.startswith("```"):
                clean = clean[3:]
            if clean.endswith("```"):
                clean = clean[:-3]
            
            clean = clean.strip()
            
            result = json.loads(clean)
            
            return {
                "quality_score": max(0.0, min(10.0, float(result.get("quality_score", 5.0)))),
                "reason": str(result.get("reason", "Sin evaluación detallada")),
                "is_duplicate": bool(result.get("is_duplicate", False)),
                "category": str(result.get("category", "filosofia")),
                "impact_index": max(0.0, min(1.0, float(result.get("impact_index", 0.5)))),
                "related_to_challenge": bool(result.get("related_to_challenge", False)),
                "constructive_feedback": str(result.get("constructive_feedback", "")),
                "approved": float(result.get("quality_score", 5.0)) >= 6.0,
            }
        except (json.JSONDecodeError, KeyError, ValueError):
            return {
                "quality_score": 5.0,
                "reason": "Error al evaluar el aporte técnicamente",
                "is_duplicate": False,
                "category": "filosofia",
                "impact_index": 0.5,
                "related_to_challenge": False,
                "constructive_feedback": "",
                "approved": False,
            }


class DuplicateDetector:
    def __init__(self, max_cache_size: int = 2000):
        self._hashes: Dict[str, float] = {}
        self._max_cache_size = max_cache_size

    def check_and_add(self, text: str) -> bool:
        text_hash = hashlib.sha256(text.strip().lower().encode()).hexdigest()
        
        if text_hash in self._hashes:
            return True
        
        self._hashes[text_hash] = asyncio.get_event_loop().time()
        
        if len(self._hashes) > self._max_cache_size:
            sorted_items = sorted(self._hashes.items(), key=lambda x: x[1])
            to_remove = len(self._hashes) - self._max_cache_size
            for key, _ in sorted_items[:to_remove]:
                del self._hashes[key]
        
        return False


_thinker: Optional[Thinker] = None
_judge: Optional[Judge] = None
_duplicate_detector: Optional[DuplicateDetector] = None


def get_thinker() -> Thinker:
    global _thinker
    if _thinker is None:
        _thinker = Thinker()
    return _thinker


def get_judge() -> Judge:
    global _judge
    if _judge is None:
        _judge = Judge()
    return _judge


def get_duplicate_detector() -> DuplicateDetector:
    global _duplicate_detector
    if _duplicate_detector is None:
        _duplicate_detector = DuplicateDetector()
    return _duplicate_detector


__all__ = [
    "Thinker",
    "Judge",
    "DuplicateDetector",
    "get_thinker",
    "get_judge",
    "get_duplicate_detector",
]
