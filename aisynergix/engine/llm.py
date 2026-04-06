# aisynergix/engine/llm.py
import json
import logging
import httpx
import re
from typing import Optional, Dict, Any

logger = logging.getLogger("synergix.engine")

class SynergixEngine:
    """
    Motor de Inferencia Local optimizado para ARM64.
    Se conecta a llama-server (llama.cpp) en el puerto 8080.
    """
    def __init__(self, base_url: str = "http://127.0.0.1:8080"):
        self.url = f"{base_url}/v1/chat/completions"
        self.timeout = httpx.Timeout(connect=5.0, read=120.0, write=10.0)

    async def chat(self, messages: list, temperature: float = 0.7, max_tokens: int = 300) -> str:
        payload = {
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                resp = await client.post(self.url, json=payload)
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"].strip()
            except Exception as e:
                logger.error(f"❌ LLM error: {e}")
                return "La red está sincronizando... 🧠🔄"

    async def judge(self, content: str) -> Dict[str, Any]:
        """Modo Juez: Analiza aportes y devuelve JSON estructurado."""
        prompt = (
            "Eres el Curador Synergix. Analiza el aporte. "
            "Responde SOLO un JSON: {\"score\": 1-10, \"reason\": \"...\", \"tags\": [\"...\"]}"
        )
        try:
            raw = await self.chat([
                {"role": "system", "content": prompt},
                {"role": "user", "content": content[:500]}
            ], temperature=0.1, max_tokens=150)
            
            # Limpieza de JSON para Qwen2.5-1.5B
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            if match:
                return json.loads(match.group())
        except Exception as e:
            logger.warning(f"⚠️ Judge fallback: {e}")
        return {"score": 5, "reason": "Evaluación estándar", "tags": ["general"]}

    def detect_tone(self, text: str) -> str:
        """Heurística de tono basada en texto."""
        text = text.lower()
        if any(w in text for w in ["!", "genial", "increíble", "vamos", "🔥", "🚀"]):
            return "energético"
        if any(w in text for w in ["?", "por qué", "cómo", "creo", "🤔", "🧠"]):
            return "reflexivo"
        return "neutral"

engine = SynergixEngine()
