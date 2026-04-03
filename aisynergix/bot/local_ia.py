# aisynergix/bot/local_ia.py
"""
Synergix Local IA - Edición "Velocidad Absoluta" (4-CPU / 8GB RAM)
Optimización de pre-procesamiento y control de contexto estricto.
"""

import httpx
import logging
import asyncio
import os
import time

logger = logging.getLogger("synergix.local_ia")

OLLAMA_BASE  = os.getenv("OLLAMA_BASE", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:1.5b")

# --- CONFIGURACIÓN DE ALTO IMPACTO ---
OPTIONS = {
    "num_thread": 4,        # Uso total de los 4 núcleos
    "num_ctx": 2045,        # Límite estricto para evitar lag de memoria
    "num_batch": 512,       # Aceleración de lectura inicial en ARM
    "num_predict": 150,     # Respuestas cortas = Respuestas rápidas
    "temperature": 0.3,     # Menos creatividad = Más velocidad
    "top_k": 10,            # Menos tokens a evaluar
    "top_p": 0.5,           # Muestreo rápido
    "repeat_penalty": 1.1,
    "keep_alive": "24h"     # Modelo siempre en RAM
}

async def chat(messages: list, temperature: float = 0.3, max_tokens: int = 150) -> str:
    """Inferencia optimizada para latencia CERO."""
    start_time = time.time()
    
    # PODA AGRESIVA DE HISTORIAL
    # Para que la CPU no tarde en 'leer', solo enviamos el mensaje del sistema y el último del usuario.
    if len(messages) > 2:
        lite_messages = [messages[0], messages[-1]]
    else:
        lite_messages = messages
    
    payload = {
        "model": OLLAMA_MODEL,
        "messages": lite_messages,
        "stream": False,
        "options": {**OPTIONS, "temperature": temperature, "num_predict": max_tokens},
        "keep_alive": "24h"
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            # USAMOS LA API NATIVA /api/chat (La más rápida de Ollama)
            resp = await client.post(f"{OLLAMA_BASE}/api/chat", json=payload)
            resp.raise_for_status()
            data = resp.json()
            
            content = data.get("message", {}).get("content", "").strip()
            elapsed = time.time() - start_time
            
            # Si tarda más de 5 segundos, logueamos alerta
            log_level = logger.info if elapsed < 5 else logger.warning
            log_level(f"🚀 INFERENCIA NATIVA: {elapsed:.2f}s | tokens: {len(content)//4}")
            
            return content.replace("*", "")
            
        except Exception as e:
            logger.error(f"⚠️ Error Inferencia: {e}")
            return "🔄 Optimizando sabiduría... reintenta en 2 segundos."

# Aliases para compatibilidad con bot.py
async def judge(content: str):
    prompt = [{"role": "user", "content": f"Score 1-10 y categoría: {content[:200]}"}]
    return await chat(prompt, max_tokens=50)

async def summarize(content: str, lang="es"):
    prompt = [{"role": "user", "content": f"Resume en 5 palabras: {content[:200]}"}]
    return await chat(prompt, max_tokens=30)

async def health():
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            resp = await client.get(f"{OLLAMA_BASE}/api/tags")
            return {"status": "ok", "model_ready": resp.status_code == 200}
    except: return {"status": "error", "model_ready": False}

async def warmup():
    await chat([{"role":"user", "content":"hi"}], max_tokens=1)
    return True

groq_call = chat
groq_judge = judge
groq_summarize = summarize
def transcribe_audio(path): return "🎙️ (Audio en espera)"
