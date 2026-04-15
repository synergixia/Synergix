"""
local_ia.py — Conector HTTP asíncrono para las IAs locales.
Gestiona la comunicación con el Juez (0.5B, :8080) y el Pensador (1.5B, :8081).
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

# Modelos Ollama
JUDGE_MODEL = "qwen2:0.5b"
THINKER_MODEL = "qwen2:1.5b"

class JudgeResult:
    def __init__(self, raw: Dict[str, Any]) -> None:
        self.calificacion = float(raw.get("calificacion", 0))
        vt = raw.get("validez_tecnica", False)
        self.validez_tecnica = vt if isinstance(vt, bool) else str(vt).lower() in ("true", "1", "si", "sí")
        self.categoria = str(raw.get("categoria", "otro"))
        self.raw = raw

    def __repr__(self) -> str:
        return f"JudgeResult(score={self.calificacion}, valid={self.validez_tecnica}, cat={self.categoria})"

async def _ollama_request(base_url: str, model: str, system: str, prompt: str) -> str:
    url = f"{base_url}/api/chat"
    payload = {
        "model": model,
        "stream": False,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt}
        ]
    }
    async with httpx.AsyncClient(timeout=float(IA_TIMEOUT_SECONDS)) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()
        return response.json().get("message", {}).get("content", "").strip()

async def ask_judge(text: str) -> JudgeResult:
    try:
        raw = await _ollama_request(IA_JUEZ_URL, JUDGE_MODEL, JUEZ_SYSTEM_PROMPT, text)
        start, end = raw.find("{"), raw.rfind("}") + 1
        if start == -1 or end == 0:
            raise ValueError("No JSON found in judge response")
        return JudgeResult(json.loads(raw[start:end]))
    except Exception as e:
        logger.error(f"Error ask_judge: {e}")
        return JudgeResult({"calificacion": 0, "validez_tecnica": False, "categoria": "error"})

async def ask_thinker(prompt: str, context: Optional[str] = None) -> str:
    full_prompt = f"Contexto:\n{context}\n\nPregunta:\n{prompt}" if context else prompt
    try:
        return await _ollama_request(IA_PENSADOR_URL, THINKER_MODEL, PENSADOR_SYSTEM_PROMPT, full_prompt)
    except Exception as e:
        logger.error(f"Error ask_thinker: {e}")
        return "⚠️ Error en el motor de pensamiento."
