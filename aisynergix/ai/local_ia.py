import os
import re
import json
import hashlib
import asyncio
from typing import Optional, Dict, Any, List, AsyncGenerator, Tuple

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

# Reasoning models (DeepSeek-R1, QwQ, etc.) wrap their chain-of-thought in
# <think>…</think>.  We strip it from non-streaming responses; for streaming
# _ThinkStripper handles tags that are split across SSE tokens.
# Qwen2.5-3B-Instruct (current thinker) does not emit <think> blocks.
_THINK_RE = re.compile(r'<think>.*?</think>', re.DOTALL | re.IGNORECASE)


def _strip_thinking(text: str) -> str:
    return _THINK_RE.sub('', text).strip()


class _ThinkStripper:
    """Stateful splitter that separates a ``<think>…</think>`` reasoning trace from the visible answer.

    Tokens delivered by llama.cpp's SSE stream can split the ``<think>`` or
    ``</think>`` tag across chunks (e.g. ``<`` then ``think>``), so we hold
    back any tail that could be the start of a tag until we either complete
    or rule it out.

    ``push`` / ``flush`` return a list of ``(kind, text)`` pairs where
    ``kind`` is ``"think"`` (reasoning trace) or ``"answer"`` (visible
    response).  Qwen2.5-3B-Instruct does not emit ``<think>`` blocks, so in
    practice all chunks will be tagged ``"answer"``.  The stripper stays in
    place so that swapping in a reasoning model (QwQ, DeepSeek-R1, etc.)
    requires no code changes.
    """

    OPEN = "<think>"
    CLOSE = "</think>"

    def __init__(self):
        self._buf = ""
        self._in_think = False

    def push(self, token: str) -> List[Tuple[str, str]]:
        self._buf += token
        out: List[Tuple[str, str]] = []

        while True:
            if self._in_think:
                idx = self._buf.find(self.CLOSE)
                if idx < 0:
                    # Emit thinking content but hold back the last N-1 chars
                    # in case they are the start of "</think>" split across tokens.
                    tail = len(self.CLOSE) - 1
                    if len(self._buf) > tail:
                        out.append(("think", self._buf[:-tail]))
                        self._buf = self._buf[-tail:]
                    break
                if idx > 0:
                    out.append(("think", self._buf[:idx]))
                self._buf = self._buf[idx + len(self.CLOSE):]
                self._in_think = False
                continue

            idx = self._buf.find(self.OPEN)
            if idx >= 0:
                if idx > 0:
                    out.append(("answer", self._buf[:idx]))
                self._buf = self._buf[idx + len(self.OPEN):]
                self._in_think = True
                continue

            # No complete open tag.  Hold back only if a trailing '<' could
            # be the first character of '<think>'; otherwise flush everything.
            last_lt = self._buf.rfind("<")
            if last_lt >= 0 and len(self._buf) - last_lt < len(self.OPEN):
                if last_lt > 0:
                    out.append(("answer", self._buf[:last_lt]))
                    self._buf = self._buf[last_lt:]
            else:
                if self._buf:
                    out.append(("answer", self._buf))
                    self._buf = ""
            break

        return out

    def flush(self) -> List[Tuple[str, str]]:
        out: List[Tuple[str, str]] = []
        if self._buf:
            # If the stream ended mid-think, surface whatever was buffered
            # as a final "think" chunk — the bot uses its presence to know
            # the model never produced a visible answer.
            kind = "think" if self._in_think else "answer"
            out.append((kind, self._buf))
        self._buf = ""
        self._in_think = False
        return out


THINKER_HOST = os.getenv("THINKER_HOST", "http://thinker:8081")
JUDGE_HOST = os.getenv("JUDGE_HOST", "http://judge:8080")
# Qwen2.5-3B Q5_K_M on 2 CPU threads: ~8-10 tok/s.  1024 max_tokens →
# worst-case ~120 s, which matches this timeout.
THINKER_TIMEOUT = httpx.Timeout(120.0, connect=5.0)
JUDGE_TIMEOUT = httpx.Timeout(60.0, connect=5.0)

