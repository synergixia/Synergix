import httpx
import json
import re
from aisynergix.config.system_prompts import JUDGE_PROMPT, THINKER_PROMPT

JUDGE_URL = "http://localhost:8080/completion"
THINKER_URL = "http://localhost:8081/completion"

def escape_markdown_v2(text: str) -> str:
    """Escapa rigurosamente los caracteres de Telegram para evitar errores de parse_mode."""
    escape_chars = r"_*[]()~`>#+-=|{}.!"
    return re.sub(f"([{re.escape(escape_chars)}])", r"\\\1", text)

async def ask_judge(text: str) -> dict:
    prompt = f"{JUDGE_PROMPT}\n\nAporte a Evaluar:\n{text}\n\nJSON:"
    payload = {
        "prompt": prompt,
        "n_predict": 128,
        "temperature": 0.05, # Ultra-bajo para evaluación estricta y determinista
        "top_p": 0.85,
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
        return {"score": 0.0, "valido": False, "razon": "Error de inferencia local."}

async def ask_thinker(query: str, context: str, lang: str) -> str:
    sys_prompt = THINKER_PROMPT.format(lang=lang)
    prompt = f"{sys_prompt}\n\n{context}\n\nUsuario: {query}\nSynergix:"
    payload = {
        "prompt": prompt,
        "n_predict": 768,
        "temperature": 0.2, # Bajo para evitar alucinaciones, alta precisión
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
        return escape_markdown_v2("⚠️ Ocurrió un error en la inferencia del nodo local. Reintentando...")
