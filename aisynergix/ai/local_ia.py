"""
local_ia.py — Conector HTTP asíncrono para las IAs locales de Synergix.
Gestiona la comunicación con el Juez (Qwen 0.5B, puerto 8080) y el Pensador (Qwen 1.5B/3B, puerto 8081).
Implementa resiliencia, control de timeouts y parseo estricto para salidas JSON.
"""

import json
import logging
from typing import Any, Optional, Dict
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

# Modelos locales a utilizar (alineados con Ollama/llama.cpp)
JUDGE_MODEL = "qwen2.5:0.5b"
THINKER_MODEL = "qwen2.5:1.5b"

class JudgeResult:
    """Estructura de datos tipada para la validación del modelo Juez."""
    def __init__(self, raw: Dict[str, Any]) -> None:
        try:
            self.calificacion = float(raw.get("calificacion", 0.0))
        except (ValueError, TypeError):
            self.calificacion = 0.0
            
        vt = raw.get("validez_tecnica", False)
        self.validez_tecnica = vt if isinstance(vt, bool) else str(vt).lower() in ("true", "1", "si", "sí")
        self.categoria = str(raw.get("categoria", "otro"))
        self.raw = raw

    def __repr__(self) -> str:
        return f"JudgeResult(score={self.calificacion}, valid={self.validez_tecnica}, cat={self.categoria})"

async def _ollama_request(base_url: str, model: str, system: str, prompt: str, require_json: bool = False) -> str:
    """Ejecuta una petición asíncrona a la API HTTP de Ollama/Llama-server."""
    url = f"{base_url}/api/chat"
    payload = {
        "model": model,
        "stream": False,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt}
        ]
    }
    
    # Forzar formato JSON en el Juez si el modelo soporta format="json"
    if require_json:
        payload["format"] = "json"

    async with httpx.AsyncClient(timeout=IA_TIMEOUT_SECONDS) as client:
        try:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
            return data.get("message", {}).get("content", "").strip()
        except httpx.ReadTimeout:
            logger.error(f"[IA Local] Timeout excedido ({IA_TIMEOUT_SECONDS}s) al contactar {model} en {base_url}")
            raise
        except httpx.HTTPError as e:
            logger.error(f"[IA Local] Error HTTP contactando {model}: {e}")
            raise

async def ask_judge(text: str) -> JudgeResult:
    """
    Envía un aporte al Juez (0.5B) para evaluación.
    Busca, extrae y parsea el primer bloque JSON de la respuesta.
    """
    try:
        logger.debug(f"[Juez] Evaluando aporte de {len(text)} caracteres...")
        raw_response = await _ollama_request(
            base_url=IA_JUEZ_URL, 
            model=JUDGE_MODEL, 
            system=JUEZ_SYSTEM_PROMPT, 
            prompt=text,
            require_json=True
        )
        
        # Limpieza y extracción estricta de JSON en caso de que el modelo alucine texto extra
        start = raw_response.find("{")
        end = raw_response.rfind("}") + 1
        
        if start == -1 or end == 0:
            raise ValueError("No se encontró una estructura JSON en la respuesta del Juez.")
            
        json_str = raw_response[start:end]
        data = json.loads(json_str)
        
        result = JudgeResult(data)
        logger.info(f"[Juez] Evaluación completada: {result}")
        return result
        
    except json.JSONDecodeError as e:
        logger.error(f"[Juez] Error decodificando JSON: {e} | Raw: {raw_response}")
        return JudgeResult({"calificacion": 0, "validez_tecnica": False, "categoria": "error_parseo"})
    except Exception as e:
        logger.error(f"[Juez] Error general: {e}")
        return JudgeResult({"calificacion": 0, "validez_tecnica": False, "categoria": "error_red"})

async def ask_thinker(prompt: str, context: Optional[str] = None) -> str:
    """
    Envía una consulta al Pensador (1.5B/3B).
    Si se provee contexto (vía RAG), lo inyecta en el prompt.
    """
    if context:
        full_prompt = (
            f"Contexto recuperado del Cerebro Colmena:\n{context}\n\n"
            f"Pregunta del usuario:\n{prompt}\n\n"
            f"Responde basándote estrictamente en el contexto cuando sea aplicable. "
            f"Si el contexto no ayuda, utiliza tu conocimiento general, pero aclara que no está en los registros."
        )
    else:
        full_prompt = prompt

    try:
        logger.debug(f"[Pensador] Generando respuesta (Contexto: {'Sí' if context else 'No'})")
        response = await _ollama_request(
            base_url=IA_PENSADOR_URL, 
            model=THINKER_MODEL, 
            system=PENSADOR_SYSTEM_PROMPT, 
            prompt=full_prompt
        )
        return response
    except Exception as e:
        logger.error(f"[Pensador] Error en inferencia: {e}")
        return "⚠️ *Anomalía detectada.* Mis nodos cognitivos locales están temporalmente inaccesibles. Intenta de nuevo en unos instantes."
