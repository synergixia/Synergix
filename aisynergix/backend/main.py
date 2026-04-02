# aisynergix/backend/main.py
"""
Synergix Backend - FastAPI Producción Completa.
Endpoints: /chat, /upload_memory, /upload_voice_memory, /status, /reputation, /memory.
Evolución federada cada 8 minutos. RAG real con Greenfield.
"""

import asyncio
import json
import logging
import os
import subprocess
import tempfile
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

# Agregar raíz al path para imports
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from aisynergix.bot.local_ia import chat as _qwen_chat, judge as _qwen_judge, summarize as _qwen_summarize
from aisynergix.backend.services.greenfield import greenfield_client
from aisynergix.backend.services.rag_manager import rag_manager
from aisynergix.config.system_prompts import IDENTITY, JUDGE, SUMMARIZE, BRAIN_FUSION, build_system_prompt
from aisynergix.config.paths import GF

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("synergix.backend")

# ── Config ────────────────────────────────────────────────────────────────────
DB_FILE = os.path.join(os.path.dirname(__file__), "..", "aisynergix", "data", "synergix_db.json")
FEDERATION_INTERVAL = 480  # 8 minutos
OLLAMA_MODEL = "qwen2.5:1.5b"
UPLOAD_JS_PATH = os.path.join(os.path.dirname(__file__), "..", "aisynergix", "backend", "upload.js")


# ── DB Local ──────────────────────────────────────────────────────────────────

def load_db() -> dict:
    os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as exc:
            logger.error("❌ Error cargando DB: %s", exc)
    return {
        "reputation": {},
        "memory": {},
        "chat": {},
        "global_stats": {
            "total_contributions": 0,
            "challenge": "Mejor estrategia DeFi 2026",
            "collective_wisdom": "Sincronizando con la red descentralizada...",
        },
    }


def save_db() -> None:
    try:
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(db, f, indent=2, ensure_ascii=False)
    except Exception as exc:
        logger.error("❌ Error guardando DB: %s", exc)


db = load_db()


# ── Greenfield Bridge (Node.js) ───────────────────────────────────────────────

def upload_to_greenfield_sync(content: str, uid: int, metadata: dict = None) -> str:
    """
    Sube un aporte a Greenfield via Node.js bridge.
    Retorna el txHash (CID).
    """
    try:
        result = greenfield_client.upload_object_sync(content, uid, metadata)
        return result.get("cid", "CID_PENDING")
    except Exception as exc:
        logger.error("❌ upload_to_greenfield_sync error: %s", exc)
        return "CID_LOCAL_PENDING"


# ── Evolución Federada ────────────────────────────────────────────────────────

async def federated_evolution_loop() -> None:
    """
    Loop en segundo plano que procesa aportes cada 8 minutos.
    Actualiza collective_wisdom en DB y refresca cache RAG.
    """
    while True:
        await asyncio.sleep(FEDERATION_INTERVAL)
        logger.info("📈 [Federation] Iniciando ciclo de evolución...")

        try:
            # Refrescar cache RAG desde Greenfield
            await rag_manager.refresh_cache(force=True)

            # Construir contexto de todos los resúmenes recientes
            all_summaries = [
                e.get("summary", "")
                for uid_key in db["memory"]
                for e in db["memory"][uid_key]
                if e.get("summary")
            ]

            if len(all_summaries) < 2:
                logger.info("📭 [Federation] Insuficientes aportes para evolución")
                continue

            context = " | ".join(all_summaries[-50:])
            wisdom = await _qwen_chat([{"role":"system","content":BRAIN_FUSION},{"role":"user","content":context[:800]}], temperature=0.3, max_tokens=300)
            db["global_stats"]["collective_wisdom"] = wisdom
            save_db()
            logger.info("🌟 [Federation] Memoria Inmortal actualizada (%d chars)", len(wisdom))

        except Exception as exc:
            logger.error("❌ [Federation] Error en evolución: %s", exc)


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(federated_evolution_loop())
    logger.info("🔥 Synergix Backend: Motor de Memoria Inmortal activado")
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await greenfield_client.close()
    logger.info("🛑 Synergix Backend detenido limpiamente")


