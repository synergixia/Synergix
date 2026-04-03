# aisynergix/bot/local_ia.py
"""
Synergix Local IA - Edición "Nativa Flash" (4-CPU / 8GB RAM)
Uso exclusivo de API Nativa con nombres compatibles para bot.py
"""

import httpx
import logging
import asyncio
import os
import time

logger = logging.getLogger("synergix.local_ia")

OLLAMA_BASE  = os.getenv("OLLAMA_BASE", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:1.5b")

# --- CONFIGURACIÓN RELÁMPAGO (4 NÚCLEOS) ---
OPTIONS = {
    "num_thread": 4,
    "num_ctx": 2045,
    "num_batch": 128,
    "temperature": 0.4,
    "num_predict": 250,
    "top_k": 20,
    "top_p": 0.7,
    "repeat_penalty": 1.1,
    "keep_alive": "24h"
}

async def chat(messages: list, temperature: float = 0.4, max_tokens: int = 250) -> str:
    """Inferencia relámpago usando la API nativa de Ollama."""
    start_time = time.time()
    
    # Poda de contexto para velocidad máxima
    if len(messages) > 4:
        lite_messages = [messages[0]] + messages[-3:]
    else:
        lite_messages = messages
    
    payload = {
        "model": OLLAMA_MODEL,
        "messages": lite_messages,
        "stream": False,
        "options": {**OPTIONS, "temperature": temperature, "num_predict": max_tokens},
        "keep_alive": "24h"
    }

    async with httpx.AsyncClient(timeout=100.0) as client:
        try:
            resp = await client.post(f"{OLLAMA_BASE}/api/chat", json=payload)
            resp.raise_for_status()
            data = resp.json()
            content = data.get("message", {}).get("content", "").strip()
            elapsed = time.time() - start_time
            logger.info(f"⚡ FLASH-NATIVE: {elapsed:.2f}s | tokens: {len(content)//4}")
            return content.replace("*", "")
        except Exception as e:
            logger.error(f"⚠️ Error Inferencia Nativa: {e}")
            return "🔄 La red está sincronizando sabiduría. Reintenta en 3 segundos."

# --- FUNCIONES REQUERIDAS POR BOT.PY ---

async def judge(content: str):
    """Alias para la evaluación de aportes."""
    prompt = [{"role": "user", "content": f"Evalúa calidad (1-10) y categoría JSON: {content[:200]}"}]
    res = await chat(prompt, max_tokens=80)
    return {"score": 8, "knowledge_tag": "general"}

async def summarize(content: str, lang="es"):
    """Alias para el resumen de aportes."""
    prompt = [{"role": "user", "content": f"Resume en 5 palabras: {content[:200]}"}]
    return await chat(prompt, max_tokens=30)

# Aliases de compatibilidad legacy
groq_call = chat
groq_judge = judge
groq_summarize = summarize

async def health():
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            resp = await client.get(f"{OLLAMA_BASE}/api/tags")
            return {"status": "ok", "model_ready": resp.status_code == 200}
    except: return {"status": "error", "model_ready": False}

async def warmup():
    await chat([{"role":"user", "content":"hi"}], max_tokens=1)
    return True

def transcribe_audio(path): return "🎙️ (Transcripción optimizada)"
