import httpx
import json
import re
from aisynergix.config.system_prompts import JUDGE_PROMPT, THINKER_PROMPT

# Endpoints de IA conectados a la red interna de Docker
JUDGE_URL = "http://synergix-ia-juez:8080/completion"
THINKER_URL = "http://synergix-ia-pensador:8081/completion"

def escape_markdown_v2(text: str) -> str:
    """Escapa caracteres reservados de Telegram MarkdownV2 sin romper negritas ni código."""
    # Lista de caracteres que Telegram exige escapar en MarkdownV2
    # No escapamos * _ ` [ ] para permitir formato básico de la IA
    special_chars = r"-.!>#+-=|{}()"
    return re.sub(f"([{re.escape(special_chars)}])", r"\\\1", text)

async def ask_judge(text: str) -> dict:
    """El Juez (1.5B) evalúa la calidad técnica del aporte. JSON puro."""
    prompt = f"{JUDGE_PROMPT}\n\nAporte a Evaluar:\n{text}\n\nJSON:"
    payload = {
        "prompt": prompt,
        "n_predict": 128,
        "temperature": 0.05,  # Estricto y matemático
        "stop": ["}"]
    }
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(JUDGE_URL, json=payload, timeout=30.0)
            resp.raise_for_status()
            raw_text = resp.json().get("content", "") + "}"
            json_str = raw_text[raw_text.find("{"):raw_text.rfind("}")+1]
            return json.loads(json_str)
    except Exception as e:
        print(f"[IA Judge] Error: {e}")
        return {"score": 0.0, "valido": False, "razon": "Error interno del nodo."}

async def ask_thinker(query: str, context: str, lang: str) -> str:
    """El Pensador (0.5B) responde basándose en el RAG. Cero alucinaciones."""
    sys_prompt = THINKER_PROMPT.format(lang=lang)
    prompt = f"{sys_prompt}\n\n{context}\n\nUsuario: {query}\nSynergix:"
    payload = {
        "prompt": prompt,
        "n_predict": 768,
        "temperature": 0.1,  # Determinista
        "top_p": 0.9,
        "stop": ["Usuario:", "Synergix:"]
    }
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(THINKER_URL, json=payload, timeout=60.0)
            resp.raise_for_status()
            answer = resp.json()["content"].strip()
            return escape_markdown_v2(answer)
    except Exception as e:
        print(f"[IA Thinker] Error: {e}")
        return escape_markdown_v2("⚠️ Error temporal en los motores locales. Reintente.")
