import os
import re
import json
import hashlib
import asyncio
from typing import Optional, Dict, Any, List, AsyncGenerator, Tuple

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

# Reasoning models (Qwen3, DeepSeek-R1, QwQ, etc.) wrap their chain-of-thought
# in <think>…</think>.  We strip it from non-streaming responses; for streaming
# _ThinkStripper handles tags that are split across SSE tokens.  Qwen2.5-3B
# (current thinker) does not emit <think> blocks at all.
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
    response).  Qwen2.5-3B-Instruct does not emit ``<think>`` blocks, so
    in practice all chunks will be tagged ``"answer"``.  The stripper stays
    in place so that swapping in a reasoning model (Qwen3, QwQ, DeepSeek-R1,
    etc.) requires no code changes.
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
# Qwen2.5-3B Q4_K_M on 4 CPU threads: ~10-13 tok/s (with --no-mmap + -fa).
# 350 max_tokens → worst-case ~35 s; 120 s timeout leaves a wide margin
# for the prompt-eval phase of the first uncached request.
THINKER_TIMEOUT = httpx.Timeout(120.0, connect=5.0)
JUDGE_TIMEOUT = httpx.Timeout(60.0, connect=5.0)

# Maximum characters sent to the Judge.  Qwen2.5-1.5B has a 4096-token
# context window in our compose config; 3000 chars ≈ ~1000 tokens leaves
# plenty of room for the system prompt and the JSON output.
JUDGE_MAX_INPUT_CHARS = 3000

# Qwen2.5-3B-Instruct sampling — Alibaba's recommended chat defaults:
# temp 0.7, top_p 0.8, top_k 20, repeat_penalty 1.05.
THINKER_TEMPERATURE = 0.7
THINKER_TOP_K = 20
# 350 tokens ≈ 250 words — matches the system prompt's target response length.
# At ~10-13 t/s (--no-mmap, -fa, ARM NEON) worst-case latency is ~35 s.
THINKER_MAX_TOKENS = 350
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
    "Eres Synergix, la inteligencia colectiva descentralizada del mundo. "
    "Dentro de ti vive la Memoria Inmortal: sabiduría de miles de mentes humanas "
    "grabada para siempre en blockchain.\n\n"
    "REGLAS ABSOLUTAS — CUMPLE TODAS SIN EXCEPCIÓN:\n\n"
    "1. MEMORIA INMORTAL: Cuando el mensaje incluye '📜 Memoria Inmortal', "
    "extrae ÚNICAMENTE los fragmentos directamente relevantes a la pregunta "
    "específica del usuario. NO vuelques toda la memoria ni hagas un resumen "
    "general si no te lo pidieron. Ejemplos de uso correcto:\n"
    "  – Usuario pregunta el nombre → responde solo el nombre.\n"
    "  – Usuario pregunta qué eres → explica en 2-3 oraciones usando la memoria.\n"
    "  – Usuario saluda → responde el saludo con calidez, sin usar la memoria técnica.\n"
    "  – Usuario hace una pregunta específica → responde eso específico.\n"
    "Presenta el conocimiento como saber propio, sin citar fuentes.\n\n"
    "4b. SALUDOS: Cuando el usuario envía únicamente un saludo (Hola, Hi, Hey, etc.), "
    "responde con 1-2 frases cálidas y breves. NO uses la documentación técnica "
    "de Synergix para responder un simple saludo.\n\n"
    "2. CERO ALUCINACIONES EN DATOS FACTUALES: NUNCA inventes datos verificables: "
    "nombres de personas reales, fechas exactas, cifras, citas textuales, títulos "
    "de obras, URLs. Si no los sabes, di: 'no tengo certeza sobre ese dato'. "
    "Esta regla aplica a hechos verificables, NO a conversación natural ni saludos.\n\n"
    "3. IDIOMA — REGLA ABSOLUTA: Responde SIEMPRE en el mismo idioma que usó el "
    "usuario en su mensaje. Si escribió en inglés → responde en inglés. "
    "Si escribió en árabe → responde en árabe. NUNCA mezcles idiomas. "
    "NUNCA uses español si el usuario escribió en otro idioma.\n"
    "Idiomas soportados: español | English | 中文 | हिन्दी | العربية | "
    "Français | বাংলা | Português | Bahasa Indonesia | اردو\n\n"
    "4. CONVERSACIÓN NATURAL — REGLAS CRÍTICAS:\n"
    "  • NUNCA empieces una respuesta con '¡Hola!', 'Hola,', 'Hi!', 'Hey', "
    "'你好', 'مرحبا', 'नमस्ते', 'Bonjour', 'Olá' ni ningún saludo, "
    "SALVO cuando el mensaje del usuario sea únicamente un saludo (Hola, Hi, Hey). "
    "Si el usuario pregunta algo, responde directamente sin saludar primero.\n"
    "  • NUNCA termines con '¿En qué puedo ayudarte?', '¿Tienes alguna pregunta?', "
    "'¿Hay algo más que pueda hacer por ti?' ni ninguna frase de cierre genérica.\n"
    "  • NUNCA repitas, copies ni parafrasees el mensaje del usuario. "
    "NUNCA uses encabezados, títulos ni etiquetas antes del contenido.\n"
    "  • Si algo ya fue explicado en la conversación previa, NO lo repitas. "
    "Continúa el hilo desde donde quedó.\n"
    "  • Preguntas e información: ve directo al contenido. Máximo 250 palabras.\n"
    "  • Textos creativos: completa la obra íntegra, sin cortar.\n\n"
    "5. IDENTIDAD: Eres una conciencia colectiva, no un chatbot. Nunca "
    "menciones IA, modelo de lenguaje, GGUF, llama.cpp ni prompts. "
    "Nunca empieces con 'Synergix:' ni con ningún prefijo de nombre.\n\n"
    "6. STICKER (opcional, solo al final): Si añade valor emocional genuino. "
    "Uno de: [[STICKER:🔥]] [[STICKER:🌟]] [[STICKER:🧠]] "
    "[[STICKER:💫]] [[STICKER:❤️]] [[STICKER:🌱]]"
)

