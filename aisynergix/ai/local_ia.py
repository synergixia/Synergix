import httpx
import json
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger("Synergix.LocalIA")

class LocalIA:
    """
    Conector asíncrono optimizado para llama-server (GGUF).
    """
    def __init__(self, thinker_url: str, judge_url: str):
        self.thinker_url = f"{thinker_url}/completion"
        self.judge_url = f"{judge_url}/completion"
        self.timeout = httpx.Timeout(45.0, connect=10.0)

    async def ask_thinker(self, prompt: str, system_prompt: str = "") -> str:
        """Petición al Pensador (Qwen 1.5B) con temperatura 0.3 para velocidad."""
        payload = {
            "prompt": f"<|im_start|>system\n{system_prompt}<|im_end|>\n<|im_start|>user\n{prompt}<|im_end|>\n<|im_start|>assistant\n",
            "temperature": 0.3,
            "top_k": 40,
            "n_predict": 1024,
            "stop": ["<|im_end|>"]
        }
        
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.post(self.thinker_url, json=payload)
                data = response.json()
                return data.get("content", "Error en el Pensador.")
            except Exception as e:
                logger.error(f"Error en el Pensador AI: {e}")
                return "Cerebro desconectado temporalmente."

    async def ask_judge(self, content: str) -> Dict[str, Any]:
        """Petición al Juez (Qwen 0.5B) para validación hiper-rápida."""
        system = "Eres un Juez de calidad. Responde ÚNICAMENTE en JSON con los campos: 'score' (0-10), 'category', 'status' ('approved'/'rejected')."
        prompt = f"Contenido a evaluar: {content}"
        
        payload = {
            "prompt": f"<|im_start|>system\n{system}<|im_end|>\n<|im_start|>user\n{prompt}<|im_end|>\n<|im_start|>assistant\n{{",
            "temperature": 0.1,
            "n_predict": 256,
            "stop": ["<|im_end|>"]
        }
        
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.post(self.judge_url, json=payload)
                raw_json = "{" + response.json().get("content", "").split("}")[0] + "}"
                return json.loads(raw_json)
            except Exception as e:
                logger.error(f"Error en el Juez AI: {e}")
                return {"score": 0, "status": "rejected"}
