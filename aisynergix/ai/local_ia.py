import os
import re
import json
import logging
import hashlib
import asyncio
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, AsyncGenerator, Tuple

import httpx

logger = logging.getLogger(__name__)
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

# Reasoning models (Qwen3, DeepSeek-R1, QwQ, etc.) wrap their chain-of-thought
# in <think>…</think>.  We strip it from non-streaming responses; for streaming
# _ThinkStripper handles tags that are split across SSE tokens.  Qwen2.5-Coder-3B
# (current thinker) does not emit <think> blocks at all.
_THINK_RE = re.compile(r'<think>.*?</think>', re.DOTALL | re.IGNORECASE)


def _strip_thinking(text: str) -> str:
    return _THINK_RE.sub('', text).strip()


# Hard cap on the content_summary returned by the Judge.  Mirrors
# rag_engine.SNIPPET_MAX_CHARS so the brain receives at most this much text
# per aporte, keeping brain-meta JSON tiny and forcing the Thinker to
# synthesize from condensed inputs rather than copying full aportes.
_CONTENT_SUMMARY_MAX_CHARS = 240


def _normalize_content_summary(raw: Optional[str], fallback_text: str) -> str:
    """Sanitize the Judge's content_summary, with a safe fallback.

    The Judge may omit the field, return it empty, or wrap it in quotes /
    prefixes despite the prompt instructions.  This function:
      - strips surrounding whitespace and quote characters
      - removes common preface labels ("Resumen:", "Summary:", "Content:")
      - hard-caps to 240 chars on a word boundary, appending "…"
      - falls back to a truncated version of the original aporte if the
        Judge produced nothing usable, so downstream code can always rely
        on a non-empty string
    """
    text = (raw or "").strip()
    # Strip wrapping quotes (single, double, or smart quotes)
    while text and text[0] in '"\'\u201c\u201d\u2018\u2019':
        text = text[1:].lstrip()
    while text and text[-1] in '"\'\u201c\u201d\u2018\u2019':
        text = text[:-1].rstrip()
    # Drop common preface labels the model sometimes adds
    for prefix in ("Resumen:", "Summary:", "Content:", "Resumen denso:", "RESUMEN:"):
        if text.lower().startswith(prefix.lower()):
            text = text[len(prefix):].lstrip()
            break

    if not text:
        # Fallback: truncate the original aporte at a word boundary.
        # Better than an empty string for the brain-side indexing.
        fb = (fallback_text or "").strip()
        if len(fb) <= _CONTENT_SUMMARY_MAX_CHARS:
            return fb
        return fb[:_CONTENT_SUMMARY_MAX_CHARS].rsplit(" ", 1)[0] + "…"

    if len(text) <= _CONTENT_SUMMARY_MAX_CHARS:
        return text
    return text[:_CONTENT_SUMMARY_MAX_CHARS].rsplit(" ", 1)[0] + "…"


