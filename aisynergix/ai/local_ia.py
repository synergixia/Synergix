# =============================================================================
# SYNERGIX — local_ia.py
# Conector HTTP asíncrono para las IAs locales.
# Gestiona comunicación con el Juez (0.5B, :8080) y el Pensador (1.5B, :8081).
# Ambas IAs corren bajo Ollama en la red interna Docker.
# =============================================================================

import json
import logging
from typing import Any, Optional

import httpx

from aisynergix.config.constants import (
    IA_JUEZ_URL,
    IA_PENSADOR_URL,
    IA_TIMEOUT_SECONDS,
)
from aisynergix.config.system_prompts import (
    JUEZ_SYSTEM_PROMPT,
    PENSADOR_SYSTEM_PROMPT,
)

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# MODELOS INTERNOS (nombres registrados en Ollama)
# ─────────────────────────────────────────────
JUDGE_MODEL: str = "qwen2:0.5b"
THINKER_MODEL: str = "qwen2:1.5b"

# Endpoint de chat de Ollama
OLLAMA_CHAT_PATH: str = "/api/chat"
OLLAMA_TAGS_PATH: str = "/api/tags"


# =============================================================================
# CONTENEDOR TIPADO PARA LA RESPUESTA DEL JUEZ
# =============================================================================

class JudgeResult:
    """
    Contenedor tipado para la respuesta estructurada del modelo Juez.

    Atributos:
        calificacion (float):  Puntuación de calidad 0–10.
        validez_tecnica (bool): True si el contenido es técnicamente correcto.
        categoria (str):        Clasificación temática del aporte.
        raw (dict):             JSON crudo devuelto por el modelo.
    """

    def __init__(self, raw: dict[str, Any]) -> None:
        self.calificacion: float = float(raw.get("calificacion", 0))
        # El mega_prompt define validez_tecnica como booleano
        vt = raw.get("validez_tecnica", False)
        if isinstance(vt, str):
            self.validez_tecnica: bool = vt.lower() in ("true", "1", "sí", "si")
        else:
            self.validez_tecnica: bool = bool(vt)
        self.categoria: str = raw.get("categoria", "otro")
        self.raw: dict[str, Any] = raw

    def is_valid_for_rag(self, threshold: float = 7.0) -> bool:
        """True si el aporte supera el umbral mínimo para entrar al FAISS."""
        return self.calificacion >= threshold

    def __repr__(self) -> str:
        return (
            f"JudgeResult(calificacion={self.calificacion}, "
            f"validez_tecnica={self.validez_tecnica}, "
            f"categoria='{self.categoria}')"
        )


# =============================================================================
# FUNCIÓN AUXILIAR INTERNA — Llamada genérica a Ollama /api/chat
# =============================================================================

async def _call_ollama(
    base_url: str,
    model: str,
    system_prompt: str,
    user_message: str,
    timeout: int = IA_TIMEOUT_SECONDS,
) -> str:
    """
    Ejecuta una llamada HTTP POST al endpoint /api/chat de Ollama.

    Args:
        base_url:      URL base del servicio (ej: http://synergix-ia-juez:8080).
        model:         Nombre del modelo Ollama (ej: 'qwen2:0.5b').
        system_prompt: Prompt de sistema que define el comportamiento del modelo.
        user_message:  Mensaje del usuario a procesar.
        timeout:       Timeout máximo en segundos.

    Returns:
        Contenido textual de la respuesta (campo message.content).

    Raises:
        httpx.ConnectError:     Si la IA local no está accesible.
        httpx.TimeoutException: Si la respuesta supera el timeout.
        ValueError:             Si la respuesta no contiene contenido válido.
    """
    payload: dict[str, Any] = {
        "model": model,
        "stream": False,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_message},
        ],
    }

    url = f"{base_url}{OLLAMA_CHAT_PATH}"
    logger.debug(
        f"[LocalIA] POST {url} | modelo={model} | "
        f"msg_len={len(user_message)} chars"
    )

    async with httpx.AsyncClient(timeout=float(timeout)) as client:
        try:
            response = await client.post(url, json=payload)
            response.raise_for_status()

        except httpx.ConnectError as exc:
            logger.error(f"[LocalIA] Sin conexión a {base_url}: {exc}")
            raise

        except httpx.TimeoutException as exc:
            logger.error(f"[LocalIA] Timeout esperando a {base_url}: {exc}")
            raise

        except httpx.HTTPStatusError as exc:
            logger.error(
                f"[LocalIA] HTTP {exc.response.status_code} desde {base_url}: "
                f"{exc.response.text[:200]}"
            )
            raise

    try:
        data: dict = response.json()
    except json.JSONDecodeError as exc:
        logger.error(
            f"[LocalIA] Respuesta no JSON desde {base_url}: "
            f"{response.text[:200]} | {exc}"
        )
        raise ValueError(f"Respuesta inválida de Ollama ({base_url}): {response.text[:200]}")

    # Ollama /api/chat → {"message": {"role": "assistant", "content": "..."}}
    content: str = data.get("message", {}).get("content", "").strip()
    if not content:
        logger.warning(
            f"[LocalIA] Modelo {model} devolvió contenido vacío. "
            f"Data completa: {json.dumps(data)[:300]}"
        )
        raise ValueError(f"Modelo {model} devolvió contenido vacío.")

    logger.debug(f"[LocalIA] Respuesta de {model} ({len(content)} chars): {content[:120]}...")
    return content


