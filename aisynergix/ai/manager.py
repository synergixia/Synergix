import json
import logging
from typing import Dict, Any, List

import httpx

logger = logging.getLogger(__name__)

class LocalIAEngine:
    """
    Controlador HTTPx asíncrono para interactuar con los cerebros GGUF locales.
    - Pensador: qwen2.5-1.5b.gguf (puerto 8081)
    - Juez: qwen2.5-0.5b.gguf (puerto 8080)
    Garantizado DNS interno Docker. Llama al endpoint de tipo llama.cpp u Ollama estándar.
    """
    def __init__(self):
        self.pensador_url = "http://pensador:8081/v1/chat/completions"
        self.juez_url = "http://juez:8080/v1/chat/completions"
        
    async def ask_pensador(self, messages: List[Dict[str, str]], temperature: float = 0.3, top_k: int = 40) -> str:
        """
        Inferencia del Pensador (qwen 1.5b) para mantener conversaciones fluidas.
        Se le envían los mensajes inyectados con el RAG Context y system prompts estructurados.
        """
        payload = {
            "messages": messages,
            "temperature": temperature,
            "top_k": top_k,
            "stream": False
        }
        
        async with httpx.AsyncClient(timeout=60.0) as client:
            try:
                response = await client.post(self.pensador_url, json=payload)
                response.raise_for_status()
                data = response.json()
                return data["choices"][0]["message"]["content"].strip()
            except Exception as e:
                logger.error(f"Error crítico en el nodo Pensador IA (8081): {str(e)}")
                return "Me encuentro momentáneamente meditando en el vacío. Intenta comunicarte en unos instantes. 🌌"

    async def evaluate_with_juez(self, text: str) -> Dict[str, Any]:
        """
        Inferencia Crítica del Juez (qwen 0.5b) configurado en JSON mode.
        Valida rigurosamente los conocimientos recibidos y retorna metadata estructurada.
        """
        prompt = (
            "Eres el Juez Implacable de Synergix. Evalúa el siguiente aporte de conocimiento. "
            "Devuelve EXCLUSIVAMENTE un JSON con: {'quality_score': float (1-10), "
            "'is_duplicate': boolean, 'reason': 'breve explicación causal', "
            "'category': 'tech/defi/crypto/general/etc', 'impact_index': int (1-100), "
            "'related_to_challenge': boolean}."
            f"\\n\\nAporte: {text}"
        )
        
        payload = {
            "messages": [{"role": "system", "content": prompt}],
            "temperature": 0.1,  # Máxima de terminismo 
            "response_format": {"type": "json_object"},
            "stream": False
        }
        
        async with httpx.AsyncClient(timeout=45.0) as client:
            try:
                response = await client.post(self.juez_url, json=payload)
                response.raise_for_status()
                data = response.json()
                content = data["choices"][0]["message"]["content"]
                return json.loads(content)
            except Exception as e:
                logger.error(f"Falla crítica en el Juez IA (8080): {str(e)}")
                # Fail-safe estructurado de rechazo
                return {
                    "quality_score": 0.0,
                    "is_duplicate": False,
                    "reason": "Cortocircuito en la corte local del Juez.",
                    "category": "unknown",
                    "impact_index": 0,
                    "related_to_challenge": False
                }

local_ia = LocalIAEngine()
