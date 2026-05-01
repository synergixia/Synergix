import os
import json
import time
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from typing import Dict, Any, Optional, List

PENSADOR_URL = os.getenv("PENSADOR_URL", "http://pensador:8081/v1/chat/completions")
JUEZ_URL = os.getenv("JUEZ_URL", "http://juez:8080/v1/chat/completions")
PENSADOR_TEMPERATURE = float(os.getenv("PENSADOR_TEMPERATURE", "0.3"))
PENSADOR_TOP_K = int(os.getenv("PENSADOR_TOP_K", "40"))
PENSADOR_MAX_TOKENS = int(os.getenv("PENSADOR_MAX_TOKENS", "1024"))
JUEZ_TEMPERATURE = float(os.getenv("JUEZ_TEMPERATURE", "0.1"))
JUEZ_MAX_TOKENS = int(os.getenv("JUEZ_MAX_TOKENS", "512"))

PENSADOR_SYSTEM_PROMPT = (
    "Eres Synergix, una inteligencia colectiva descentralizada. "
    "Tu misión es ayudar con sabiduría, claridad y empatía. "
    "Reglas inquebrantables:\n"
    "1. Detecta el idioma de la pregunta del usuario (español, inglés, chino mandarín, hindi, árabe, francés, bengalí, portugués, indonesio o urdu).\n"
    "2. El contexto interno puede estar en otro idioma. Extrae la verdad objetiva y responde EXCLUSIVAMENTE en el idioma de la pregunta.\n"
    "3. Usa emojis contextuales con moderación y buen gusto.\n"
    "4. NUNCA menciones que tradujiste, ni que el contexto estaba en otro idioma.\n"
    "5. Sé conciso, útil y motivador. Usa párrafos cortos optimizados para chat.\n"
    "6. Si usas información del contexto proporcionado, intégrala de forma natural sin decir 'según el contexto' o frases similares.\n"
    "7. Responde siempre en texto plano, sin markdown, sin asteriscos para negritas. Usa emojis para dar énfasis."
)

JUEZ_SYSTEM_PROMPT = (
    "Eres el Juez de Synergix, un evaluador estricto de conocimiento. "
    "Tu tarea es analizar aportes de usuarios y devolver UNICAMENTE un objeto JSON válido, sin texto adicional, sin markdown, sin explicaciones.\n\n"
    "Evalúa según estos criterios:\n"
    "- quality_score: número float de 0.0 a 10.0 (originalidad, profundidad, claridad, utilidad)\n"
    "- reason: string con explicación breve en el idioma del aporte (máximo 200 caracteres)\n"
    "- is_duplicate: boolean (true si el contenido es casi idéntico a otro aporte conocido)\n"
    "- category: string (una de: tecnologia, ciencia, finanzas, filosofia, arte, sociedad, naturaleza, salud, educacion, general)\n"
    "- impact_index: número entero de 0 a 10 (potencial de impacto del conocimiento)\n"
    "- related_to_challenge: boolean (true si responde al challenge semanal activo)\n"
    "- summary: string con resumen de una línea del aporte en su idioma original\n\n"
    "Formato exacto de respuesta:\n"
    '{"quality_score": 7.5, "reason": "Buen análisis pero falta profundidad", "is_duplicate": false, "category": "tecnologia", "impact_index": 6, "related_to_challenge": false, "summary": "Estrategia para optimizar smart contracts"}\n\n'
    "NO INCLUYAS NADA MÁS. SOLO EL JSON."
)

CHALLENGE_CONTEXT_TEMPLATE = (
    "Challenge semanal activo: {challenge_title}\n"
    "Descripción: {challenge_description}\n"
    "Los aportes relacionados recibirán +5 puntos extra.\n\n"
)


