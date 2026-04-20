import json
import logging
import os
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
# CONFIGURACIÓN DE LOS SERVICIOS IA LOCAL (A través de las IPs de Docker DNS)
# ──────────────────────────────────────────────────────────────────────────────

PENSADOR_URL = os.getenv("PENSADOR_URL", "http://synergix-ia-pensador:8081") + "/v1/chat/completions"
JUEZ_URL = os.getenv("JUEZ_URL", "http://synergix-ia-juez:8080") + "/v1/chat/completions"

PENSADOR_TEMPERATURE = 0.3
PENSADOR_TOP_K = 40
JUEZ_TEMPERATURE = 0.1
MAX_TOKENS = 1024

class LlamaServerClient:
    """Cliente HTTP robusto con Exponential Backoff para interactuar con C++ Llama."""
    
    def __init__(self, base_url: str, default_temperature: float, default_top_k: Optional[int] = None):
        self.base_url = base_url
        self.default_temperature = default_temperature
        self.default_top_k = default_top_k
        self._client: Optional[httpx.AsyncClient] = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        """Creación diferida (Lazy) del pool TLS asíncrono para Llama-Server."""
        if self._client is None:
            timeout = httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=None)
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=timeout,
                headers={"User-Agent": "Synergix-GhostNode/2.0"},
            )
        return self._client

    async def aclose(self):
        """Apagado elegante de sockets."""
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
        """Se conecta al completion endpoint de llama.cpp v1."""
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
            payload["response_format"] = {"type": "json_object"}

        retryer = AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=1, max=10),
            retry=retry_if_exception_type((httpx.NetworkError, httpx.TimeoutException, httpx.HTTPStatusError)),
            reraise=True,
        )

        async for attempt in retryer:
            with attempt:
                try:
                    res = await client.post("", json=payload)
                    res.raise_for_status()
                    content = res.json()["choices"][0]["message"]["content"].strip()
                    
                    if json_mode:
                        content = content.replace("```json", "").replace("```", "").strip()
                    return content
                    
                except (KeyError, IndexError, json.JSONDecodeError) as e:
                    logger.error(f"Caos en la matrix (Llama-Server falló parseo): {e}")
                    raise ValueError(f"Respuesta rota de IA: {e}")

        raise RuntimeError("Jamás debe llegar a esta línea")


# ──────────────────────────────────────────────────────────────────────────────
# SINGLETONS Y EXPORTACIONES PÚBLICAS
# ──────────────────────────────────────────────────────────────────────────────

_pensador_client: Optional[LlamaServerClient] = None
_juez_client: Optional[LlamaServerClient] = None

async def get_pensador() -> LlamaServerClient:
    global _pensador_client
    if _pensador_client is None:
        _pensador_client = LlamaServerClient(PENSADOR_URL, PENSADOR_TEMPERATURE, PENSADOR_TOP_K)
    return _pensador_client

async def get_juez() -> LlamaServerClient:
    global _juez_client
    if _juez_client is None:
        _juez_client = LlamaServerClient(JUEZ_URL, JUEZ_TEMPERATURE, None)
    return _juez_client

async def close_ia_clients():
    global _pensador_client, _juez_client
    if _pensador_client: await _pensador_client.aclose()
    if _juez_client: await _juez_client.aclose()
    _pensador_client = _juez_client = None

# ──────────────────────────────────────────────────────────────────────────────
# INTERFACES DE LLAMADO FÁCIL (CON REGLAS DEUX-EX-MACHINA DEL JUEZ)
# ──────────────────────────────────────────────────────────────────────────────

async def call_thinker(prompt: str, idioma: str = "es", system_prompt: Optional[str] = None) -> str:
    """Invoca al Qwen2.5 1.5B (El Razonador) con soporte multi-idioma."""
    client = await get_pensador()
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    else:
        messages.append({
            "role": "system", 
            "content": f"Eres el Pensador de Synergix, la suma de todo conocimiento humano. Responde obligatoriamente en el idioma: {idioma}. Se sabio y conciso."
        })
    messages.append({"role": "user", "content": prompt})
    
    try:
        return await client.call(messages)
    except Exception as e:
        logger.error(f"Pensador Indispuesto: {e}")
        return "🤖 *El Pensador está temporalmente indisponible.* 🔄"

