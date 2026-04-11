import logging
import re
import json

import httpx

from aisynergix.config.system_prompts import JUDGE_PROMPT, THINKER_PROMPT

logger = logging.getLogger("synergix.ia")

JUDGE_URL   = "http://localhost:8080/completion"
THINKER_URL = "http://localhost:8081/completion"

# También intentar /v1/chat/completions para compatibilidad con Ollama
JUDGE_CHAT_URL   = "http://localhost:8080/v1/chat/completions"
THINKER_CHAT_URL = "http://localhost:8081/v1/chat/completions"


def escape_markdown_v2(text: str) -> str:
    """Escapa caracteres especiales de Telegram MarkdownV2."""
    escape_chars = r"_*[]()~`>#+-=|{}.!"
    return re.sub(f"([{re.escape(escape_chars)}])", r"\\\1", text)


async def _post_completion(url: str, payload: dict) -> str | None:
    """POST a /completion (llama.cpp) o /v1/chat/completions (Ollama)."""
    try:
        async with httpx.AsyncClient(timeout=60.0) as cli:
            r = await cli.post(url, json=payload)
            r.raise_for_status()
            data = r.json()
            # llama.cpp → data["content"]
            if "content" in data:
                return data["content"]
            # Ollama OpenAI-compat → data["choices"][0]["message"]["content"]
            if "choices" in data:
                return data["choices"][0]["message"]["content"]
    except Exception as e:
        logger.warning("⚠️  %s: %s", url, e)
    return None


async def ask_judge(text: str) -> dict:
    """
    El Juez evalúa la calidad del aporte.
    Usa Qwen 0.5B con temperatura ultra-baja para JSON determinista.
    """
    prompt  = f"{JUDGE_PROMPT}\n\nAporte:\n{text[:500]}\n\nJSON:"
    payload_completion = {
        "prompt":      prompt,
        "n_predict":   128,
        "temperature": 0.05,
        "top_p":       0.85,
        "stop":        ["}"],
    }
    payload_chat = {
        "model":      "qwen2.5:0.5b",
        "messages":   [
            {"role": "system", "content": JUDGE_PROMPT},
            {"role": "user",   "content": f"Aporte:\n{text[:500]}"},
        ],
        "temperature": 0.05,
        "max_tokens":  128,
    }

    # Intentar /completion primero, luego /v1/chat/completions
    raw = await _post_completion(JUDGE_URL, payload_completion)
    if raw is None:
        raw = await _post_completion(JUDGE_CHAT_URL, payload_chat)

    if raw:
        try:
            raw_clean = (raw + "}") if not raw.strip().endswith("}") else raw
            j_start   = raw_clean.find("{")
            j_end     = raw_clean.rfind("}") + 1
            if j_start != -1 and j_end > j_start:
                return json.loads(raw_clean[j_start:j_end])
        except Exception as e:
            logger.warning("⚠️  Judge JSON parse: %s | raw: %s", e, raw[:80])

    return {"score": 0.0, "valido": False, "razon": "Error de inferencia local."}


async def ask_thinker(
    query:   str,
    context: str,
    lang:    str,
    history: list[dict] = None,
) -> str:
    """
    El Pensador responde con contexto RAG e historial de conversación.
    Temperatura 0.7 → respuestas más naturales e inteligentes.
    """
    history = history or []
    sys_prompt = THINKER_PROMPT.format(lang=lang)

    # Intentar OpenAI-compat (Ollama) primero — soporta historial real
    messages: list[dict] = [{"role": "system", "content": sys_prompt}]
    if context:
        messages.append({
            "role":    "system",
            "content": f"Contexto del Legado:\n{context}",
        })
    # Añadir historial reciente
    for turn in history[-6:]:   # últimos 3 turnos de conversación
        messages.append(turn)
    messages.append({"role": "user", "content": query})

    payload_chat = {
        "model":       "qwen2.5:1.5b",
        "messages":    messages,
        "temperature": 0.7,
        "max_tokens":  512,
        "stop":        ["Usuario:", "User:"],
    }

    raw = await _post_completion(THINKER_CHAT_URL, payload_chat)

    # Fallback: /completion con prompt concatenado
    if raw is None:
        history_str = ""
        for turn in history[-4:]:
            role   = "Usuario" if turn["role"] == "user" else "Synergix"
            history_str += f"\n{role}: {turn['content']}"

        prompt = (
            f"{sys_prompt}\n\n"
            + (f"Contexto:\n{context}\n\n" if context else "")
            + history_str
            + f"\nUsuario: {query}\nSynergix:"
        )
        payload_comp = {
            "prompt":      prompt,
            "n_predict":   512,
            "temperature": 0.7,
            "top_p":       0.9,
            "stop":        ["Usuario:", "User:", "\n\n\n"],
        }
        raw = await _post_completion(THINKER_URL, payload_comp)

    if not raw or not raw.strip():
        return escape_markdown_v2(
            "⚠️ El nodo local está iniciando. Inténtalo en unos segundos."
        )

    # Limpiar y escapar para MarkdownV2
    cleaned = raw.strip()
    # Quitar posibles repeticiones del prompt
    for stop in ["Usuario:", "User:", "Synergix:"]:
        if stop in cleaned:
            cleaned = cleaned[:cleaned.index(stop)].strip()

    return escape_markdown_v2(cleaned)