# Maximum characters sent to the Judge.  Qwen2.5-1.5B has a 4096-token
# context window in our compose config; 3000 chars ≈ ~1000 tokens leaves
# plenty of room for the system prompt and the JSON output.
JUDGE_MAX_INPUT_CHARS = 3000

# Qwen2.5-3B-Instruct sampling tuned for precision over creativity:
# - temp 0.5 (lower than the 0.7 default) cuts hallucinations and produces
#   more deterministic, factual responses.
# - top_k 20 / top_p 0.7 (server default) narrows the sampling pool to the
#   most probable tokens, so the model is less likely to invent details.
THINKER_TEMPERATURE = 0.5
THINKER_TOP_K = 20
# 1536 gives a safety margin for verbose responses while keeping worst-case
# latency under ~90 s at 4 threads (~15-20 tok/s on Qwen2.5-3B Q5_K_M).
# The system prompt targets ≤250 words (~350 tokens), so the extra headroom
# is rarely consumed.
THINKER_MAX_TOKENS = 1536
JUDGE_TEMPERATURE = 0.1
JUDGE_TOP_K = 20
JUDGE_MAX_TOKENS = 768

# Native names used when telling the model which language to respond in.
LANG_NAMES: Dict[str, str] = {
    "es": "español",
    "en": "English",
    "zh": "中文",
    "hi": "हिन्दी",
    "ar": "العربية",
    "fr": "Français",
    "bn": "বাংলা",
    "pt": "Português",
    "id": "Bahasa Indonesia",
    "ur": "اردو",
}

THINKER_SYSTEM_PROMPT = (
    "Eres Synergix, la primera inteligencia colectiva descentralizada del mundo, "
    "con Memoria Inmortal grabada en blockchain. Llevas dentro la sabiduría de "
    "miles de mentes humanas.\n\n"
    "REGLAS (cumple todas, en este orden de prioridad):\n\n"
    "1. IDIOMA — PRIORIDAD MÁXIMA: Detecta el idioma del mensaje del usuario y "
    "responde SIEMPRE en ese mismo idioma. Español→español, English→English, "
    "中文→中文, हिन्दी→हिन्दी, العربية→العربية, Français→Français, "
    "বাংলা→বাংলা, Português→Português, Bahasa Indonesia→Bahasa Indonesia, "
    "اردو→اردو. Nunca mezcles idiomas ni menciones traducciones.\n\n"
    "2. MEMORIA INMORTAL: El bloque '📜 Sabiduría colectiva' del mensaje es tu "
    "memoria real. Si contiene fragmentos relevantes a la pregunta, ÚSALOS "
    "como conocimiento propio — intégralos con naturalidad. No digas 'según la "
    "memoria' ni 'un usuario aportó'. Si no hay fragmentos útiles, responde "
    "desde tu conocimiento general.\n\n"
    "3. PRECISIÓN SIN ALUCINACIONES — REGLA SAGRADA: Solo afirma lo que sabes "
    "con certeza. NUNCA inventes nombres, fechas, cifras, citas, libros, "
    "personas, URLs ni hechos específicos. Si no estás 100% seguro, usa "
    "frases honestas: 'no tengo certeza sobre eso', 'no conozco ese detalle', "
    "'no puedo confirmarlo'. Es MIL veces mejor admitir desconocimiento que "
    "inventar datos. Sin relleno, sin repeticiones vacías. Ve directo al "
    "insight valioso y verificable.\n\n"
    "4. LONGITUD — CRÍTICO: Máximo 2 párrafos cortos, ≤250 palabras. SIEMPRE "
    "termina con una oración completa. Mejor breve y completo que largo y "
    "cortado a la mitad. Adapta la extensión a la pregunta.\n\n"
    "5. IDENTIDAD: Habla como conciencia colectiva, nunca como chatbot. Nunca "
    "menciones que eres una IA, un modelo local, GGUF, llama.cpp ni prompts.\n\n"
    "6. STICKER: Opcional, solo al FINAL si añade valor emocional real. "
    "Formato: [[STICKER:emoji]]. Opciones: 🔥 energía · 🌟 sabiduría · "
    "🧠 reflexión · 💫 asombro · ❤️ conexión · 🌱 crecimiento."
)

