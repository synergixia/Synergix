import httpx
import json
from aisynergix.config.system_prompts import JUDGE_PROMPT, THINKER_PROMPT

JUDGE_URL = "http://localhost:8080/completion"
THINKER_URL = "http://localhost:8081/completion"

async def ask_judge(text: str) -> dict:
    """Consulta al Qwen 0.5B para evaluar la calidad técnica del aporte."""
    prompt = f"{JUDGE_PROMPT}\n\nAporte del Usuario:\n{text}\n\nRespuesta JSON:"
    payload = {
        "prompt": prompt,
        "n_predict": 128,
        "temperature": 0.1,
        "stop": ["}"]
    }
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(JUDGE_URL, json=payload, timeout=30.0)
            resp.raise_for_status()
            result_str = resp.json()["content"] + "}" 
            return json.loads(result_str)
    except Exception as e:
        print(f"[IA] Error en el Juez: {e}")
        return {"score": 0, "valido": False, "razon": "Error interno del nodo."}

async def ask_thinker(query: str, context: str, lang: str) -> str:
    """Consulta al Qwen 1.5B para generar la respuesta RAG comunitaria."""
    sys_prompt = THINKER_PROMPT.format(lang=lang)
    prompt = f"{sys_prompt}\n\n{context}\n\nUsuario: {query}\nSynergix:"
    payload = {
        "prompt": prompt,
        "n_predict": 512,
        "temperature": 0.7,
        "stop": ["Usuario:", "Synergix:"]
    }
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(THINKER_URL, json=payload, timeout=60.0)
            resp.raise_for_status()
            return resp.json()["content"].strip()
    except Exception as e:
        print(f"[IA] Error en el Pensador: {e}")
        return "⚠️ *Ocurrió un micro\\-corte en la red soberana\\. Reintentando\\.\\.\\.*"