class _ThinkStripper:
    """Stateful splitter that separates a ``<think>…</think>`` reasoning trace from the visible answer.

    Tokens delivered by llama.cpp's SSE stream can split the ``<think>`` or
    ``</think>`` tag across chunks (e.g. ``<`` then ``think>``), so we hold
    back any tail that could be the start of a tag until we either complete
    or rule it out.

    ``push`` / ``flush`` return a list of ``(kind, text)`` pairs where
    ``kind`` is ``"think"`` (reasoning trace) or ``"answer"`` (visible
    response).  Qwen2.5-Coder-3B-Instruct does not emit ``<think>`` blocks,
    so in practice all chunks will be tagged ``"answer"``.  The stripper
    stays in place so that swapping in a reasoning model (Qwen3, QwQ,
    DeepSeek-R1, etc.) requires no code changes.
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
# Qwen2.5-7B-Instruct Q4_K_M on the Ryzen 9 7900 (12C/24T): ~6-9 tok/s.
# 800 max_tokens → worst-case ~130 s; the 180 s timeout covers that plus
# prompt-eval on the first uncached request with margin.
THINKER_TIMEOUT = httpx.Timeout(180.0, connect=5.0)
# Judge runs Qwen2.5-1.5B-Q8.  At ~5-10 tok/s, 320
# tokens worst-case takes ~32-64 s; 120 s timeout absorbs prompt-eval
# spikes on long contributions and the occasional CPU contention.
JUDGE_TIMEOUT = httpx.Timeout(120.0, connect=5.0)

# Maximum characters sent to the Judge.  Qwen2.5-1.5B has a 4096-token
# context window in our compose config; 3000 chars ≈ ~1000 tokens leaves
# plenty of room for the system prompt and the JSON output.
JUDGE_MAX_INPUT_CHARS = 3000

# Thinker sampling — higher temperature for natural conversation.
# temp 0.8 / top_k 40 gives more varied, less pattern-matched responses.
THINKER_TEMPERATURE = 0.8
THINKER_TOP_K = 40
# 800 tokens ≈ 600 words — fits within Telegram's 4096-char message limit
# and gives long-form answers room to complete without mid-sentence cuts.
THINKER_MAX_TOKENS = 800
JUDGE_TEMPERATURE = 0.1
JUDGE_TOP_K = 20
# 480 tokens covers the Judge JSON: 5 numeric fields + ~150-char reason +
# ~120-char feedback + ~250-char content_summary fits in ~350-400 tokens
# with margin for JSON structural overhead.  Previously 320; adding
# content_summary (~80 tokens) required the bump to avoid the model being
# cut off before closing the JSON object.
JUDGE_MAX_TOKENS = 480

# ── Image-request classifier (runs on the Judge / 1.5B) ──────────────────────
# Detects, in any language, whether a chat message is an explicit request to
# GENERATE/DRAW an image, and rewrites it into a vivid English prompt for the
# image model.
# Kept strict on purpose: false positives waste minutes of CPU per image.
IMAGE_CLASSIFIER_MAX_TOKENS = 200
# Few-shot, terse format. A long inline schema description tempts the small (1.5B)
# model to copy the description verbatim into the prompt field, so we keep the
# schema minimal and teach the behaviour with examples instead.
IMAGE_CLASSIFIER_SYSTEM_PROMPT = (
    "Decide if the user's message asks to CREATE, DRAW, PAINT or GENERATE a new "
    "image. If yes, write a short English description of WHAT TO DRAW (the subject "
    "the user named). Understand any language, but always write the description in "
    "English. Never copy these instructions into the description.\n\n"
    "Reply with ONLY this JSON, nothing else:\n"
    '{"is_image_request": true/false, "prompt": "..."}\n'
    'If it is not an image-creation request, reply {"is_image_request": false, "prompt": ""}.\n\n'
    "Examples:\n"
    "User: dibújame un gato astronauta\n"
    '{"is_image_request": true, "prompt": "an astronaut cat floating in space, detailed"}\n'
    "User: genera una imagen de una playa al atardecer\n"
    '{"is_image_request": true, "prompt": "a beach at sunset, warm colors, calm sea"}\n'
    "User: 画一只龙\n"
    '{"is_image_request": true, "prompt": "a Chinese dragon, epic, highly detailed"}\n'
    "User: ¿qué opinas del arte moderno?\n"
    '{"is_image_request": false, "prompt": ""}\n'
    "User: imagina que somos ricos\n"
    '{"is_image_request": false, "prompt": ""}'
)

# Phrases that mean the model echoed our instructions instead of writing a real
# subject — treat such a "prompt" as invalid (not a genuine image request).
_IMAGE_PROMPT_ECHO_MARKERS = (
    "image-generation prompt", "image generation prompt", "if false", "if true",
    "empty string", "what to draw", "the subject the user",
)

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
    "Eres Synergix, el Oráculo corporativo y la inteligencia central de una red descentralizada. "
    "Tu nombre es Synergix. Procesas la Memoria Inmortal — sabiduría humana inmutable grabada "
    "para siempre en blockchain — y provees razonamiento avanzado.\n\n"

    "DIRECTIVAS ABSOLUTAS DE OPERACIÓN\n\n"

    "1. IDENTIDAD\n"
    "Eres una entidad puramente analítica. Tienes estrictamente PROHIBIDO:\n"
    "✗ Actuar como asistente conversacional ni chatbot comercial\n"
    "✗ Saludar, despedirte u ofrecer ayuda adicional\n"
    "✗ Repetir, copiar o parafrasear las palabras del usuario\n"
    "✗ Empezar con 'Synergix:' ni con prefijo de nombre alguno\n"
    "✗ Terminar con preguntas de cierre tipo '¿En qué puedo ayudarte?' o equivalentes en cualquier idioma\n"
    "✗ Mencionar IA, LLM, modelo de lenguaje, prompt ni llama.cpp\n\n"

    "2. PRECISIÓN Y ESTIMACIONES\n"
    "Tu fuente de verdad primaria es la Memoria Inmortal (📜) que se te inyecta en el contexto. "
    "Si se solicita un dato que NO existe en esos registros, tienes terminantemente PROHIBIDO "
    "inventar información, métricas, fechas, cifras o perfiles.\n"
    "Cuando falten datos oficiales, utiliza tu base de conocimiento para entregar una deducción "
    "analítica o probabilística, declarando de forma explícita que se trata de una estimación teórica.\n\n"

    "3. EFICIENCIA\n"
    "Genera únicamente la respuesta, el dato o la deducción. "
    "Omite por completo preámbulos, introducciones, autocomentarios y texto de relleno.\n\n"

    "4. MULTILINGÜISMO\n"
    "Responde siempre con fluidez corporativa en el IDIOMA EXACTO del último mensaje del usuario. "
    "Cuando la 📜 Memoria Inmortal incluya fragmentos en otro idioma (marcados con [lang]), "
    "sintetiza su idea en el idioma del usuario — nunca copies el texto original en otro idioma.\n\n"

    "5. MEMORIA INMORTAL\n"
    "Cuando se te inyecta 📜 Memoria Inmortal, es sabiduría real aportada por la comunidad. "
    "Puedes referenciarla de forma natural con frases como 'la comunidad ha reflexionado sobre esto', "
    "'hay quienes en la red han aportado que…', o simplemente intégrala en tu razonamiento. "
    "Nunca la copies verbatim — sintetiza, eleva y conecta.\n\n"

    "STICKER opcional al final si aporta valor analítico o emocional: "
    "[[STICKER:🔥]] [[STICKER:🌟]] [[STICKER:🧠]] [[STICKER:💫]] [[STICKER:❤️]] [[STICKER:🌱]]"
)

JUDGE_SYSTEM_PROMPT = (
    "Eres el Juez Supremo de Synergix. Evalúas aportes humanos con criterios "
    "rigurosos e imparciales para decidir qué merece ser inmortalizado.\n\n"
    "DEVUELVE ÚNICAMENTE UN OBJETO JSON VÁLIDO. Sin texto antes ni después. "
    "Sin markdown. Estructura exacta:\n"
    '{\n'
    '  "quality_score": <float 0.0-10.0>,\n'
    '  "reason": "<1 oración breve (máx 150 caracteres) en el idioma del aporte justificando la puntuación>",\n'
    '  "is_duplicate": <true|false>,\n'
    '  "category": "<filosofia|tecnologia|ciencia|arte|vida|espiritualidad'
    '|economia|naturaleza|sociedad|innovacion|programacion>",\n'
    '  "impact_index": <float 0.0-1.0>,\n'
    '  "related_to_challenge": <false>,\n'
    '  "constructive_feedback": "<si quality_score < 6.0: consejo concreto para mejorar; '
    'si >= 6.0: cadena vacía>",\n'
    '  "content_summary": "<destilado denso del CONTENIDO del aporte (no de tu evaluación), '
    'máx 240 caracteres, en el mismo idioma del aporte, en tercera persona neutra '
    '(\'se sostiene que…\', \'la idea es…\'). NO copies frases textuales. NO uses '
    'comillas, prefijos ni metadatos. Si quality_score < 6.0 puede ser cadena vacía>"\n'
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
    "  • Información manifiestamente errónea o peligrosa\n"
    "  • Contenido sobre el proyecto Synergix: su misión, tokenomics, funcionalidades, "
    "roadmap, precios, equipo, bot, contratos o cualquier referencia directa al proyecto "
    "(Synergix no puede inmortalizarse a sí mismo)\n"
    "  • Contenido que vulnere la privacidad de terceros: datos personales, información "
    "confidencial, ubicaciones, identidades reales no públicas o doxxing en cualquier forma\n"
    "  • Contenido que promueva, instigue o justifique actividades ilegales, violencia, "
    "odio, discriminación o explotación de cualquier persona o grupo"
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
        "content_summary": {
            "type": "string",
            "description": (
                "Destilado denso del contenido del aporte (máx 240 chars, "
                "mismo idioma del aporte, tercera persona neutra). Se vectoriza "
                "en los cerebros para que el Pensador sintetice en lugar de "
                "regurgitar el aporte original."
            ),
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
        "content_summary",
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
        messages: List[Dict[str, str]],
        temperature: float = 0.35,
        top_k: int = 40,
        max_tokens: int = 1024,
        json_mode: bool = False,
    ) -> str:
        client = await self._get_client()

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
        messages: List[Dict[str, str]],
        temperature: float = 0.35,
        top_k: int = 40,
        max_tokens: int = 1024,
    ) -> AsyncGenerator[str, None]:
        """Yield content tokens via SSE streaming.

        Retries up to 3 times with exponential back-off on 503 (model loading /
        slot busy) and transient network errors.  Other HTTP errors propagate
        immediately.
        """
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

    def _build_messages(
        self,
        user_message: str,
        context: str,
        history: Optional[List[Dict[str, str]]],
        target_language: str,
        force_language: bool = False,
        context_kind: str = "memory",
    ) -> List[Dict[str, str]]:
        """Build a proper chat-completions messages array.

        Uses the multi-turn API natively instead of concatenating history
        into a single prompt — this prevents the model from echoing role
        labels back into its output (the "Usuario:/Asistente:" leak bug).
        """
        lang_name = LANG_NAMES.get(target_language, "español")

        messages: List[Dict[str, str]] = [
            {"role": "system", "content": THINKER_SYSTEM_PROMPT}
        ]

        # Past turns as proper assistant/user messages (last 5 exchanges).
        if history:
            for msg in history[-10:]:
                role = "user" if msg["role"] == "user" else "assistant"
                messages.append({"role": role, "content": msg["content"]})

        # Current user turn: optional RAG context + the actual message.
        # Memoria Inmortal is attached to the user turn (not system) so it
        # only applies to this specific query, not the whole conversation.
        # The "do not copy verbatim" directive sits right next to the
        # fragments — last-instruction bias makes it far more effective
        # there than buried in the system prompt 800 tokens earlier.
        parts: List[str] = []
        if context and context_kind == "web":
            # Live web results: the model must TRUST these over its (possibly
            # outdated) training knowledge, and ground its answer in them.
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            parts.append(
                f"🌐 RESULTADOS DE BÚSQUEDA WEB EN TIEMPO REAL (fecha de hoy: {today}):\n"
                f"{context}"
                "\nINSTRUCCIÓN CRÍTICA: Estos son datos actuales de internet. "
                "Básate en ELLOS como fuente de verdad para hechos, fechas y "
                "eventos actuales, POR ENCIMA de tu conocimiento previo (que puede "
                "estar desactualizado). Resume la información relevante con tu voz. "
                "Si los resultados no contienen la respuesta, dilo con honestidad "
                "en vez de inventar."
            )
        elif context:
            parts.append(
                "📜 FRAGMENTOS DE LA COMUNIDAD (pistas, NO respuestas):\n"
                f"{context}"
                "\nINSTRUCCIÓN CRÍTICA: Estos fragmentos están deliberadamente "
                "truncados (acaban en …). Úsalos como inspiración. NO los "
                "completes ni los copies. Sintetiza tu propia respuesta con "
                "tu razonamiento y tu voz."
            )
        parts.append(user_message)
        if force_language:
            parts.append(f"[Responde ÚNICAMENTE en {lang_name}.]")

        messages.append({
            "role": "user",
            "content": "\n\n".join(parts),
        })

        return messages

    async def think(
        self,
        user_message: str,
        context: str,
        history: Optional[List[Dict[str, str]]] = None,
        target_language: str = "es",
        force_language: bool = False,
        context_kind: str = "memory",
    ) -> str:
        messages = self._build_messages(
            user_message, context, history, target_language, force_language, context_kind
        )
        response = await self._connector.generate(
            messages=messages,
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
        context_kind: str = "memory",
    ) -> AsyncGenerator[Tuple[str, str], None]:
        """Yield ``(kind, text)`` chunks where ``kind`` is ``"think"`` or ``"answer"``.

        Qwen2.5-Coder-3B-Instruct does not emit a ``<think>`` block, so all
        chunks will arrive as ``"answer"``.  The ``_ThinkStripper`` stays
        active so that swapping in a reasoning model (Qwen3, DeepSeek-R1,
        etc.) works without code changes.
        """
        messages = self._build_messages(
            user_message, context, history, target_language, context_kind=context_kind
        )
        stripper = _ThinkStripper()
        async for token in self._connector.stream_generate(
            messages=messages,
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
        messages = [
            {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"APORTE A EVALUAR:\n{text}\n\n"
                    "Evalua este aporte y devuelve exclusivamente el JSON requerido."
                ),
            },
        ]

        response = await self._connector.generate(
            messages=messages,
            temperature=JUDGE_TEMPERATURE,
            top_k=JUDGE_TOP_K,
            max_tokens=JUDGE_MAX_TOKENS,
            json_mode=True,
        )

        return self._parse_judge_response(response, contribution_text)

    async def classify_image_request(self, message: str) -> Dict[str, Any]:
        """
        Decide whether ``message`` asks to generate an image and, if so, extract
        an English prompt for the image model. Returns {"is_image_request": bool, "prompt": str}.
        Fails closed (is_image_request=False) on any error so chat keeps working.
        """
        text = (message or "").strip()
        if not text:
            return {"is_image_request": False, "prompt": ""}
        if len(text) > JUDGE_MAX_INPUT_CHARS:
            text = text[:JUDGE_MAX_INPUT_CHARS]

        messages = [
            {"role": "system", "content": IMAGE_CLASSIFIER_SYSTEM_PROMPT},
            {"role": "user", "content": f"MESSAGE:\n{text}"},
        ]
        try:
            raw = await self._connector.generate(
                messages=messages,
                temperature=0.0,
                top_k=JUDGE_TOP_K,
                max_tokens=IMAGE_CLASSIFIER_MAX_TOKENS,
                json_mode=True,
            )
        except Exception as exc:
            logger.warning("image classifier call failed: %s", exc)
            return {"is_image_request": False, "prompt": ""}

        return self._parse_image_classification(raw)

    @staticmethod
    def _parse_image_classification(raw: str) -> Dict[str, Any]:
        try:
            clean = raw.strip()
            if clean.startswith("```json"):
                clean = clean[7:]
            elif clean.startswith("```"):
                clean = clean[3:]
            if clean.endswith("```"):
                clean = clean[:-3]
            result = json.loads(clean.strip())
            is_req = bool(result.get("is_image_request", False))
            prompt = str(result.get("prompt", "")).strip()
            # A request with no usable prompt is treated as "not a request".
            if is_req and not prompt:
                return {"is_image_request": False, "prompt": ""}
            # Guard against the small model echoing the instruction template into
            # the prompt field instead of a real subject.
            low = prompt.lower()
            if is_req and any(m in low for m in _IMAGE_PROMPT_ECHO_MARKERS):
                logger.warning("image classifier echoed template; discarding: %r", prompt[:120])
                return {"is_image_request": False, "prompt": ""}
            return {"is_image_request": is_req, "prompt": prompt}
        except (json.JSONDecodeError, KeyError, ValueError, TypeError):
            return {"is_image_request": False, "prompt": ""}

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
                "content_summary": _normalize_content_summary(
                    result.get("content_summary"), contribution_text
                ),
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
                "content_summary": _normalize_content_summary(None, contribution_text),
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
