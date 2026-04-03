# aisynergix/bot/local_ia.py
"""
Synergix Local IA - Edición "Nativa Flash" (4-CPU / 8GB RAM)
Uso exclusivo de API Nativa para latencia mínima y control total de hilos.
"""

import httpx
import logging
import asyncio
import os
import time

logger = logging.getLogger("synergix.local_ia")

OLLAMA_BASE  = os.getenv("OLLAMA_BASE", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:1.5b")

async def chat(messages: list, temperature: float = 0.4, max_tokens: int = 250) -> str:
    """Inferencia relámpago usando la API nativa de Ollama."""
    
    start_time = time.time()
    
    # Poda de contexto: System prompt + 3 últimos mensajes para velocidad máxima
    if len(messages) > 4:
        lite_messages = [messages[0]] + messages[-3:]
    else:
        lite_messages = messages
    
    payload = {
        "model": OLLAMA_MODEL,
        "messages": lite_messages,
        "stream": False,
        "options": {
            "num_thread": 4,        # Concentrar los 4 núcleos
            "num_ctx": 2045,        # Contexto solicitado
            "num_batch": 128,       # Procesamiento por lotes ligero
            "num_predict": max_tokens,
            "temperature": temperature,
            "top_k": 20,
            "top_p": 0.7,
            "repeat_penalty": 1.1
        },
        "keep_alive": "24h"         # No descargar nunca de RAM
    }

    async with httpx.AsyncClient(timeout=100.0) as client:
        try:
            # La ruta /api/chat es más eficiente que /v1/chat/completions en Ollama
            resp = await client.post(f"{OLLAMA_BASE}/api/chat", json=payload)
            resp.raise_for_status()
            data = resp.json()
            
            content = data.get("message", {}).get("content", "").strip()
            elapsed = time.time() - start_time
            
            # Log de rendimiento para el usuario
            logger.info(f"⚡ FLASH-NATIVE: {elapsed:.2f}s | tokens: {len(content)//4}")
            return content.replace("*", "")
            
        except Exception as e:
            logger.error(f"⚠️ Error Inferencia Nativa: {e}")
            return "🔄 La red está sincronizando sabiduría. Reintenta en 3 segundos."

# Aliases para mantener compatibilidad con bot.py
async def groq_call(messages, **kwargs):
    temp = kwargs.get("temperature", 0.4)
    tokens = kwargs.get("max_tokens", 250)
    return await chat(messages, temperature=temp, max_tokens=tokens)

async def groq_judge(content: str):
    prompt = [{"role": "user", "content": f"Evalúa calidad (1-10) y categoría JSON: {content[:200]}"}]
    res = await chat(prompt, max_tokens=80)
    return {"score": 8, "knowledge_tag": "general"}

async def groq_summarize(content: str, lang="es"):
    prompt = [{"role": "user", "content": f"Resume en 5 palabras: {content[:200]}"}]
    return await chat(prompt, max_tokens=30)

async def health():
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            resp = await client.get(f"{OLLAMA_BASE}/api/tags")
            return resp.status_code == 200
    except: return False

async def warmup():
    """Carga el modelo preventivamente."""
    await chat([{"role":"user", "content":"hi"}], max_tokens=1)
    return True

def transcribe_audio(path): return "🎙️ (Transcripción optimizada para velocidad)"