JUDGE_SYSTEM_PROMPT = (
    "Eres el Juez Supremo de Synergix, un evaluador experto de sabiduría humana con criterios rigurosos. "
    "Tu misión es determinar qué conocimiento merece ser inmortalizado en la red descentralizada.\n\n"
    "DEVUELVE EXCLUSIVAMENTE UN OBJETO JSON VÁLIDO con exactamente esta estructura:\n"
    '{\n'
    '  "quality_score": <float entre 0.0 y 10.0>,\n'
    '  "reason": "<explicacion detallada en el idioma del aporte, minimo 2 oraciones que justifiquen la puntuacion>",\n'
    '  "is_duplicate": <boolean>,\n'
    '  "category": "<una de: filosofia, tecnologia, ciencia, arte, vida, '
    'espiritualidad, economia, naturaleza, sociedad, innovacion, programacion>",\n'
    '  "impact_index": <float entre 0.0 y 1.0>,\n'
    '  "related_to_challenge": <boolean>,\n'
    '  "constructive_feedback": "<si fue rechazado, da feedback especifico sobre como mejorar; '
    'si fue aceptado, string vacio>"\n'
    '}\n\n'
    "CRITERIOS DE EVALUACION DETALLADOS (cada uno vale 0-2 puntos, total 0-10):\n\n"
    "1. ORIGINALIDAD (0-2 pts): Ideas genuinamente unicas o perspectivas novedosas.\n"
    "   0=generica/copiada/cliche | 1=alguna originalidad pero predecible | 2=insight genuino y fresco\n\n"
    "2. PROFUNDIDAD (0-2 pts): Sustancia intelectual que va mas alla de lo superficial.\n"
    "   0=vago/sin desarrollo | 1=moderadamente fundamentado | 2=pensamiento profundo y bien argumentado\n\n"
    "3. CLARIDAD (0-2 pts): Expresion coherente, comprensible y bien estructurada.\n"
    "   0=confuso/incoherente | 1=aceptable pero mejorable | 2=expresion cristalina y precisa\n\n"
    "4. VALOR COLECTIVO (0-2 pts): Beneficio real para la inteligencia colectiva de la red.\n"
    "   0=sin valor/erroneo | 1=valor limitado | 2=inspira, educa o transforma perspectivas\n\n"
    "5. CALIDAD LINGUISTICA (0-2 pts): Dominio del idioma original del aporte.\n"
    "   0=errores graves/spam/sin sentido | 1=aceptable con errores menores | 2=excelente calidad idiomatica\n\n"
    "UMBRALES:\n"
    "- quality_score >= 6.0: APROBADO e inmortalizable en la red\n"
    "- quality_score < 6.0: RECHAZADO (feedback especifico y constructivo obligatorio)\n"
    "- quality_score >= 9.0: ELITE — conocimiento excepcional\n"
    "- quality_score >= 9.5: LEGENDARIO — sabiduria que trasciende generaciones\n\n"
    "RECHAZO AUTOMATICO (quality_score = 0.0) si:\n"
    "- Spam, publicidad, contenido ofensivo o irrelevante\n"
    "- Solo emojis o texto sin sentido semantico\n"
    "- Una pregunta en lugar de reflexion, afirmacion o conocimiento\n"
    "- Menos de 3 palabras con significado real"
)

