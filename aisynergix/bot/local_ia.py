"""
aisynergix/bot/local_ia.py
═══════════════════════════════════════════════════════════════════════════════
Motor de Inferencia Local — Qwen 2.5-1.5B via Ollama.

Arquitectura soberana: CERO dependencias de APIs externas.
El modelo corre en Hetzner CX22 (4 GB RAM, 2 vCPU).

Optimizaciones para 1.5B en CPU:
  - num_ctx=512   → velocidad máxima en CPU (~2-4s vs 10s con 2048)
  - num_thread=2  → usa ambos vCPU del CX22
  - temperature baja para judge/summarize (más determinista)
  - Timeout 45s chat, 20s judge/summarize (1.5B es más rápido)
  - Cache de conexión httpx reutilizable

Instalación en Hetzner:
  ollama pull qwen2.5:1.5b
═══════════════════════════════════════════════════════════════════════════════
"""

import asyncio
import json
import logging
import os
import re
import time

import httpx

logger = logging.getLogger("synergix.ia")

# ── Config desde .env ─────────────────────────────────────────────────────────
OLLAMA_BASE  = os.environ.get("OLLAMA_BASE",  "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:0.5b")

MAX_TOKENS_CHAT  = int(os.environ.get("MAX_TOKENS_CHAT",  "400"))
MAX_TOKENS_JUDGE = int(os.environ.get("MAX_TOKENS_JUDGE", "150"))
MAX_TOKENS_SUM   = int(os.environ.get("MAX_TOKENS_SUM",   "60"))

# Opciones Ollama optimizadas para 4 GB RAM / 2 vCPU
# 0.5b es pequeño pero rápido — optimizado para 4 núcleos
_OLLAMA_OPTIONS = {
    "num_ctx":        768,    # Un poco más de contexto para 0.5b (sigue instrucciones mejor)
    "num_thread":     4,      # 4 núcleos disponibles
    "num_predict":    300,    # 0.5b necesita más tokens para respuestas completas
    "repeat_penalty": 1.15,   # Evitar repeticiones (0.5b tiende a repetir)
    "temperature":    0.8,    # Más creativo → más emojis y expresividad
    "top_p":          0.9,
    "stop":          ["<|im_end|>", "<|endoftext|>"],
}

# Opciones para judge/summarize
_OLLAMA_OPTIONS_FAST = {
    "num_ctx":        512,
    "num_thread":     4,
    "num_predict":    120,
    "repeat_penalty": 1.1,
    "temperature":    0.1,    # Determinista para JSON
    "stop":          ["<|im_end|>", "<|endoftext|>", "\n\n"],
}


# ── Cliente httpx reutilizable ────────────────────────────────────────────────
_client: httpx.AsyncClient | None = None

async def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            base_url=OLLAMA_BASE,
            timeout=httpx.Timeout(connect=5.0, read=60.0, write=10.0, pool=5.0),
        )
    return _client


# ══════════════════════════════════════════════════════════════════════════════
# FUNCIONES PRINCIPALES
# ══════════════════════════════════════════════════════════════════════════════

async def chat(messages: list,
               temperature: float = 0.7,
               max_tokens: int = None) -> str:
    """
    Llamada principal al modelo Qwen 1.5B via Ollama.
    Compatible con la firma de groq_call para migración sin cambios.

    Args:
        messages:    Lista de {role, content} — formato OpenAI
        temperature: 0.1 para tareas deterministas, 0.7 para chat
        max_tokens:  Límite de tokens. Default: MAX_TOKENS_CHAT

    Returns:
        Texto de respuesta limpio (sin asteriscos)
    """
    if max_tokens is None:
        max_tokens = MAX_TOKENS_CHAT

    # Elegir opciones según el tipo de tarea
    opts = _OLLAMA_OPTIONS_FAST if max_tokens <= 150 else _OLLAMA_OPTIONS
    payload = {
        "model":       OLLAMA_MODEL,
        "messages":    messages,
        "temperature": temperature,
        "max_tokens":  max_tokens,
        "stream":      False,
        "options":     opts,
    }

    client = await _get_client()
    t0 = time.perf_counter()
    try:
        resp = await client.post("/v1/chat/completions", json=payload)
        resp.raise_for_status()
        data = resp.json()
        text = data["choices"][0]["message"]["content"]
        elapsed = time.perf_counter() - t0
        logger.debug("🤖 Qwen %.1fs | tokens=%s",
                     elapsed, data.get("usage", {}).get("completion_tokens", "?"))
        # Limpiar formato markdown residual
        return text.replace("*", "").replace("**", "").strip()
    except httpx.TimeoutException:
        logger.error("⏱️ Ollama timeout después de %.1fs", time.perf_counter() - t0)
        raise
    except Exception as e:
        logger.error("❌ Ollama error: %s", e)
        raise


async def judge(content: str) -> dict:
    """
    Evalúa un aporte de la comunidad con Qwen 1.5B.
    Extrae score, reason y knowledge_tag en JSON.

    Optimizado para 1.5B: prompt ultra-corto + extracción robusta.
    """
    from aisynergix.config.system_prompts import JUDGE

    # Truncar el contenido para evitar contexto largo
    content_short = content[:400]

    try:
        raw = await chat(
            messages=[
                {"role": "system", "content": JUDGE},
                {"role": "user",   "content": content_short},
            ],
            temperature=0.1,
            max_tokens=MAX_TOKENS_JUDGE,
        )

        # Extracción robusta de JSON (1.5B a veces añade texto extra)
        raw = raw.strip()
        json_match = re.search(r'\{[^{}]+\}', raw, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group())
            # Validar campos mínimos
            return {
                "score":         int(data.get("score", 6)),
                "reason":        str(data.get("reason", "Evaluado"))[:100],
                "knowledge_tag": str(data.get("knowledge_tag", "general"))[:30],
                "category":      str(data.get("category", "General"))[:30],
            }
    except Exception as e:
        logger.warning("⚠️ judge error: %s", e)

    return {"score": 6, "reason": "Auto-aprobado", "category": "General", "knowledge_tag": "general"}


