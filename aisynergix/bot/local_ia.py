# aisynergix/bot/local_ia.py
"""
Synergix Local IA - Edición "Fórmula 1" para Hetzner 2-CPU
Optimización de baja latencia extrema para Qwen 2.5-1.5B.
"""

import httpx
import logging
import asyncio
import os
import time

logger = logging.getLogger("synergix.local_ia")

# Configuración desde entorno
OLLAMA_BASE  = os.getenv("OLLAMA_BASE", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:1.5b")

# --- PARÁMETROS DE VELOCIDAD SÓNICA ---
OPTIONS = {
    "num_thread": 2,        # Crucial: 1 hilo por núcleo físico Neoverse-N1
    "num_ctx": 1024,        # Reducido: Inferencia instantánea, menos pre-procesado
    "num_batch": 512,       # Optimización de carga para ARM64
    "num_predict": 200,     # Respuestas rápidas y al punto
    "temperature": 0.4,     # Menos divagación = Menos cálculo de CPU
    "top_k": 20,
    "top_p": 0.8,
    "repeat_penalty": 1.1,
    "keep_alive": "24h"     # Mantiene el modelo SIEMPRE en RAM (Zero loading time)
}

async def chat(messages: list, temperature: float = None, max_tokens: int = None) -> str:
    """Inferencia de alta velocidad para chat libre."""
    
    start_time = time.time()
    
    payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "options": {**OPTIONS}
    }
    
    # Sobreescribir si se piden valores específicos
    if temperature: payload["options"]["temperature"] = temperature
    if max_tokens:  payload["options"]["num_predict"] = max_tokens

    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            # Intentar API nativa de Ollama para mayor control de options
            resp = await client.post(f"{OLLAMA_BASE}/api/chat", json=payload)
            resp.raise_for_status()
            data = resp.json()
            
            elapsed = time.time() - start_time
            content = data.get("message", {}).get("content", "").strip()
            
            logger.info(f"⚡ Inferencia: {elapsed:.2f}s | {len(content)//4} tokens")
            return content.replace("*", "")
            
        except Exception as e:
            logger.error(f"⚠️ Error Inferencia Sónica: {e}")
            return "🔄 Calibrando sabiduría colectiva... dame 5 segundos."

# Aliases de compatibilidad para el bot.py
async def groq_call(messages, **kwargs):
    return await chat(messages, **kwargs)

async def groq_judge(content: str):
    """Evaluación ultrarrápida (poda de tokens)"""
    prompt = [{"role": "system", "content": "Evalúa calidad (1-10) y categoría. Responde SOLO JSON: {\"score\":8, \"knowledge_tag\":\"tech\"}"},
              {"role": "user", "content": content[:300]}]
    try:
        res = await chat(prompt, temperature=0.1, max_tokens=100)
        import json, re
        match = re.search(r'\{.*?\}', res, re.DOTALL)
        return json.loads(match.group()) if match else {"score": 7, "knowledge_tag": "general"}
    except: return {"score": 7, "knowledge_tag": "general"}

async def groq_summarize(content: str, lang="es"):
    """Resumen relámpago"""
    prompt = [{"role": "system", "content": "Resume en 8 palabras:"},
              {"role": "user", "content": content[:400]}]
    return await chat(prompt, temperature=0.1, max_tokens=40)

async def health():
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            resp = await client.get(f"{OLLAMA_BASE}/api/tags")
            return resp.status_code == 200
    except: return False

async def warmup():
    """Calentamiento inicial para cargar el modelo en RAM."""
    await chat([{"role":"user", "content":"hi"}], max_tokens=1)
    return True

def transcribe_audio(path): return "🎙️ (Transcripción deshabilitada para velocidad)"