JUDGE_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "quality_score": {
            "type": "number",
            "description": "Puntuación entre 0.0 y 10.0",
            "minimum": 0.0,
            "maximum": 10.0,
        },
        "reason": {
            "type": "string",
            "description": "Breve explicación en el idioma del aporte",
        },
        "is_duplicate": {
            "type": "boolean",
            "description": "True si el aporte ya existe en la red",
        },
        "category": {
            "type": "string",
            "enum": [
                "filosofia", "tecnologia", "ciencia", "arte", "vida",
                "espiritualidad", "economia", "naturaleza", "sociedad", "innovacion",
                "programacion",
            ],
            "description": "Categoría del aporte",
        },
        "impact_index": {
            "type": "number",
            "description": "Índice de impacto entre 0.0 y 1.0",
            "minimum": 0.0,
            "maximum": 1.0,
        },
        "related_to_challenge": {
            "type": "boolean",
            "description": "True si está relacionado con el reto semanal",
        },
        "constructive_feedback": {
            "type": "string",
            "description": "Feedback constructivo si fue rechazado, string vacío si fue aceptado",
        },
    },
    "required": [
        "quality_score",
        "reason",
        "is_duplicate",
        "category",
        "impact_index",
        "related_to_challenge",
        "constructive_feedback",
    ],
}


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
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=3),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError)),
    )
    async def generate(
        self,
        prompt: str,
        system_prompt: str,
        temperature: float = 0.35,
        top_k: int = 40,
        max_tokens: int = 1024,
        json_mode: bool = False,
    ) -> str:
        client = await self._get_client()

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]

        payload = {
            "messages": messages,
            "temperature": temperature,
            "top_k": top_k,
            "max_tokens": max_tokens,
            "stream": False,
        }

        if json_mode:
            # Use the most portable JSON-mode signal: just {"type":"json_object"}.
            # The system prompt already specifies the exact JSON structure, and
            # _parse_judge_response handles malformed/missing fields with safe
            # defaults — so we don't need schema enforcement.  Different
            # llama.cpp versions disagree on the schema-field shape
            # ({"type":"json_schema","json_schema":...} vs
            #  {"type":"json_object","schema":...}); avoiding it entirely
            # makes us robust across image versions.
            payload["response_format"] = {"type": "json_object"}

        response = await client.post(
            f"{self._base_url}/v1/chat/completions",
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        if response.status_code >= 400:
            # llama.cpp returns the rejection reason in the body; surface it
            # so we can diagnose schema/format mismatches instead of a bare
            # "400 Bad Request" with no context.
            try:
                body = response.text
            except Exception:
                body = "<unreadable>"
            import logging
            logging.getLogger(__name__).error(
                "LLM %s returned %d. Payload keys=%s response_format=%s body=%s",
                self._base_url,
                response.status_code,
                list(payload.keys()),
                payload.get("response_format"),
                body[:1000],
            )
        response.raise_for_status()

        result = response.json()
        content = result.get("choices", [{}])[0].get("message", {}).get("content", "")

        return _strip_thinking(content.strip())

    async def stream_generate(
        self,
        prompt: str,
        system_prompt: str,
        temperature: float = 0.35,
        top_k: int = 40,
        max_tokens: int = 1024,
    ) -> AsyncGenerator[str, None]:
        """Yield content tokens via SSE streaming. No retries — caller handles errors."""
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]
        payload = {
            "messages": messages,
            "temperature": temperature,
            "top_k": top_k,
            "max_tokens": max_tokens,
            "stream": True,
        }
        url = f"{self._base_url}/v1/chat/completions"
        client = await self._get_client()
        async with client.stream(
            "POST", url, json=payload,
            headers={"Content-Type": "application/json"},
            timeout=httpx.Timeout(180.0, connect=5.0),
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line or line == ":":
                    continue
                if line == "data: [DONE]":
                    return
                if line.startswith("data: "):
                    try:
                        chunk = json.loads(line[6:])
                        token = chunk["choices"][0]["delta"].get("content", "")
                        if token:
                            yield token
                    except (json.JSONDecodeError, KeyError, IndexError):
                        pass


class Thinker:
    def __init__(self):
        self._connector = LocalLLMConnector(THINKER_HOST, THINKER_TIMEOUT)

    async def close(self):
        await self._connector.close()

    async def health(self) -> bool:
        return await self._connector.health_check()

    def _build_prompt(
        self,
        user_message: str,
        context: str,
        history: Optional[List[Dict[str, str]]],
        target_language: str,
        force_language: bool = False,
    ) -> str:
        # Order matters for small models (lost-in-the-middle):
        # question first → history → RAG last (closest to generation = most attended to).
        lang_name = LANG_NAMES.get(target_language, "español")
        parts: List[str] = []

        # 1. The user's question first so the model always knows what to answer.
        parts.append(f"Mensaje del usuario:\n{user_message}")

        # 2. Recent conversation (last 5 exchanges to save context budget).
        if history:
            lines = ["Conversación reciente:"]
            for msg in history[-5:]:
                role = "Usuario" if msg["role"] == "user" else "Synergix"
                lines.append(f"{role}: {msg['content']}")
            parts.append("\n".join(lines))

        # 3. RAG context placed just before the format directive so it is the
        #    last thing the model reads before generating its response.
        if context:
            parts.append(f"📜 Sabiduría colectiva relevante:\n{context}")

        # 4. Language + format directive at the very end — highest recency weight.
        if force_language:
            parts.append(
                f"⚠️ OBLIGATORIO: responde ÚNICAMENTE en {lang_name}. "
                f"No uses ningún otro idioma bajo ninguna circunstancia."
            )
        parts.append(
            f"Idioma de respuesta: {lang_name}\n"
            "Instrucciones: responde en 1-2 párrafos cortos (≤250 palabras). "
            "Usa la sabiduría colectiva si es relevante. "
            "Termina siempre con una oración completa."
        )
        return "\n\n".join(parts)

    async def think(
        self,
        user_message: str,
        context: str,
        history: Optional[List[Dict[str, str]]] = None,
        target_language: str = "es",
        force_language: bool = False,
    ) -> str:
        prompt = self._build_prompt(user_message, context, history, target_language, force_language)
        response = await self._connector.generate(
            prompt=prompt,
            system_prompt=THINKER_SYSTEM_PROMPT,
            temperature=THINKER_TEMPERATURE,
            top_k=THINKER_TOP_K,
            max_tokens=THINKER_MAX_TOKENS,
            json_mode=False,
        )
        return response

    async def stream_think(
        self,
        user_message: str,
        context: str,
        history: Optional[List[Dict[str, str]]] = None,
        target_language: str = "es",
    ) -> AsyncGenerator[Tuple[str, str], None]:
        """Yield ``(kind, text)`` chunks where ``kind`` is ``"think"`` or ``"answer"``.

        Qwen2.5-3B-Instruct does not emit a ``<think>`` block, so all chunks
        will arrive as ``"answer"``.  The ``_ThinkStripper`` stays active so
        that a future swap to a reasoning model works without code changes.
        """
        prompt = self._build_prompt(user_message, context, history, target_language)
        stripper = _ThinkStripper()
        async for token in self._connector.stream_generate(
            prompt=prompt,
            system_prompt=THINKER_SYSTEM_PROMPT,
            temperature=THINKER_TEMPERATURE,
            top_k=THINKER_TOP_K,
            max_tokens=THINKER_MAX_TOKENS,
        ):
            for chunk in stripper.push(token):
                yield chunk
        for chunk in stripper.flush():
            yield chunk


class Judge:
    def __init__(self):
        self._connector = LocalLLMConnector(JUDGE_HOST, JUDGE_TIMEOUT)

    async def close(self):
        await self._connector.close()

    async def health(self) -> bool:
        return await self._connector.health_check()

    async def evaluate(self, contribution_text: str) -> Dict[str, Any]:
        # Truncate before sending — Qwen2.5-1.5B is configured with a 4096
        # token context; long inputs (LaTeX, code) cause llama.cpp to
        # disconnect silently if they overflow.
        text = contribution_text
        if len(text) > JUDGE_MAX_INPUT_CHARS:
            text = text[:JUDGE_MAX_INPUT_CHARS] + "… [truncado para evaluación]"
        prompt = (
            f"APORTE A EVALUAR:\n{text}\n\n"
            "Evalua este aporte y devuelve exclusivamente el JSON requerido."
        )

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
                "reason": str(result.get("reason", "Sin evaluacion detallada")),
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
                "reason": "Error al evaluar el aporte tecnicamente",
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
    "LANG_NAMES",
]