# ── FastAPI App ───────────────────────────────────────────────────────────────

app = FastAPI(title="Synergix Core Backend", version="2.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Pydantic Models ───────────────────────────────────────────────────────────

class ContributeMsg(BaseModel):
    user_id: int
    user_name: str
    content: str
    lang: str = "es"


class VoiceMsg(BaseModel):
    user_id: int
    user_name: str
    file_url: str
    duration: int
    lang: str = "es"


class ChatMsg(BaseModel):
    user_id: int
    text: str
    lang: str = "es"


# ── Endpoints de Consulta ─────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "online", "version": "2.0.0", "rag_cache": rag_manager.get_cache_stats()}


@app.get("/status")
async def get_status():
    return db["global_stats"]


@app.get("/reputation/{uid}")
async def get_reputation(uid: int):
    return db["reputation"].get(str(uid), {"points": 0, "contributions": 0, "impact": 0})


@app.get("/memory/{uid}")
async def get_memory(uid: int):
    return {"entries": db["memory"].get(str(uid), [])}


# ── Upload Memory ─────────────────────────────────────────────────────────────

@app.post("/upload_memory")
async def upload_memory(data: ContributeMsg):
    uid_str = str(data.user_id)
    try:
        # Generar resumen rápido
        try:
            summary = await _qwen_summarize(data.content, data.lang)
        except Exception:
            summary = data.content[:60] + "..."

        # Evaluar calidad con Juez Groq
        try:
            evaluation = await _qwen_judge(data.content)
            score = int(evaluation.get("score", 5))
            knowledge_tag = evaluation.get("knowledge_tag", "general")
            quality_tier = "high" if score >= 8 else "standard"
        except Exception:
            score = 5
            knowledge_tag = "general"
            quality_tier = "standard"

        if score <= 4:
            return {"success": False, "rejected": True, "score": score}

        # Construir metadata
        metadata = {
            "x-amz-meta-ai-summary": summary[:200],
            "x-amz-meta-quality-score": str(score),
            "x-amz-meta-quality": quality_tier,
            "x-amz-meta-knowledge-tag": knowledge_tag,
            "x-amz-meta-evaluator": "qwen2.5-1.5b-local",
            "x-amz-meta-impact": "0",
            "x-amz-meta-user-id": uid_str,
            "x-amz-meta-lang": data.lang,
        }

        # Subir a Greenfield
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: greenfield_client.upload_object_sync(data.content, data.user_id, metadata),
        )
        cid = result.get("cid", "PENDING")
        object_name = result.get("objectName", "")

        # Challenge bonus
        challenge_keywords = ["blockchain","greenfield","bnb","defi","crypto","web3","ia","ai"]
        challenge_bonus = any(kw.lower() in data.content.lower() for kw in challenge_keywords)
        points = (20 if quality_tier == "high" else 10) + (5 if challenge_bonus else 0)

        # Actualizar DB
        if uid_str not in db["reputation"]:
            db["reputation"][uid_str] = {"points": 0, "contributions": 0, "impact": 0}
        if uid_str not in db["memory"]:
            db["memory"][uid_str] = []

        db["reputation"][uid_str]["points"] += points
        db["reputation"][uid_str]["contributions"] += 1
        db["memory"][uid_str].insert(0, {
            "cid": cid,
            "object_name": object_name,
            "summary": summary,
            "score": score,
            "quality": quality_tier,
        })
        db["memory"][uid_str] = db["memory"][uid_str][:10]
        db["global_stats"]["total_contributions"] += 1
        save_db()

        # Cache RAG
        if object_name:
            rag_manager.add_to_cache(object_name, {
                "summary": summary,
                "quality_score": score,
                "knowledge_tag": knowledge_tag,
                "user_id": uid_str,
                "lang": data.lang,
            })

        logger.info("✅ upload_memory uid=%d cid=%s score=%d pts=%d", data.user_id, cid, score, points)
        return {"success": True, "cid": cid, "challenge_bonus": challenge_bonus, "points": points}

    except Exception as exc:
        logger.error("❌ upload_memory error uid=%d: %s", data.user_id, exc)
        raise HTTPException(status_code=500, detail=f"Error procesando aporte: {str(exc)[:200]}")


