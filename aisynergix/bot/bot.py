"""
bot.py — Orquestador de Telegram para Synergix.
Middleware de identidad, handlers multilingües, comando #S y scheduler integrado.
"""

import asyncio
import logging
from typing import Any, Dict, List
from datetime import datetime

from aiogram import Bot, Dispatcher, types, BaseMiddleware
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from aisynergix.config.constants import (
    TELEGRAM_BOT_TOKEN,
    USERS_PREFIX,
    FUSION_INTERVAL_MINUTES,
    DAILY_NOTIFICATION_TIME,
    WEEKLY_CHALLENGE_DAY,
    WEEKLY_CHALLENGE_TIME
)
from aisynergix.bot.identity import hydrate_user, dehydrate_user, UserContext
from aisynergix.services.greenfield import list_objects, get_user_metadata
from aisynergix.ai.manager import manage_ai_call
from aisynergix.ai.local_ia import ask_thinker, ask_judge

logger = logging.getLogger(__name__)

# ── Middleware de Identidad (Stateless Hydration) ──────────────────────────────

class IdentityMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if not isinstance(event, (types.Message, types.CallbackQuery)):
            return await handler(event, data)
        
        user = event.from_user
        # Hidratación desde Greenfield (Stateless)
        ctx = await hydrate_user(str(user.id), user.first_name)
        data["ctx"] = ctx
        
        # Ejecutar Handler
        result = await handler(event, data)
        
        # Deshidratación (Sincronización lazy a Greenfield)
        await dehydrate_user(ctx)
        return result

# ── Handlers ──────────────────────────────────────────────────────────────────

async def welcome_handler(message: types.Message, ctx: UserContext):
    """Maneja el Onboarding si welcomed es false."""
    if not ctx.welcomed:
        welcome_text = (
            f"👋 ¡Hola {ctx.first_name}! Bienvenido a la Mente Colmena Synergix.\n\n"
            f"Tu identidad está anclada en BNB Greenfield (0-bytes).\n"
            f"Rango: {ctx.rank} | Puntos: {ctx.points}\n\n"
            f"Escribe tus aportes técnicos para ganar puntos o haz preguntas al Cerebro Colectivo."
        )
        ctx.welcomed = True
        await message.answer(welcome_text)

async def ranking_handler(message: types.Message):
    """Comando #S — Ranking de usuarios en tiempo real."""
    users_list = await list_objects(USERS_PREFIX)
    # Por brevedad, simulamos la lectura masiva de metadatos 
    # (En prod usaríamos un indexer o caché de metadatos)
    rank_msg = f"🏆 TOP 10 SYNERGIX — Mente Colmena\n"
    rank_msg += f"Total de nodos activos: {len(users_list)}\n\n"
    
    # Simulación de carga (solo demostrativo, requiere iterar users_list)
    rank_msg += "1. [Arquitecto] @MasterNode — 15400 pts\n"
    rank_msg += "2. [Mente Colmena] @DevSync — 8200 pts\n"
    await message.answer(rank_msg)

async def chat_handler(message: types.Message, ctx: UserContext):
    """Procesador principal de mensajes con prioridad de rango y RAM-lock."""
    # 1. ¿Es un aporte o una pregunta?
    if len(message.text) > 100:
        # Evaluar aporte con el Juez
        evaluacion = await manage_ai_call(ctx.uid, ctx.rank, ask_judge(message.text))
        if evaluacion.calificacion > 7:
            ctx.points += 10
            await message.answer(f"✅ Aporte valioso (+10 pts). Categoría: {evaluacion.categoria}")
        else:
            await message.answer(f"ℹ️ Aporte registrado, pero no alcanzó el umbral de calidad.")
    else:
        # Preguntar al Pensador con RAG (Placeholder de RAG)
        response = await manage_ai_call(ctx.uid, ctx.rank, ask_thinker(message.text))
        await message.answer(response)

# ── Scheduler ─────────────────────────────────────────────────────────────────

async def fusion_brain_task():
    logger.info("[Scheduler] Iniciando Fusión del Cerebro (10m)...")
    # Lógica de scripts/fusion_brain.py se llamaría aquí

async def notification_task():
    logger.info("[Scheduler] Enviando notificaciones de puntos residuales (23:59)...")

# ── Setup del Bot ─────────────────────────────────────────────────────────────

async def start_bot():
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    
    # Registrar Middleware
    dp.message.middleware(IdentityMiddleware())
    
    # Registrar Handlers
    dp.message.register(welcome_handler, Command("start"))
    dp.message.register(ranking_handler, lambda m: m.text == "#S")
    dp.message.register(chat_handler)
    
    # Configurar Scheduler
    scheduler = AsyncIOScheduler()
    scheduler.add_job(fusion_brain_task, 'interval', minutes=FUSION_INTERVAL_MINUTES)
    scheduler.add_job(notification_task, 'cron', hour=23, minute=59)
    scheduler.start()
    
    logger.info("Bot Synergix encendido. Escuchando Greenfield...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(start_bot())
