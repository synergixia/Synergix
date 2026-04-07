"""
aisynergix/bot/local_ia.py
══════════════════════════════════════════════════════════════════════════════
SynergixEngine — Motor de IA Local con arquitectura dual:

  · El Juez   (Qwen 0.5B) — ultraligero, siempre en RAM.
                             Evalúa aportes, genera ai-summary, extrae tags.
                             Contexto: 512 tokens. Temperatura: 0.1.

  · El Pensador (Qwen 1.5B) — motor pesado vía llama-server :8080.
                               RAG, fusión de cerebro, respuestas complejas.
                               Contexto: 768 tokens. Temperatura: 0.8.

  Sin APIs externas. 100% local. ARM64 NEON optimizado.

Instalación en Hetzner (ARM64):
  # llama-server (principal — Pensador):
  ollama pull qwen2.5:1.5b

  # Juez (0.5B siempre cargado):
  ollama pull qwen2.5:0.5b

  # whisper.cpp (transcripción local):
  apt install ffmpeg
  pip install faster-whisper --break-system-packages
══════════════════════════════════════════════════════════════════════════════
"""

import asyncio
import json
import logging
import os
import re
import subprocess
import time
from typing import Optional

import httpx

logger = logging.getLogger("synergix.ia")

# ── Backends ──────────────────────────────────────────────────────────────────
LLAMA_BASE    = os.environ.get("LLAMA_BASE",    "http://localhost:8080")
OLLAMA_BASE   = os.environ.get("OLLAMA_BASE",   "http://localhost:11434")
MODEL_JUDGE   = os.environ.get("MODEL_JUDGE",   "qwen2.5:0.5b")   # El Juez
MODEL_THINKER = os.environ.get("MODEL_THINKER", "qwen2.5:1.5b")   # El Pensador
OLLAMA_MODEL  = os.environ.get("OLLAMA_MODEL",  "qwen2.5:0.5b")   # default

# Tokens por tarea
MAX_TOKENS_CHAT  = int(os.environ.get("MAX_TOKENS_CHAT",  "350"))
MAX_TOKENS_JUDGE = int(os.environ.get("MAX_TOKENS_JUDGE", "120"))
MAX_TOKENS_SUM   = int(os.environ.get("MAX_TOKENS_SUM",   "60"))

# ── Opciones de inferencia ARM64 ──────────────────────────────────────────────
# El Juez — ultraligero, siempre en RAM
_JUDGE_OPTS: dict = {
    "num_ctx":        512,
    "num_thread":     int(os.environ.get("NUM_THREADS", "4")),
    "num_predict":    MAX_TOKENS_JUDGE,
    "repeat_penalty": 1.1,
    "temperature":    0.1,   # Determinista para JSON
    "stop":           ["<|im_end|>", "<|endoftext|>", "\n\n"],
}

# El Pensador — RAG + chat complejo
_THINKER_OPTS: dict = {
    "num_ctx":        768,
    "num_thread":     int(os.environ.get("NUM_THREADS", "4")),
    "num_predict":    MAX_TOKENS_CHAT,
    "repeat_penalty": 1.15,
    "temperature":    0.8,
    "top_p":          0.9,
    "stop":           ["<|im_end|>", "<|endoftext|>"],
}

# ── Cliente httpx persistente ─────────────────────────────────────────────────
_client: Optional[httpx.AsyncClient] = None

async def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=55.0, write=10.0, pool=5.0)
        )
    return _client

# ══════════════════════════════════════════════════════════════════════════════
# FUNCIÓN CORE — llama a cualquier backend
# ══════════════════════════════════════════════════════════════════════════════
async def _call_backend(
    base_url: str,
    model: str,
    messages: list[dict],
    max_tokens: int,
    opts: dict,
) -> str:
    """
    Llama a un backend compatible OpenAI (llama-server o Ollama).
    Maneja 404 intentando sin sufijo de cuantización.
    """
    client  = await _get_client()
    payload = {
        "model":      model,
        "messages":   messages,
        "max_tokens": max_tokens,
        "stream":     False,
        "options":    {**opts, "num_predict": max_tokens},
    }
    resp = await client.post(
        f"{base_url}/v1/chat/completions",
        json=payload,
        headers={"Content-Type": "application/json"},
    )
    if resp.status_code == 404:
        # Reintentar sin sufijo de cuantización (ej: qwen2.5:0.5b → qwen2.5)
        payload["model"] = model.split(":")[0]
        resp = await client.post(
            f"{base_url}/v1/chat/completions",
            json=payload,
            headers={"Content-Type": "application/json"},
        )
    resp.raise_for_status()
    text = resp.json()["choices"][0]["message"]["content"]
    return re.sub(r"\*+", "", text).strip()

