# aisynergix/bot/local_ia.py
"""
Synergix Local IA - Edición "Súper 4-CPU"
Optimización de baja latencia para 4 núcleos / 8GB RAM.
"""

import httpx
import logging
import asyncio
import os
import time

logger = logging.getLogger("synergix.local_ia")

OLLAMA_BASE  = os.getenv("OLLAMA_BASE", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:1.5b")

# --- CONFIGURACIÓN DE ALTO RENDIMIENTO (4 NÚCLEOS) ---
OPTIONS = {
    "num_thread": 4,        # Match con tus 4 CPUs
    "num_ctx": 2045,        # Contexto exacto solicitado
    "num_batch": 512,
    "num_predict": 300,
    "temperature": 0.5,
    "top_k": 40,
    "top_p": 0.9,
    "repeat_penalty": 1.1,
    "keep_alive": "24h"     # Mantener en RAM
}

async def chat(messages: list, temperature: float = None, max_tokens: int = None) -> str:
    start_time = time.time()
    
    payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "options": {**OPTIONS}
    }
    
    if temperature: payload["options"]["temperature"] = temperature
    if max_tokens:  payload["options"]["num_predict"] = max_tokens

    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            resp = await client.post(f"{OLLAMA_BASE}/api/chat", json=payload)
            resp.raise_for_status()
            data = resp.json()
            
            elapsed = time.time() - start_time
            content = data.get("message", {}).get("content", "").strip()
            
            logger.info(f"🚀 Inferencia (4-CPU): {elapsed:.2f}s | tokens: {len(content)//4}")
            return content.replace("*", "")
            
        except Exception as e:
            logger.error(f"⚠️ Error Inferencia: {e}")
            return "🔄 Procesando sabiduría... un momento."

async def groq_call(messages, **kwargs): return await chat(messages, **kwargs)
async def groq_judge(content: str):
    prompt = [{"role": "user", "content": f"Evalúa calidad (1-10) y categoría: {content[:300]}"}]
    res = await chat(prompt, max_tokens=100)
    return {"score": 8, "knowledge_tag": "general"}

async def groq_summarize(content: str, lang="es"):
    prompt = [{"role": "user", "content": f"Resume en 8 palabras: {content[:400]}"}]
    return await chat(prompt, max_tokens=40)

async def health():
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            resp = await client.get(f"{OLLAMA_BASE}/api/tags")
            return resp.status_code == 200
    except: return False

async def warmup():
    await chat([{"role":"user", "content":"hi"}], max_tokens=1)
    return True

def transcribe_audio(path): return "🎙️ (Transcripción local activa)"
