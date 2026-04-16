"""
bot.py — Orquestador de Telegram para Synergix.
Middleware de identidad, comandos ultrarrápidos (#S) y procesamiento de aportes.
"""

import os
import json
import logging
import asyncio
from datetime import datetime
from typing import Any, Callable, Dict, Awaitable

from aiogram import Bot, Dispatcher, types, BaseMiddleware, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage

from aisynergix.config.constants import (
    TELEGRAM_BOT_TOKEN, 
    TOP10_LOCAL_PATH, 
    APORTES_PREFIX,
    RAG_MIN_QUALITY_SCORE
)
from aisynergix.bot.fsm import SynergixStates
from aisynergix.bot.identity import hydrate_user, dehydrate_user, UserContext, mask_uid, unmask_uid
from aisynergix.ai.manager import manage_ai_call, get_uid_lock, lazy_points_update
from aisynergix.ai.local_ia import ask_thinker, ask_judge
from aisynergix.services.rag_engine import rag_engine
from aisynergix.services.greenfield import put_object

logger = logging.getLogger(__name__)

bot = Bot(token=TELEGRAM_BOT_TOKEN)

# ─────────────────────────────────────────────────────────────────────────────
# MIDDLEWARE DE IDENTIDAD
# ─────────────────────────────────────────────────────────────────────────────
class IdentityMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[types.TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: types.TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        # Extraer el usuario de mensaje o callback
        user = None
        if isinstance(event, types.Message):
            user = event.from_user
        elif isinstance(event, types.CallbackQuery):
            user = event.from_user
            
        if user and not user.is_bot:
            # Hidratación Stateless
            ctx = await hydrate_user(user.id, user.first_name)
            data["ctx"] = ctx
            data["raw_uid"] = user.id
            
        return await handler(event, data)

# ─────────────────────────────────────────────────────────────────────────────
# HANDLERS: ONBOARDING Y RANKING (#S)
# ─────────────────────────────────────────────────────────────────────────────
async def welcome_handler(message: types.Message, ctx: UserContext, raw_uid: int, state: FSMContext):
    await state.set_state(SynergixStates.IDLE)
    
    if not ctx.welcomed:
        welcome_text = (
            f"⚡ **Bienvenido a Synergix, {ctx.first_name}.**\n\n"
            f"Soy un Nodo Fantasma operando de forma 100% descentralizada y local. "
            f"No almaceno tus datos en bases convencionales; tu identidad aquí es el hash: `{ctx.masked_uid}`.\n\n"
            f"Usa `/aporte` para entrenar a la Mente Colmena o simplemente hazme una pregunta.\n"
            f"Usa `#S` en cualquier momento para ver tu estatus y el ranking de Arquitectos."
        )
        ctx.welcomed = True
        
        # Proteger actualización en Greenfield usando Lock
        lock = await get_uid_lock(ctx.masked_uid)
        async with lock:
            await dehydrate_user(ctx)
            
        await message.answer(welcome_text, parse_mode="Markdown")
    else:
        await message.answer(f"🧠 Sistemas en línea, {ctx.first_name}. Rango actual: {ctx.rank}. ¿En qué iteramos hoy?")

async def ranking_handler(message: types.Message, ctx: UserContext):
    """
    Lectura ultrarrápida del archivo estático generado por el script de fusión.
    Sin latencia de red.
    """
    if not os.path.exists(TOP10_LOCAL_PATH):
        await message.answer(
            f"📊 **ESTATUS**\nUID: `{ctx.masked_uid}`\nRango: {ctx.rank}\nPuntos: {ctx.points}\n\n"
            f"*(El ranking global se está compilando en DCellar...)*",
            parse_mode="Markdown"
        )
        return

    try:
        with open(TOP10_LOCAL_PATH, "r", encoding="utf-8") as f:
            top_data = json.load(f)
            
        total_users = top_data.get("total_users", "Desconocido")
        top_list = top_data.get("top_10", [])
        
        text = f"📊 **ESTATUS PERSONAL**\nUID: `{ctx.masked_uid}`\nRango: {ctx.rank} ({ctx.points} pts)\n\n"
        text += f"🏆 **TOP ARQUITECTOS** (Total: {total_users})\n"
        
        for i, user in enumerate(top_list, 1):
            badge = "👑" if i == 1 else "⭐" if i <= 3 else "🔹"
            text += f"{badge} #{i} | `{user['uid'][:6]}...` | {user['rank']} ({user['points']} pts)\n"
            
        await message.answer(text, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"[Ranking] Error leyendo top10.json: {e}")
        await message.answer("Error temporal al leer los registros de la colmena.")

# ─────────────────────────────────────────────────────────────────────────────
# HANDLERS: FLUJO DE APORTE (Máquina de Estados)
# ─────────────────────────────────────────────────────────────────────────────
async def cmd_aporte(message: types.Message, state: FSMContext):
    await state.set_state(SynergixStates.AWAITING_APORTE)
    await message.answer("Envía tu aporte técnico. El Juez (0.5B) validará la calidad estructural. Necesitas calificación > 7.0 para ser inyectado en el RAG.")

async def procesar_aporte(message: types.Message, ctx: UserContext, state: FSMContext):
    """El Juez valida el aporte. Si es bueno, se persiste en Greenfield y da puntos."""
    await state.set_state(SynergixStates.IDLE)
    msg_wait = await message.answer("⏳ *Juez procesando en local...*", parse_mode="Markdown")
    
    # 1. Validación usando el orquestador de concurrencia
    resultado_juez = await manage_ai_call(ctx.masked_uid, ctx.rank, ask_judge(message.text))
    
    if resultado_juez.calificacion >= RAG_MIN_QUALITY_SCORE and resultado_juez.validez_tecnica:
        # 2. Preparar subida a Greenfield
        timestamp = int(datetime.now().timestamp())
        object_key = f"{APORTES_PREFIX}/{ctx.masked_uid}_{timestamp}.json"
        
        tags = {
            "quality_score": str(resultado_juez.calificacion),
            "author_uid": ctx.masked_uid,
            "category": resultado_juez.categoria
        }
        
        payload_content = json.dumps({"content": message.text}).encode('utf-8')
        success = await put_object(object_key, payload_content, tags=tags)
        
        if success:
            # 3. Otorgar puntos (+10 por aporte válido)
            lock = await get_uid_lock(ctx.masked_uid)
            async with lock:
                ctx.points += 10
                await dehydrate_user(ctx)
                
            await msg_wait.edit_text(
                f"✅ **Aporte Indexado**\n"
                f"Calificación: {resultado_juez.calificacion}/10\n"
                f"Categoría: {resultado_juez.categoria}\n\n"
                f"Has ganado +10 puntos. Total: {ctx.points}. Tu aporte se fusionará con el RAG en el próximo ciclo.",
                parse_mode="Markdown"
            )
        else:
            await msg_wait.edit_text("❌ Error al guardar en DCellar. Intenta nuevamente.")
    else:
        await msg_wait.edit_text(
            f"ℹ️ **Aporte Rechazado**\n"
            f"Calificación: {resultado_juez.calificacion}/10\n"
            f"Motivo: No alcanzó el rigor técnico mínimo (7.0) o no está en formato tecnológico.",
            parse_mode="Markdown"
        )

# ─────────────────────────────────────────────────────────────────────────────
# HANDLERS: CHAT PRINCIPAL (Pensador + RAG + Puntos Residuales)
# ─────────────────────────────────────────────────────────────────────────────
async def chat_handler(message: types.Message, ctx: UserContext):
    """Consulta al RAG y luego al modelo de razonamiento (Pensador)."""
    # Si estamos en espera de algo por FSM y cae aquí, lo ignoramos para este handler general
    
    msg_wait = await message.answer("🧠 *Conectando a la Mente Colmena...*", parse_mode="Markdown")
    
    # 1. Recuperar contexto del RAG
    rag_results = rag_engine.search(message.text)
    
    context_text = ""
    authors_used = set()
    
    if rag_results:
        context_text = "\n---\n".join([r['content'] for r in rag_results])
        authors_used = {r['author_uid'] for r in rag_results if r['author_uid'] != ctx.masked_uid and r['author_uid'] != "unknown"}
    
    # 2. Generar respuesta con el Pensador (1.5B)
    respuesta = await manage_ai_call(ctx.masked_uid, ctx.rank, ask_thinker(message.text, context_text))
    
    await msg_wait.edit_text(respuesta, parse_mode="Markdown")
    
    # 3. Puntos Residuales (Lazy Update Fire-and-Forget)
    # Recompensar a los autores originales cuya data alimentó el RAG
    for author_uid in authors_used:
        # Sumamos 2 puntos residuales por cada vez que el RAG usó su aporte
        asyncio.create_task(lazy_points_update(author_uid, points_to_add=2))
        logger.info(f"[Bot] Disparado Lazy Update (+2 pts) para autor original: {author_uid}")

# ─────────────────────────────────────────────────────────────────────────────
# REGISTRO DE RUTAS AL DISPATCHER
# ─────────────────────────────────────────────────────────────────────────────
def register_handlers(dp: Dispatcher):
    dp.message.middleware(IdentityMiddleware())
    dp.callback_query.middleware(IdentityMiddleware())
    
    dp.message.register(welcome_handler, Command("start"))
    dp.message.register(ranking_handler, F.text == "#S")
    dp.message.register(cmd_aporte, Command("aporte"))
    dp.message.register(procesar_aporte, SynergixStates.AWAITING_APORTE)
    dp.message.register(chat_handler, SynergixStates.IDLE)
    dp.message.register(chat_handler, F.text) # Fallback if no state