# CÓDIGO DEL JUEZ RENOMBRADO PARA ENCAJAR CON BOT.PY
async def get_juez_evaluation(content: str, tema_challenge: str = "", idioma_usuario: str = "es") -> Dict[str, Any]:
    """
    Invoca al Qwen2.5 0.5B (El Juez Evaluador Rápido).
    Aplica el sistema de calificación estricto REDISEÑADO hacia JSON puro.
    """
    client = await get_juez()
    system_prompt = f"""ERES EL JUEZ SUPREMO DE SYNERGIX.
Tu misión es evaluar aportes de texto según su originalidad, técnica y profundidad.
IDIOMA DE USUARIO: {idioma_usuario.upper()}. Debes escribir "summary_user_lang" y "reason" en {idioma_usuario.upper()}.
TEMA SEMANAL ACTUAL PARA EL CHALLENGE: "{tema_challenge}".

ESTRICTO: DEBES RETORNAR UN FORMATO JSON EXACTO CON ESTAS ÚNICAS 7 LLAVES (NADA DE TEXTO FUERA DEL JSON):
{{
  "approved": true,
  "quality_score": 8,
  "category": "tecnología",
  "impact_index": 0.5,
  "related_to_challenge": false,
  "summary_user_lang": "resumen corto aquí",
  "reason": "explicación breve de por qué se asignó el puntaje"
}}

REGLAS DE CALIFICACIÓN:
- approved: booleano (true si tiene sentido. false si es puro spam, insultos o un 'hola').
- quality_score: número entero (0 a 10). 8 es Aporte de élite, 9 y 10 es Aporte legendario.
- impact_index: decimal (0.0 a 1.0).
- related_to_challenge: booleano. Sólo true si el contenido dialoga directa o periféricamente con "{tema_challenge}".
"""
    
    try:
        raw_json = await client.call(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Aporte humano a evaluar:\n\n{content}"}
            ],
            json_mode=True
        )
        
        # Validación de Integridad Estricta de la Respuesta
        result = json.loads(raw_json)
        required = {"approved", "quality_score", "category", "impact_index", "related_to_challenge", "summary_user_lang", "reason"}
        
        # Corrección autónoma anti-alucinaciones Llama.cpp (Para que Python nunca colapse)
        for key in required:
            if key not in result:
                if key == "approved": result[key] = True
                elif key == "quality_score": result[key] = 5
                elif key == "impact_index": result[key] = 0.5
                elif key == "related_to_challenge": result[key] = False
                elif key == "category": result[key] = "General"
                else: result[key] = "..."
                
        # Coerción de Tipos (En caso el LLM envíe strings donde van ints o bools)
        if isinstance(result["quality_score"], str):
             try: result["quality_score"] = int(result["quality_score"])
             except Exception: result["quality_score"] = 5
             
        result["quality_score"] = max(0, min(10, int(result["quality_score"])))
        if not isinstance(result["approved"], bool): result["approved"] = str(result["approved"]).lower() == "true"
        if not isinstance(result["related_to_challenge"], bool): result["related_to_challenge"] = str(result["related_to_challenge"]).lower() == "true"
        
        return result
        
    except Exception as e:
        logger.error(f"Fallo masivo en El Juez: {e}. Usando salvavidas...")
        return {
            "approved": False,
            "quality_score": 0,
            "reason": "Evaluación de red automática fallida (El Nodo AI está procesando demasiadas peticiones).",
            "category": "General",
            "impact_index": 0.0,
            "related_to_challenge": False,
            "summary_user_lang": ""
        }

# Alias para scripts legados
call_judge = get_juez_evaluation
get_pensador_chat = call_thinker