async def _llm(
    messages: list[dict],
    model: str = None,
    max_tokens: int = None,
    temperature: float = 0.8,
    fast: bool = False,
) -> str:
    """
    Llamada unificada al LLM.
    Prioridad: llama-server :8080 → Ollama :11434.

    Args:
        messages:     Conversación OpenAI-format.
        model:        Modelo a usar. None = auto (Pensador para chat, Juez para fast).
        max_tokens:   Tokens máximos de salida.
        temperature:  Temperatura (ignorada si fast=True, se usa 0.1).
        fast:         Si True, usa El Juez (0.5B) con opciones ultrarrápidas.
    """
    if max_tokens is None:
        max_tokens = MAX_TOKENS_JUDGE if fast else MAX_TOKENS_CHAT

    if fast:
        target_model = model or MODEL_JUDGE
        opts         = {**_JUDGE_OPTS, "temperature": 0.1}
    else:
        target_model = model or MODEL_THINKER
        opts         = {**_THINKER_OPTS, "temperature": temperature}

    # 1. Intentar llama-server (principal)
    try:
        return await _call_backend(LLAMA_BASE, target_model, messages, max_tokens, opts)
    except (httpx.ConnectError, httpx.ConnectTimeout):
        pass  # llama-server no disponible → Ollama fallback
    except Exception as e:
        logger.warning("⚠️ llama-server error: %s", e)

    # 2. Fallback: Ollama
    try:
        return await _call_backend(OLLAMA_BASE, target_model, messages, max_tokens, opts)
    except (httpx.ConnectError, httpx.ConnectTimeout):
        pass
    except Exception as e:
        logger.warning("⚠️ Ollama error: %s", e)

    raise RuntimeError("Sin backend LLM disponible (llama-server y Ollama fallaron)")

# ══════════════════════════════════════════════════════════════════════════════
# EL JUEZ — Qwen 0.5B (evaluación de aportes)
# ══════════════════════════════════════════════════════════════════════════════
async def judge(content: str) -> dict:
    """
    El Juez evalúa un aporte de la comunidad.
    Usa Qwen 0.5B — ultraligero, siempre en RAM.

    Returns:
        {"score": 1-10, "reason": "...", "knowledge_tag": "..."}
    """
    system = (
        "You are a knowledge quality curator for Synergix decentralized AI. "
        "Evaluate the contribution and reply ONLY with valid JSON, nothing else:\n"
        '{"score":7,"reason":"brief explanation","knowledge_tag":"blockchain"}'
    )
    try:
        raw = await _llm(
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": content[:500]},
            ],
            max_tokens=MAX_TOKENS_JUDGE,
            fast=True,
        )
        m = re.search(r'\{[^{}]+\}', raw, re.DOTALL)
        if m:
            d = json.loads(m.group())
            return {
                "score":         min(10, max(1, int(d.get("score", 6)))),
                "reason":        str(d.get("reason", ""))[:150],
                "knowledge_tag": str(d.get("knowledge_tag", "general"))[:30],
            }
    except Exception as e:
        logger.warning("⚠️ judge error: %s", e)
    return {"score": 6, "reason": "Auto-evaluated", "knowledge_tag": "general"}

# ══════════════════════════════════════════════════════════════════════════════
# EL PENSADOR — Qwen 1.5B (chat, RAG, fusión)
# ══════════════════════════════════════════════════════════════════════════════
async def chat(messages: list[dict],
               temperature: float = 0.8,
               max_tokens: int = None) -> str:
    """
    El Pensador responde preguntas complejas con contexto RAG.
    Usa Qwen 1.5B vía llama-server.
    """
    return await _llm(messages, max_tokens=max_tokens, temperature=temperature, fast=False)

async def summarize(content: str, lang: str = "es") -> str:
    """Genera ai-summary en 12 palabras para tags de Greenfield."""
    prompts = {
        "es":    "Resume en máximo 12 palabras. Solo texto plano sin puntuación extra.",
        "en":    "Summarize in max 12 words. Plain text only.",
        "zh_cn": "用最多12个字总结，纯文本。",
        "zh":    "用最多12個字總結，純文字。",
    }
    try:
        return await _llm(
            messages=[
                {"role": "system", "content": prompts.get(lang, prompts["es"])},
                {"role": "user",   "content": content[:500]},
            ],
            max_tokens=MAX_TOKENS_SUM,
            fast=True,
        )
    except Exception:
        return content[:80] + "..."

async def fuse_brain(summaries: list[str]) -> str:
    """
    Fusión colectiva: sintetiza summaries de la comunidad en sabiduría.
    Se llama desde fusion_brain_loop cada 20 min.
    """
    system = (
        "You are Synergix collective brain. "
        "Synthesize these community contributions into collective wisdom. "
        "Write 3-5 concise sentences. Plain text, no bullets, no headers."
    )
    text = "\n".join(f"- {s}" for s in summaries[:25])
    try:
        return await _llm(
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": text},
            ],
            max_tokens=300,
            temperature=0.3,
        )
    except Exception as e:
        logger.warning("⚠️ fuse_brain: %s", e)
        return ""

