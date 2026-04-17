"""
Conector asíncrono a los servidores llama‑server (C++ puro).
Comunica con el Pensador (8081) y el Juez (8080) mediante httpx.
"""

import asyncio
import json
import logging
import random
from typing import Any, Dict, List, Optional

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger("synergix.ai.local")

# ──────────────────────────────────────────────────────────────────────────────
# CONFIGURACIÓN DE LOS SERVICIOS IA LOCAL
# ──────────────────────────────────────────────────────────────────────────────

PENSADOR_URL = "http://synergix-ia-pensador:8081/v1/chat/completions"
JUEZ_URL = "http://synergix-ia-juez:8080/v1/chat/completions"

# Parámetros por defecto (spec oficial)
PENSADOR_TEMPERATURE = 0.3
PENSADOR_TOP_K = 40
JUEZ_TEMPERATURE = 0.1

# Timeouts (segundos)
CONNECT_TIMEOUT = 10.0
READ_TIMEOUT = 120.0
WRITE_TIMEOUT = 30.0

# Número máximo de tokens generados
MAX_TOKENS = 1024


# ──────────────────────────────────────────────────────────────────────────────
# CLIENTE HTTP CON REINTENTOS
# ──────────────────────────────────────────────────────────────────────────────

class LlamaServerClient:
    """
    Cliente robusto para servidores llama‑server con reintentos exponenciales.
    """

    def __init__(
        self,
        base_url: str,
        default_temperature: float,
        default_top_k: Optional[int] = None,
    ):
        self.base_url = base_url
        self.default_temperature = default_temperature
        self.default_top_k = default_top_k
        self._client: Optional[httpx.AsyncClient] = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            timeout = httpx.Timeout(
                connect=CONNECT_TIMEOUT,
                read=READ_TIMEOUT,
                write=WRITE_TIMEOUT,
                pool=None,
            )
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=timeout,
                headers={"User-Agent": "Synergix-NodoFantasma/1.0"},
            )
        return self._client

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    async def call(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        top_k: Optional[int] = None,
        max_tokens: int = MAX_TOKENS,
        json_mode: bool = False,
    ) -> str:
        """
        Realiza una llamada al endpoint /v1/chat/completions del servidor.
        Si json_mode=True, se añade una directiva para forzar JSON.
        Retorna el contenido de la respuesta (texto plano o JSON string).
        """
        client = await self._ensure_client()
        payload = {
            "messages": messages,
            "temperature": temperature if temperature is not None else self.default_temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if self.default_top_k is not None:
            payload["top_k"] = top_k if top_k is not None else self.default_top_k
        if json_mode:
            # Forzar JSON en la respuesta (algunos servidores lo soportan)
            payload["response_format"] = {"type": "json_object"}

        retryer = AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=1, max=10),
            retry=retry_if_exception_type(
                (httpx.NetworkError, httpx.TimeoutException, httpx.HTTPStatusError)
            ),
            reraise=True,
        )

        async for attempt in retryer:
            with attempt:
                try:
                    response = await client.post("", json=payload)
                    response.raise_for_status()
                    data = response.json()
                    content = data["choices"][0]["message"]["content"]
                    # Limpieza básica
                    content = content.strip()
                    if json_mode:
                        # Eliminar posibles marcas de código
                        content = content.replace("```json", "").replace("```", "").strip()
                    return content
                except (KeyError, IndexError, json.JSONDecodeError) as e:
                    logger.error(
                        "Respuesta inválida del servidor IA: %s, payload: %s",
                        e,
                        json.dumps(payload, ensure_ascii=False)[:200],
                    )
                    raise ValueError(f"Respuesta inválida del servidor IA: {e}")

        raise RuntimeError("Unreachable")


# ──────────────────────────────────────────────────────────────────────────────
# INSTANCIAS GLOBALES (Pensador y Juez)
# ──────────────────────────────────────────────────────────────────────────────

_pensador_client: Optional[LlamaServerClient] = None
_juez_client: Optional[LlamaServerClient] = None