async def summarize(content: str, lang: str = "es") -> str:
    """
    Resume un aporte en máximo 12 palabras con Qwen 1.5B.
    Resultado: summary para el tag ai-summary en Greenfield.
    """
    from aisynergix.config.system_prompts import SUMMARIZE

    system = SUMMARIZE.get(lang, SUMMARIZE["es"])
    content_short = content[:500]

    try:
        result = await chat(
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": content_short},
            ],
            temperature=0.1,
            max_tokens=MAX_TOKENS_SUM,
        )
        return result.strip()[:200]
    except Exception as e:
        logger.warning("⚠️ summarize error: %s", e)
        return content[:60] + "..."


async def fuse_brain(summaries: list, lang: str = "es") -> str:
    """
    Fusiona summaries de aportes en wisdom colectivo.
    Se llama desde fusion_brain.py cada 20 minutos.
    """
    from aisynergix.config.system_prompts import BRAIN_FUSION

    if not summaries:
        return ""

    # Truncar lista para no exceder contexto del 1.5B
    sample = summaries[:20]
    contrib_text = "\n".join(f"- {s}" for s in sample)

    try:
        result = await chat(
            messages=[
                {"role": "system", "content": BRAIN_FUSION},
                {"role": "user",   "content": contrib_text},
            ],
            temperature=0.3,
            max_tokens=300,
        )
        return result.strip()
    except Exception as e:
        logger.warning("⚠️ fuse_brain error: %s", e)
        return ""


async def generate_challenge(lang: str = "es") -> str:
    """Genera el challenge semanal de conocimiento."""
    from aisynergix.config.system_prompts import CHALLENGE_GEN

    prompt = CHALLENGE_GEN.get(lang, CHALLENGE_GEN["en"])
    try:
        return await chat(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.8,
            max_tokens=120,
        )
    except Exception as e:
        logger.warning("⚠️ generate_challenge error: %s", e)
        return "Topic: BNB Greenfield decentralized storage — ¿Qué ventajas tiene sobre AWS S3?"


async def transcribe_audio(audio_path: str, lang: str = "es") -> str:
    """
    Transcribe audio con faster-whisper local.
    Reemplaza Groq Whisper completamente.
    """
    loop = asyncio.get_running_loop()
    try:
        def _run():
            from faster_whisper import WhisperModel
            lang_code = lang[:2] if lang != "zht" else "zh"
            model = WhisperModel("base", device="cpu", compute_type="int8")
            segments, _ = model.transcribe(audio_path, language=lang_code)
            return " ".join(s.text.strip() for s in segments).strip()

        return await loop.run_in_executor(None, _run)
    except ImportError:
        logger.error("❌ faster-whisper no instalado: pip install faster-whisper")
        return ""
    except Exception as e:
        logger.error("❌ transcribe error: %s", e)
        return ""


# ══════════════════════════════════════════════════════════════════════════════
# HEALTH CHECK
# ══════════════════════════════════════════════════════════════════════════════

async def health() -> dict:
    """
    Verifica que Ollama esté corriendo y Qwen 1.5B esté cargado.
    Returns dict con status y detalles.
    """
    try:
        client = await _get_client()
        resp = await client.get("/api/tags", timeout=5.0)
        resp.raise_for_status()
        models = [m["name"] for m in resp.json().get("models", [])]
        model_base = OLLAMA_MODEL.split(":")[0]
        model_found = any(model_base in m for m in models)
        return {
            "ollama":      True,
            "model":       OLLAMA_MODEL,
            "model_ready": model_found,
            "models":      models,
        }
    except Exception as e:
        return {
            "ollama":      False,
            "model":       OLLAMA_MODEL,
            "model_ready": False,
            "error":       str(e),
        }


async def warmup() -> bool:
    """
    Calienta el modelo con una inferencia corta.
    Llama esto al arrancar el bot para que la primera respuesta sea rápida.
    """
    try:
        logger.info("🔥 Calentando Qwen 1.5B...")
        t0 = time.perf_counter()
        await chat(
            messages=[{"role": "user", "content": "Hi"}],
            temperature=0.1,
            max_tokens=5,
        )
        elapsed = time.perf_counter() - t0
        logger.info("✅ Qwen 1.5B listo — warmup %.1fs", elapsed)
        return True
    except Exception as e:
        logger.warning("⚠️ Warmup falló: %s", e)
        return False


# ── Alias para compatibilidad con código legacy ───────────────────────────────
# El bot.py anterior usaba groq_call — estos aliases evitan cambiar todo el código
async def groq_call(messages: list, model: str = None,
                    temperature: float = 0.7,
                    max_tokens: int = None) -> str:
    """Alias de chat() para compatibilidad con código legacy."""
    return await chat(messages, temperature=temperature, max_tokens=max_tokens)

async def groq_judge(content: str) -> dict:
    """Alias de judge() para compatibilidad con código legacy."""
    return await judge(content)

async def groq_summarize(content: str, lang: str = "es") -> str:
    """Alias de summarize() para compatibilidad con código legacy."""
    return await summarize(content, lang)