# =============================================================================
# ASK_JUDGE — Evalúa un aporte con el modelo Juez
# =============================================================================

async def ask_judge(aporte_text: str) -> JudgeResult:
    """
    Envía un aporte al Juez (0.5B) para evaluación de calidad.

    El Juez está instruido para devolver SIEMPRE un JSON estricto con:
      - calificacion (int 0–10)
      - validez_tecnica (bool)
      - categoria (str)

    Si el servicio no está disponible, retorna un JudgeResult conservador
    con calificacion=0 en lugar de propagar la excepción (fail-safe).

    Args:
        aporte_text: Texto completo del aporte de conocimiento a evaluar.

    Returns:
        JudgeResult con la evaluación parseada.

    Raises:
        ValueError: Si el Juez devuelve una respuesta sin JSON parseable
                    y el servicio SÍ está disponible.
    """
    logger.info(
        f"[Juez] Iniciando evaluación de aporte "
        f"({len(aporte_text)} chars)..."
    )

    try:
        raw_response = await _call_ollama(
            base_url=IA_JUEZ_URL,
            model=JUDGE_MODEL,
            system_prompt=JUEZ_SYSTEM_PROMPT,
            user_message=aporte_text,
        )
    except (httpx.ConnectError, httpx.TimeoutException):
        # Fail-safe: si el Juez no responde, bloqueamos el aporte (score 0)
        logger.warning(
            "[Juez] Servicio no disponible. "
            "Retornando evaluación nula (fail-safe)."
        )
        return JudgeResult({
            "calificacion": 0,
            "validez_tecnica": False,
            "categoria": "otro",
        })

    # El modelo puede agregar texto antes/después del JSON; extraemos el bloque {}
    json_start = raw_response.find("{")
    json_end   = raw_response.rfind("}") + 1

    if json_start == -1 or json_end == 0:
        logger.error(
            f"[Juez] No se encontró JSON en la respuesta. "
            f"Respuesta cruda: {raw_response[:400]}"
        )
        raise ValueError(
            f"El Juez no devolvió JSON válido. Respuesta: {raw_response[:200]}"
        )

    json_str = raw_response[json_start:json_end]

    try:
        parsed: dict = json.loads(json_str)
    except json.JSONDecodeError as exc:
        logger.error(
            f"[Juez] JSON malformado extraído: {json_str[:300]} | Error: {exc}"
        )
        raise ValueError(f"El Juez devolvió JSON malformado: {exc}") from exc

    result = JudgeResult(parsed)
    logger.info(
        f"[Juez] ✅ Evaluación completada: {result}"
    )
    return result


# =============================================================================
# ASK_THINKER — Genera respuesta con el modelo Pensador
# =============================================================================