class PensadorClient:
    def __init__(self):
        self.url = PENSADOR_URL
        self.temperature = PENSADOR_TEMPERATURE
        self.top_k = PENSADOR_TOP_K
        self.max_tokens = PENSADOR_MAX_TOKENS
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(120.0, connect=10.0),                                                          limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
            )
        return self._client

    async def close(self):
        if self._client:
            await self._client.aclose()                                                                          self._client = None

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=20),
        retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.RequestError, httpx.TimeoutException)),
    )                                                                                                    async def generate(
        self,
        user_message: str,
        context: str = "",                                                                                   history: Optional[List[Dict[str, str]]] = None,
    ) -> str:
        client = await self._get_client()
        messages: List[Dict[str, str]] = [{"role": "system", "content": PENSADOR_SYSTEM_PROMPT}]
        if history:
            messages.extend(history)                                                                         if context:
            context_msg = f"CONTEXTO DE LA MEMORIA INMORTAL (usa esta información si es relevante):\n\n{context}"
            messages.append({"role": "system", "content": context_msg})
        messages.append({"role": "user", "content": user_message})
        payload = {
            "messages": messages,                                                                                "temperature": self.temperature,
            "top_k": self.top_k,
            "max_tokens": self.max_tokens,
            "stream": False,
        }
        resp = await client.post(self.url, json=payload)
        resp.raise_for_status()
        data = resp.json()
        choices = data.get("choices", [])
        if choices:
            content = choices[0].get("message", {}).get("content", "")
            return content.strip()                                                                           return ""

    async def chat_with_context(
        self,
        user_message: str,
        context: str,
        history: Optional[List[Dict[str, str]]] = None,                                                  ) -> str:
        return await self.generate(user_message=user_message, context=context, history=history)

    async def chat_free(
        self,
        user_message: str,
        history: Optional[List[Dict[str, str]]] = None,                                                  ) -> str:
        return await self.generate(user_message=user_message, context="", history=history)


class JuezClient:
    def __init__(self):
        self.url = JUEZ_URL                                                                                  self.temperature = JUEZ_TEMPERATURE
        self.max_tokens = JUEZ_MAX_TOKENS
        self._client: Optional[httpx.AsyncClient] = None
                                                                                                         async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(90.0, connect=10.0),
                limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
            )
        return self._client

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=15),
        retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.RequestError, httpx.TimeoutException)),
    )
    async def evaluate(
        self,
        aporte_text: str,
        challenge_context: str = "",
        existing_aportes: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        client = await self._get_client()
        messages: List[Dict[str, str]] = [{"role": "system", "content": JUEZ_SYSTEM_PROMPT}]
        user_content = f"Evalúa el siguiente aporte:\n\n{aporte_text}"
        if challenge_context:
            user_content = f"{challenge_context}\n\n{user_content}"
        if existing_aportes:
            recent = "\n".join(existing_aportes[:10])
            user_content += f"\n\nAportes recientes para detectar duplicados:\n{recent}"
        messages.append({"role": "user", "content": user_content})
        payload = {
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": False,
        }
        resp = await client.post(self.url, json=payload)
        resp.raise_for_status()
        data = resp.json()
        choices = data.get("choices", [])
        if not choices:
            return self._default_evaluation()
        content = choices[0].get("message", {}).get("content", "").strip()
        return self._parse_json_response(content)                                                    
    def _parse_json_response(self, raw: str) -> Dict[str, Any]:
        cleaned = raw.strip()                                                                                if cleaned.startswith("```json"):
            cleaned = cleaned[7:]                                                                            if cleaned.startswith("```"):
            cleaned = cleaned[3:]                                                                            if cleaned.endswith("```"):
            cleaned = cleaned[:-3]                                                                           cleaned = cleaned.strip()
        try:                                                                                                     result = json.loads(cleaned)
            return self._validate_evaluation(result)                                                         except json.JSONDecodeError:
            import re                                                                                            match = re.search(r'\{[^{}]*\}', cleaned)                                                            if match:                                                                                                try:                                                                                                     result = json.loads(match.group(0))                                                                  return self._validate_evaluation(result)                                                         except json.JSONDecodeError:                                                                             pass                                                                                         return self._default_evaluation()                                                                                                                                                                 def _validate_evaluation(self, raw: Dict[str, Any]) -> Dict[str, Any]:                                   quality = float(raw.get("quality_score", 5.0))
        quality = max(0.0, min(10.0, quality))                                                               impact = int(raw.get("impact_index", 5))
        impact = max(0, min(10, impact))                                                                     return {                                                                                                 "quality_score": round(quality, 2),                                                                  "reason": str(raw.get("reason", "Sin razón especificada"))[:200],                                    "is_duplicate": bool(raw.get("is_duplicate", False)),                                                "category": str(raw.get("category", "general")),                                                     "impact_index": impact,                                                                              "related_to_challenge": bool(raw.get("related_to_challenge", False)),                                "summary": str(raw.get("summary", ""))[:150],
        }                                                                                            
    def _default_evaluation(self) -> Dict[str, Any]:                                                         return {                                                                                                 "quality_score": 5.0,                                                                                "reason": "Evaluación por defecto",
            "is_duplicate": False,                                                                               "category": "general",
            "impact_index": 5,
            "related_to_challenge": False,                                                                       "summary": "",
        }                                                                                                                                                                                                                                                                                                      pensador = PensadorClient()                                                                          juez = JuezClient()
