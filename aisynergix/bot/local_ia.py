# aisynergix/bot/local_ia.py
import asyncio
import logging
import httpx
import time
import os
from aisynergix.backend.services.rag_manager import rag_manager
from aisynergix.config.constants import T

logger = logging.getLogger("synergix.ia")

# Configuración del servidor local llama-server
LLAMA_SERVER_URL = os.getenv("OLLAMA_BASE", "http://127.0.0.1:8080")

async def chat(messages: list, temperature: float = 0.7, max_tokens: int = 300) -> str:
    """Función principal de chat compatible con llama-server."""
    payload = {
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            resp = await client.post(f"{LLAMA_SERVER_URL}/v1/chat/completions", json=payload)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.error(f"❌ LLM error: {e}")
            return "La red está sincronizando sabiduría... 🧠🔄"

async def health() -> dict:
    """Verifica si llama-server está online."""
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(f"{LLAMA_SERVER_URL}/health")
            # llama-server suele responder 200 OK en /health
            return {"ollama": True, "model_ready": resp.status_code == 200}
    except Exception as e:
        return {"ollama": False, "model_ready": False, "error": str(e)}

async def warmup() -> bool:
    """Calienta el modelo al arrancar."""
    try:
        await chat([{"role": "user", "content": "hi"}], max_tokens=5)
        return True
    except:
        return False

async def transcribe_audio(audio_path: str, lang: str = "es") -> str:
    """Transcribe audio usando faster-whisper local."""
    try:
        from faster_whisper import WhisperModel
        # Optimizado para ARM64 / CPU
        model = WhisperModel("base", device="cpu", compute_type="int8")
        segments, _ = model.transcribe(audio_path, language=lang[:2])
        return " ".join(s.text for s in segments).strip()
    except Exception as e:
        logger.error(f"❌ Transcribe error: {e}")
        return ""

# Aliases para compatibilidad con el código anterior de bot.py
async def groq_call(messages: list, model: str = None, temperature: float = 0.7, max_tokens: int = None) -> str:
    return await chat(messages, temperature, max_tokens or 300)

async def groq_judge(content: str) -> dict:
    """Modo Juez para evaluar aportes."""
    prompt = "Responde SOLO JSON: {\"score\":1-10,\"reason\":\"...\",\"tag\":\"...\"}"
    raw = await chat([{"role":"system","content":prompt},{"role":"user","content":content[:500]}], temperature=0.1)
    import json, re
    try:
        match = re.search(r'\{.*\}', raw)
        return json.loads(match.group()) if match else {"score":5}
    except:
        return {"score":5}
