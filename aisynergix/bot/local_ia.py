# aisynergix/bot/local_ia.py
import asyncio
import logging
import httpx
import os
import re
import json

logger = logging.getLogger("synergix.ia")

# Configuración dinámica para Docker o Local
URL_PENSADOR = os.getenv("OLLAMA_BASE", "http://127.0.0.1:8080") + "/v1"
URL_JUEZ     = os.getenv("URL_JUEZ", "http://127.0.0.1:8081") + "/v1"

async def chat(messages: list, temperature: float = 0.7, max_tokens: int = 300) -> str:
    payload = {"messages": messages, "temperature": temperature, "max_tokens": max_tokens, "stream": False}
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            resp = await client.post(f"{URL_PENSADOR}/chat/completions", json=payload)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.error(f"❌ Pensador error: {e}")
            return "La red está sincronizando sabiduría... 🧠🔄"

async def groq_judge(content: str) -> dict:
    prompt = "Responde SOLO JSON: {\"score\":1-10,\"reason\":\"...\",\"tag\":\"...\"}"
    payload = {
        "messages": [{"role":"system","content":prompt}, {"role":"user","content":content[:500]}],
        "temperature": 0.1, "max_tokens": 150
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.post(f"{URL_JUEZ}/chat/completions", json=payload)
            raw = resp.json()["choices"][0]["message"]["content"].strip()
            match = re.search(r'\{.*\}', raw)
            return json.loads(match.group()) if match else {"score":5}
        except Exception as e:
            logger.error(f"❌ Juez error: {e}")
            return {"score": 5, "reason": "Error en evaluación automática"}

async def health() -> dict:
    return {"pensador": "check_docker", "juez": "check_docker"}

async def warmup():
    pass

async def transcribe_audio(audio_path: str, lang: str = "es") -> str:
    try:
        from faster_whisper import WhisperModel
        model = WhisperModel("base", device="cpu", compute_type="int8")
        segments, _ = model.transcribe(audio_path, language=lang[:2])
        return " ".join(s.text for s in segments).strip()
    except Exception as e:
        logger.error(f"❌ Transcribe error: {e}")
        return ""

async def groq_call(messages: list, **kwargs):
    return await chat(messages, **kwargs)
