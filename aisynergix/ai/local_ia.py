import httpx
import json
import os

THINKER_URL = os.getenv("THINKER_URL")
JUDGE_URL = os.getenv("JUDGE_URL")

async def ask_thinker(query, context, lang):
    # Prompts en el idioma del usuario
    system_prompts = {
        "es": "Eres Synergix, una IA colectiva soberana. Responde de forma técnica y directa.",
        "en": "You are Synergix, a sovereign collective AI. Respond technically and directly.",
        "zh_cn": "你是 Synergix，一个主权集体人工智能。请进行技术性和直接的回答。",
        "zh": "你是 Synergix，一個主權集體人工智能。請進行技術性和直接的回答。"
    }
    
    prompt = f"### Sistema: {system_prompts.get(lang, system_prompts['es'])}\n### Contexto:\n{context}\n\n### Usuario: {query}\n### Synergix:"
    
    payload = {
        "prompt": prompt,
        "temperature": 0.3,
        "top_k": 40,
        "n_predict": 768,
        "stop": ["###", "Usuario:"]
    }
    
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{THINKER_URL}/completion", json=payload, timeout=60.0)
        return resp.json()["content"].strip()

async def ask_judge(text):
    prompt = f"### Sistema: Evalúa este aporte técnico. Responde SOLO un JSON con 'score' (0-10) y 'valido' (bool).\n### Aporte: {text}\n### JSON:"
    
    payload = {
        "prompt": prompt,
        "temperature": 0.1,
        "n_predict": 128
    }
    
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{JUDGE_URL}/completion", json=payload, timeout=15.0)
        try:
            content = resp.json()["content"]
            # Limpieza básica por si el modelo añade texto extra
            json_start = content.find("{")
            json_end = content.rfind("}") + 1
            return json.loads(content[json_start:json_end])
        except:
            return {"score": 0, "valido": False}