async def ask_thinker(
    user_message: str,
    rag_context: Optional[str] = None,
    user_language: str = "es",
) -> str:
    """
    Envía una pregunta al Pensador (1.5B) y obtiene una respuesta experta.

    Si se proporciona contexto RAG, se inyecta como prefijo en el mensaje
    para que el Pensador lo use como base de conocimiento colectivo.

    Args:
        user_message:  Pregunta o mensaje original del usuario.
        rag_context:   Fragmentos de conocimiento recuperados del FAISS.
                       None o vacío = sin contexto RAG.
        user_language: Código de idioma detectado (ej: 'es', 'en', 'pt').
                       Usado para reforzar la instrucción de idioma.

    Returns:
        Respuesta textual del Pensador como string listo para enviar.

    Raises:
        No propaga excepciones; devuelve mensajes de error amigables al usuario.
    """
    # Construir mensaje enriquecido con contexto RAG si existe
    if rag_context and rag_context.strip():
        enriched_message = (
            f"[CONTEXTO DEL CEREBRO COLECTIVO — usa este conocimiento como base]\n"
            f"{'─' * 50}\n"
            f"{rag_context.strip()}\n"
            f"{'─' * 50}\n\n"
            f"[PREGUNTA DEL USUARIO]\n"
            f"{user_message}"
        )
        logger.info(
            f"[Pensador] Pregunta con contexto RAG "
            f"({len(rag_context)} chars). Idioma detectado: {user_language}"
        )
    else:
        enriched_message = user_message
        logger.info(
            f"[Pensador] Pregunta sin contexto RAG "
            f"({len(user_message)} chars). Idioma: {user_language}"
        )

    try:
        response = await _call_ollama(
            base_url=IA_PENSADOR_URL,
            model=THINKER_MODEL,
            system_prompt=PENSADOR_SYSTEM_PROMPT,
            user_message=enriched_message,
        )
        return response

    except httpx.ConnectError:
        logger.error("[Pensador] Servicio no disponible (ConnectError).")
        return (
            "⚠️ El motor de inteligencia está temporalmente fuera de línea. "
            "Por favor, intenta de nuevo en unos momentos."
        )
    except httpx.TimeoutException:
        logger.error("[Pensador] Timeout esperando respuesta del modelo.")
        return (
            "⚠️ Se agotó el tiempo de espera al procesar tu consulta. "
            "El modelo está bajo alta carga. Intenta de nuevo en un momento."
        )
    except ValueError as exc:
        logger.error(f"[Pensador] Error de valor en respuesta: {exc}")
        return (
            "⚠️ Ocurrió un error interno al generar la respuesta. "
            "Por favor, reformula tu pregunta e intenta de nuevo."
        )


# =============================================================================
# HEALTH CHECK — Verifica disponibilidad de ambas IAs
# =============================================================================

async def check_ia_health() -> dict[str, bool]:
    """
    Verifica la disponibilidad de Juez y Pensador mediante GET /api/tags.

    Returns:
        {"juez": bool, "pensador": bool}
        True = servicio disponible y respondiendo 200.
    """
    health: dict[str, bool] = {"juez": False, "pensador": False}

    async with httpx.AsyncClient(timeout=5.0) as client:

        # ── Verificar Juez ──────────────────────────────────────────────
        try:
            resp = await client.get(f"{IA_JUEZ_URL}{OLLAMA_TAGS_PATH}")
            health["juez"] = resp.status_code == 200
            logger.info(
                f"[HealthCheck] Juez ({IA_JUEZ_URL}): "
                f"{'✅ OK' if health['juez'] else '❌ FAIL'}"
            )
        except Exception as exc:
            logger.warning(f"[HealthCheck] Juez no disponible: {exc}")

        # ── Verificar Pensador ──────────────────────────────────────────
        try:
            resp = await client.get(f"{IA_PENSADOR_URL}{OLLAMA_TAGS_PATH}")
            health["pensador"] = resp.status_code == 200
            logger.info(
                f"[HealthCheck] Pensador ({IA_PENSADOR_URL}): "
                f"{'✅ OK' if health['pensador'] else '❌ FAIL'}"
            )
        except Exception as exc:
            logger.warning(f"[HealthCheck] Pensador no disponible: {exc}")

    return health