# ── Upload Voice Memory ───────────────────────────────────────────────────────

@app.post("/upload_voice_memory")
async def upload_voice_memory(data: VoiceMsg):
    try:
        # Descargar audio de Telegram
        async with httpx.AsyncClient(timeout=30) as client:
            audio_resp = await client.get(data.file_url)

        with tempfile.NamedTemporaryFile(delete=False, suffix=".ogg") as tmp:
            tmp.write(audio_resp.content)
            tmp_path = tmp.name

        # Transcribir con Whisper via Groq
        loop = asyncio.get_event_loop()
        from aisynergix.bot.local_ia import transcribe_audio
        transcription = await transcribe_audio(tmp_path, lang=data.lang)
        os.remove(tmp_path)

        # Procesar como upload normal
        return await upload_memory(ContributeMsg(
            user_id=data.user_id,
            user_name=data.user_name,
            content=transcription,
            lang=data.lang,
        ))

    except Exception as exc:
        logger.error("❌ upload_voice_memory error uid=%d: %s", data.user_id, exc)
        if "tmp_path" in locals() and os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise HTTPException(status_code=500, detail="Error procesando voz")


# ── Chat Libre con RAG ────────────────────────────────────────────────────────

@app.post("/chat")
async def chat_libre(data: ChatMsg):
    uid_str = str(data.user_id)
    if uid_str not in db["chat"]:
        db["chat"][uid_str] = []

    history = db["chat"][uid_str]

    # Detectar tono desde emojis
    high_energy = {"🔥", "🚀", "💪", "🌟", "⚡", "🏆", "🎯"}
    thoughtful = {"🤔", "💭", "🧠", "🌙", "😌", "🙏", "💡"}
    has_high = any(e in data.text for e in high_energy)
    has_thoughtful = any(e in data.text for e in thoughtful)
    tone = "high_energy" if has_high else ("thoughtful" if has_thoughtful else "neutral")
    tone_instr = ""

    # RAG: buscar aportes relevantes
    try:
        base_system = IDENTITY.get(data.lang, IDENTITY["es"])
        rag_template = ""
        enriched_system = await rag_manager.inject_into_prompt(
            base_system=base_system,
            question=data.text,
            lang=data.lang,
            rag_template=rag_template,
        )
    except Exception as exc:
        logger.warning("⚠️ RAG injection error: %s", exc)
        enriched_system = get_base_system(data.lang)

    if tone_instr:
        enriched_system += f"\n\nTONO: {tone_instr}"

    # Collective wisdom
    wisdom = db["global_stats"].get("collective_wisdom", "")
    if wisdom and wisdom != "Sincronizando con la red descentralizada...":
        enriched_system += f"\n\nSABIDURÍA COLECTIVA ACTUAL: {wisdom[:500]}"

    messages = [{"role": "system", "content": enriched_system}]
    messages.extend(history[-10:])
    messages.append({"role": "user", "content": data.text})

    try:
        reply = await _qwen_chat(messages=messages, temperature=0.7, max_tokens=400)

        history.append({"role": "user", "content": data.text})
        history.append({"role": "assistant", "content": reply})
        db["chat"][uid_str] = history[-20:]
        save_db()

        return {"reply": reply}

    except Exception as exc:
        logger.error("❌ chat error uid=%d: %s", data.user_id, exc)
        error_msgs = {
            "es": "La memoria colectiva está sincronizando. Inténtalo en un momento. 🔄",
            "en": "The collective memory is syncing. Try again in a moment. 🔄",
            "zh_cn": "集体记忆正在同步。请稍后重试。🔄",
            "zh": "集體記憶正在同步。請稍後重試。🔄",
        }
        return {"reply": error_msgs.get(data.lang, error_msgs["es"])}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