async def generate_challenge() -> str:
    """
    Genera el challenge semanal automáticamente.
    Se llama cada lunes 09:00 UTC desde weekly_challenge_loop.
    """
    prompt = (
        "Generate a weekly knowledge challenge for the Synergix Web3 community. "
        "Topic: blockchain, AI, BNB Chain, DeFi, or decentralization. "
        "Write ONLY: 'Topic: [short title]. [1 challenging question, max 20 words]'"
    )
    try:
        result = await _llm(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=100,
            temperature=0.9,
        )
        return result.strip()
    except Exception:
        return "Topic: BNB Greenfield. ¿Cómo la IA descentralizada supera a ChatGPT en privacidad?"

# ══════════════════════════════════════════════════════════════════════════════
# VOZ — Transcripción local (whisper.cpp → faster-whisper fallback)
# ══════════════════════════════════════════════════════════════════════════════
WHISPER_BIN   = os.environ.get("WHISPER_BIN",   "whisper-cli")
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "models/ggml-base.bin")

async def transcribe_audio(audio_path: str, lang: str = "es") -> str:
    """
    Transcribe audio localmente.
    Flujo: .ogg → ffmpeg → WAV 16kHz → whisper.cpp → texto.

    Fallback automático a faster-whisper si whisper-cli no está instalado.
    """
    lang_code = lang[:2] if lang not in ("zh_cn", "zh") else "zh"
    loop      = asyncio.get_running_loop()

    def _run() -> str:
        wav_path = audio_path.replace(".ogg", ".wav").replace(".mp4", ".wav")
        try:
            # 1. Convertir a WAV 16kHz mono con ffmpeg
            subprocess.run(
                ["ffmpeg", "-y", "-i", audio_path,
                 "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", wav_path],
                capture_output=True, timeout=30,
            )

            # 2. Transcribir con whisper.cpp
            res = subprocess.run(
                [WHISPER_BIN, "-m", WHISPER_MODEL,
                 "-f", wav_path, "-l", lang_code, "--output-txt"],
                capture_output=True, text=True, timeout=60,
            )

            # whisper-cli escribe el resultado en wav_path.txt
            txt_file = wav_path + ".txt"
            if os.path.exists(txt_file):
                with open(txt_file, "r", encoding="utf-8") as fh:
                    result = fh.read().strip()
                if result:
                    return result

            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()

        except FileNotFoundError:
            # whisper-cli no instalado → faster-whisper fallback
            logger.info("🎙️ whisper-cli no disponible → usando faster-whisper")
            try:
                from faster_whisper import WhisperModel
                model_w = WhisperModel("base", device="cpu", compute_type="int8")
                segments, _ = model_w.transcribe(audio_path, language=lang_code)
                return " ".join(s.text.strip() for s in segments).strip()
            except ImportError:
                logger.warning("⚠️ Instala: pip install faster-whisper --break-system-packages")
            except Exception as e:
                logger.warning("⚠️ faster-whisper: %s", e)

        except Exception as e:
            logger.warning("⚠️ transcribe_audio: %s", e)

        finally:
            for p in [wav_path, wav_path + ".txt"]:
                if p != audio_path and os.path.exists(p):
                    try:
                        os.remove(p)
                    except OSError:
                        pass

        return ""

    return await loop.run_in_executor(None, _run)

# ══════════════════════════════════════════════════════════════════════════════
# HEALTH & WARMUP
# ══════════════════════════════════════════════════════════════════════════════
async def health() -> dict:
    """Verifica disponibilidad de los backends LLM."""
    client  = await _get_client()
    results = {}
    for name, base in [("llama-server", LLAMA_BASE), ("ollama", OLLAMA_BASE)]:
        try:
            resp = await client.get(f"{base}/v1/models", timeout=4.0)
            results[name] = resp.status_code in (200, 404)
        except Exception:
            results[name] = False
    results["any_ok"] = any(results.values())
    return results

async def warmup() -> bool:
    """Warmup del modelo al arrancar — primera inferencia siempre es lenta."""
    try:
        logger.info("🔥 Calentando LLM...")
        t0 = time.perf_counter()
        await _llm([{"role": "user", "content": "Hi"}], max_tokens=5, fast=True)
        logger.info("✅ LLM listo en %.1fs", time.perf_counter() - t0)
        return True
    except Exception as e:
        logger.warning("⚠️ warmup falló: %s", e)
        return False

# ── Aliases para compatibilidad con código legacy ─────────────────────────────
async def groq_call(messages: list, model: str = None,
                    temperature: float = 0.8,
                    max_tokens: int = None) -> str:
    """Alias de chat() — compatibilidad con código legacy."""
    return await chat(messages, temperature=temperature, max_tokens=max_tokens)

async def groq_judge(content: str) -> dict:
    """Alias de judge() — compatibilidad con código legacy."""
    return await judge(content)

async def groq_summarize(content: str, lang: str = "es") -> str:
    """Alias de summarize() — compatibilidad con código legacy."""
    return await summarize(content, lang)