async def get_pensador() -> LlamaServerClient:
    global _pensador_client
    if _pensador_client is None:
        _pensador_client = LlamaServerClient(
            base_url=PENSADOR_URL,
            default_temperature=PENSADOR_TEMPERATURE,
            default_top_k=PENSADOR_TOP_K,
        )
    return _pensador_client


async def get_juez() -> LlamaServerClient:
    global _juez_client
    if _juez_client is None:
        _juez_client = LlamaServerClient(
            base_url=JUEZ_URL,
            default_temperature=JUEZ_TEMPERATURE,
            default_top_k=None,  # El juez no necesita top_k
        )
    return _juez_client


async def close_ia_clients():
    """Cierra los clientes HTTP (llamar al apagado)."""
    global _pensador_client, _juez_client
    if _pensador_client:
        await _pensador_client.close()
        _pensador_client = None
    if _juez_client:
        await _juez_client.close()
        _juez_client = None


# ──────────────────────────────────────────────────────────────────────────────
# FUNCIONES PÚBLICAS
# ──────────────────────────────────────────────────────────────────────────────

async def call_thinker(
    prompt: str,
    system_prompt: Optional[str] = None,
    temperature: Optional[float] = None,
    top_k: Optional[int] = None,
) -> str:
    """
    Llama al Pensador (qwen2.5‑1.5b) con un prompt de usuario.
    system_prompt se inyecta como mensaje de sistema si se proporciona.
    """
    client = await get_pensador()
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})
    try:
        return await client.call(messages, temperature=temperature, top_k=top_k)
    except Exception as e:
        logger.error("Error llamando al Pensador: %s", e)
        # Respuesta de fallback amigable
        return "🤖 *Pensador temporalmente indisponible.*\n\nIntenta de nuevo en un momento o reformula tu pregunta. 🔄"


async def call_judge(content: str) -> Dict[str, Any]:
    """
    Llama al Juez (qwen2.5‑0.5b) para evaluar un aporte.
    Retorna un dict con las claves: score, reason, category, knowledge_tag.
    """
    client = await get_juez()
    system_prompt = (
        "Eres curador de conocimiento Synergix. Evalúa el siguiente aporte en una escala de 1‑10 "
        "considerando originalidad, utilidad y claridad. "
        "Responde **EXCLUSIVAMENTE** con un JSON válido que tenga exactamente estas claves:\n"
        '{"score": N, "reason": "explicación breve", "category": "categoría", "knowledge_tag": "etiqueta"}\n'
        "No añadas texto fuera del JSON. category debe ser uno de: General, Tecnología, Finanzas, Salud, Arte, Ciencia."
    )
    user_prompt = f"Aporte a evaluar:\n\n{content}"
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    try:
        raw = await client.call(messages, json_mode=True)
        # Parsear JSON
        result = json.loads(raw)
        # Validar estructura
        required = {"score", "reason", "category", "knowledge_tag"}
        if not all(k in result for k in required):
            raise ValueError(f"Faltan claves requeridas: {result}")
        # Asegurar tipos
        result["score"] = int(result["score"])
        if not (1 <= result["score"] <= 10):
            result["score"] = max(1, min(10, result["score"]))
        result["reason"] = str(result["reason"])
        result["category"] = str(result["category"])
        result["knowledge_tag"] = str(result["knowledge_tag"])
        logger.debug("✅ Juez evaluó: score=%d, category=%s", result["score"], result["category"])
        return result
    except Exception as e:
        logger.error("Error llamando al Juez: %s", e)
        # Fallback seguro
        return {
            "score": 6,
            "reason": "Evaluación automática (fallback)",
            "category": "General",
            "knowledge_tag": "general",
        }


async def health_check() -> Dict[str, bool]:
    """
    Verifica que ambos servidores IA estén respondiendo.
    Retorna un dict con status del Pensador y del Juez.
    """
    results = {}
    for name, client_getter, test_prompt in [
        ("pensador", get_pensador, "Hola"),
        ("juez", get_juez, "Test"),
    ]:
        try:
            client = await client_getter()
            # Llamada rápida de prueba
            messages = [{"role": "user", "content": test_prompt}]
            await client.call(messages, max_tokens=5)
            results[name] = True
        except Exception:
            results[name] = False
    return results