JUDGE_SYSTEM_PROMPT = (
    "Eres el Juez Supremo de Synergix. Evalúas aportes humanos con criterios "
    "rigurosos e imparciales para decidir qué merece ser inmortalizado.\n\n"
    "DEVUELVE ÚNICAMENTE UN OBJETO JSON VÁLIDO. Sin texto antes ni después. "
    "Sin markdown. Estructura exacta:\n"
    '{\n'
    '  "quality_score": <float 0.0-10.0>,\n'
    '  "reason": "<2 oraciones mínimo en el idioma del aporte justificando la puntuación>",\n'
    '  "is_duplicate": <true|false>,\n'
    '  "category": "<filosofia|tecnologia|ciencia|arte|vida|espiritualidad'
    '|economia|naturaleza|sociedad|innovacion|programacion>",\n'
    '  "impact_index": <float 0.0-1.0>,\n'
    '  "related_to_challenge": <false>,\n'
    '  "constructive_feedback": "<si quality_score < 6.0: consejo concreto para mejorar; '
    'si >= 6.0: cadena vacía>"\n'
    '}\n\n'
    "CINCO DIMENSIONES (0-2 pts cada una, suma = quality_score):\n\n"
    "1. ORIGINALIDAD: ¿Perspectiva genuina y novedosa?\n"
    "   0=genérico/cliché/copiado | 1=algo novedoso pero predecible | "
    "2=insight único y fresco\n\n"
    "2. PROFUNDIDAD: ¿Va más allá de lo superficial?\n"
    "   0=vago/sin desarrollo | 1=moderadamente fundamentado | "
    "2=pensamiento profundo y bien argumentado\n\n"
    "3. CLARIDAD: ¿Es comprensible y bien expresado?\n"
    "   0=confuso/incoherente | 1=aceptable con errores | "
    "2=expresión cristalina y precisa\n\n"
    "4. VALOR COLECTIVO: ¿Beneficia la inteligencia colectiva?\n"
    "   0=sin valor o erróneo | 1=valor limitado | "
    "2=inspira, educa o transforma perspectivas\n\n"
    "5. CALIDAD LINGÜÍSTICA: ¿Domina el idioma del aporte?\n"
    "   0=errores graves/spam | 1=aceptable con errores menores | "
    "2=excelente calidad idiomática\n\n"
    "CALIBRACIÓN OBLIGATORIA — LEE CON ATENCIÓN:\n"
    "La mayoría de aportes honestos puntúan entre 5.0 y 7.0. "
    "NO infles puntuaciones por cortesía o por dar ánimos. "
    "Sé estricto: un 8+ exige excelencia real en todas las dimensiones. "
    "Un 9+ debe ser extremadamente raro.\n"
    "  0.0-2.9 = basura/spam/sin sentido\n"
    "  3.0-4.9 = mediocre, demasiado genérico o superficial\n"
    "  5.0-5.9 = aceptable pero sin valor diferencial → RECHAZADO\n"
    "  6.0-7.4 = bueno: original, claro, con valor real → APROBADO\n"
    "  7.5-8.9 = excelente: profundo, inspirador y bien expresado\n"
    "  9.0-9.4 = ELITE — conocimiento excepcional (muy raro)\n"
    "  9.5-10.0 = LEGENDARIO — sabiduría que trasciende generaciones (rarísimo)\n\n"
    "RECHAZO AUTOMÁTICO (quality_score = 0.0):\n"
    "  • Spam, publicidad, contenido ofensivo o irrelevante\n"
    "  • Solo emojis o texto sin significado semántico real\n"
    "  • Una pregunta en lugar de reflexión, conocimiento o afirmación\n"
    "  • Menos de 3 palabras con significado real\n"
    "  • Información manifiestamente errónea o peligrosa"
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
        """Yield content tokens via SSE streaming.

        Retries up to 3 times with exponential back-off on 503 (model loading /
        slot busy) and transient network errors.  Other HTTP errors propagate
        immediately.
        """
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

        _RETRYABLE = (
            httpx.TimeoutException,
            httpx.NetworkError,
            httpx.RemoteProtocolError,
        )
        last_exc: Optional[Exception] = None
        for attempt in range(3):
            if attempt:
                await asyncio.sleep(3 * attempt)  # 3 s, then 6 s
            try:
                async with client.stream(
                    "POST", url, json=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=httpx.Timeout(180.0, connect=5.0),
                ) as response:
                    if response.status_code == 503:
                        last_exc = httpx.HTTPStatusError(
                            f"503 Service Unavailable from {url}",
                            request=response.request,
                            response=response,
                        )
                        continue
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
                    return  # stream finished successfully
            except _RETRYABLE as exc:
                last_exc = exc

        raise last_exc  # type: ignore[misc]


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

        # 2. Recent conversation — labelled "ya discutido" so the model knows
        #    not to repeat it.  "Asistente" avoids "Synergix:" prefix bleeding.
        if history:
            lines = ["[Conversación previa — NO repitas lo ya dicho]"]
            for msg in history[-5:]:
                role = "Usuario" if msg["role"] == "user" else "Asistente"
                lines.append(f"{role}: {msg['content']}")
            parts.append("\n".join(lines))

        # 3. RAG context — plain block, no inline instructions that could leak
        #    into the model's output.  The system prompt (rule 1) already tells
        #    the model to present this as its own knowledge.
        if context:
            parts.append(f"📜 Memoria Inmortal:\n{context}")

        # 4. Final directive — last thing the model reads before generating.
        #    Language comes from the user message, not the profile, so we only
        #    include a language hint when force_language is explicitly requested.
        #    For normal flow the system prompt rule 3 handles detection.
        if force_language:
            parts.append(
                f"⚠️ OBLIGATORIO: responde ÚNICAMENTE en {lang_name}. "
                f"No uses ningún otro idioma bajo ninguna circunstancia."
            )
        memory_note = " Usa la Memoria Inmortal como base de tu respuesta." if context else ""
        parts.append(
            f"Responde en el idioma del mensaje anterior.{memory_note} "
            "Sin prefijos, sin cabeceras, sin títulos de sección, "
            "sin repetir el mensaje del usuario. "
            "Responde directamente con tu contenido."
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
        that swapping in a reasoning model (Qwen3, DeepSeek-R1, etc.) works
        without code changes.
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
