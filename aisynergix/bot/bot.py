"""
bot.py — Orquestador principal de Telegram para Synergix Ghost Node.
Middleware de identidad con caché LRU, handlers multilingües, botones permanentes,
comando #S con top10.json estático, y scheduler integrado para tareas periódicas.
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from aiogram import Bot, Dispatcher, types, F, BaseMiddleware
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardRemove
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from aisynergix.config.constants import (
    TELEGRAM_BOT_TOKEN,
    LOCAL_TOP10_JSON_PATH,
    LOCAL_DATA_DIR,
    FUSION_INTERVAL_MINUTES,
    DAILY_NOTIFICATION_TIME,
    WEEKLY_CHALLENGE_DAY,
    WEEKLY_CHALLENGE_TIME,
    get_rank_for_points
)
from aisynergix.bot.identity import (
    hydrate_user,
    dehydrate_user,
    UserContext,
    get_cache_stats
)
from aisynergix.bot.fsm import SynergixStates, update_user_fsm_state
from aisynergix.services.greenfield import upload_aporte, get_top10_json
from aisynergix.services.rag_engine import rag_engine
from aisynergix.ai.manager import manage_ai_call, add_residual_points, get_orchestrator_stats
from aisynergix.ai.local_ia import ask_judge, ask_thinker
from aisynergix.config.system_prompts import validate_judge_response

# Cargar locales
try:
    with open("locales.json", "r", encoding="utf-8") as f:
        LOCALES = json.load(f)
except FileNotFoundError:
    logger = logging.getLogger(__name__)
    logger.error("Archivo locales.json no encontrado")
    LOCALES = {
        "es": {
            "welcome": "¡Bienvenido a Synergix, {name}! 🧠",
            "btn_contribute": "🔥 Contribuir",
            "btn_status": "📊 Mi Estado",
            "btn_language": "🌐 Idioma",
            "status_msg": "📊 *Synergix — Estado*\n\n📈 Puntos: {pts}\n🏅 Rango: {rank}",
            "choose_lang": "Selecciona tu idioma:",
            "lang_updated": "✅ Idioma actualizado.",
            "await_contrib": "🖋️ *Modo Aporte Activo*\n\nEnvía conocimiento técnico.",
            "contrib_ok": "✅ *Aporte Aceptado*\n\n+{pts} puntos.",
            "contrib_rejected": "❌ *Aporte Insuficiente*",
            "contrib_short": "⚠️ *Aporte demasiado corto (mínimo 20 caracteres).*",
            "error_quota": "⚠️ *Cuota Diaria Agotada*"
        }
    }

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# KEYBOARDS Y INTERFAZ DE USUARIO
# ─────────────────────────────────────────────────────────────────────────────

def get_main_keyboard(language: str = "es") -> ReplyKeyboardMarkup:
    """
    Crea el teclado principal con botones permanentes.
    
    Args:
        language: Idioma para los textos de los botones
    
    Returns:
        ReplyKeyboardMarkup: Teclado principal
    """
    locale = LOCALES.get(language, LOCALES["es"])
    
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=locale["btn_contribute"]),
                KeyboardButton(text=locale["btn_status"])
            ],
            [
                KeyboardButton(text=locale["btn_language"]),
                KeyboardButton(text="#S")
            ]
        ],
        resize_keyboard=True,
        persistent=True,  # Botones permanentes
        selective=True    # Solo para el usuario
    )
    return keyboard


def get_language_keyboard() -> InlineKeyboardMarkup:
    """
    Crea teclado inline para selección de idioma.
    
    Returns:
        InlineKeyboardMarkup: Teclado de idiomas
    """
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🇪🇸 Español", callback_data="lang_es"),
                InlineKeyboardButton(text="🇺🇸 English", callback_data="lang_en")
            ],
            [
                InlineKeyboardButton(text="🇨🇳 简体中文", callback_data="lang_zh_cn"),
                InlineKeyboardButton(text="🇹🇼 繁體中文", callback_data="lang_zh")
            ]
        ]
    )
    return keyboard


# ─────────────────────────────────────────────────────────────────────────────
# MIDDLEWARE DE IDENTIDAD CON CACHÉ LRU
# ─────────────────────────────────────────────────────────────────────────────

class IdentityMiddleware(BaseMiddleware):
    """
    Middleware que hidrata/deshidrata el contexto del usuario en cada mensaje.
    Usa caché LRU para minimizar peticiones a Greenfield.
    """
    
    async def __call__(
        self,
        handler,
        event: types.Message | types.CallbackQuery,
        data: Dict[str, Any]
    ):
        # Solo procesar mensajes y callbacks de usuarios
        if not isinstance(event, (types.Message, types.CallbackQuery)):
            return await handler(event, data)
        
        user = event.from_user
        if not user:
            return await handler(event, data)
        
        # Hidratar usuario desde Greenfield (con caché)
        ctx = await hydrate_user(str(user.id), user.first_name or "Usuario")
        data["ctx"] = ctx
        
        # Restaurar estado FSM si está en un estado específico
        fsm_context = data.get("state")
        if fsm_context and ctx.fsm_state != "IDLE":
            from aisynergix.bot.fsm import string_to_state
            try:
                current_state = string_to_state(ctx.fsm_state)
                await fsm_context.set_state(current_state)
                logger.debug(f"Estado FSM restaurado para {ctx.uid_ofuscado[:8]}: {ctx.fsm_state}")
            except Exception as e:
                logger.error(f"Error restaurando estado FSM: {e}")
        
        try:
            # Ejecutar handler
            result = await handler(event, data)
            return result
        finally:
            # Deshidratar (sincronización lazy a Greenfield)
            await dehydrate_user(ctx)


# ─────────────────────────────────────────────────────────────────────────────
# HANDLERS PRINCIPALES
# ─────────────────────────────────────────────────────────────────────────────

async def start_handler(message: types.Message, ctx: UserContext):
    """
    Handler para /start - Onboarding y bienvenida.
    """
    locale = LOCALES.get(ctx.language, LOCALES["es"])
    
    # Mensaje de bienvenida
    welcome_text = locale["welcome"].format(name=ctx.first_name)
    
    # Onboarding si es nuevo
    if not ctx.welcomed:
        onboarding_text = (
            f"{welcome_text}\n\n"
            f"🔗 Tu identidad está anclada en BNB Greenfield (0-bytes).\n"
            f"🏅 Rango actual: {ctx.rank} | 📈 Puntos: {ctx.points}\n\n"
            f"💡 *Cómo funciona:*\n"
            f"• Comparte conocimiento → ganas puntos\n"
            f"• Cuando tu aporte ayuda a otros → puntos residuales\n"
            f"• Puntos desbloquean rangos: Iniciado → Oráculo\n\n"
            f"⚙️ *Comandos útiles:*\n"
            f"• #S - Ver ranking Top 10\n"
            f"• Usa los botones para contribuir o ver tu estado\n\n"
            f"El conocimiento compartido es el único recurso que crece al darlo. 🚀"
        )
        
        ctx.welcomed = True
        await message.answer(
            onboarding_text,
            reply_markup=get_main_keyboard(ctx.language),
            parse_mode="Markdown"
        )
        
        logger.info(f"Nuevo usuario onboarded: {ctx.uid_ofuscado} ({ctx.first_name})")
    else:
        await message.answer(
            welcome_text,
            reply_markup=get_main_keyboard(ctx.language),
            parse_mode="Markdown"
        )


async def ranking_handler(message: types.Message):
    """
    Handler para #S - Ranking Top 10 desde archivo local estático.
    NO hace peticiones a Greenfield para máxima velocidad.
    """
    try:
        # Leer top10.json desde archivo local
        if not os.path.exists(LOCAL_TOP10_JSON_PATH):
            await message.answer(
                "🏆 *Ranking Synergix*\n\n"
                "El ranking se está generando... Intenta en unos minutos.",
                parse_mode="Markdown"
            )
            return
        
        with open(LOCAL_TOP10_JSON_PATH, "r", encoding="utf-8") as f:
            ranking_data = json.load(f)
        
        ranking = ranking_data.get("ranking", [])
        total_users = ranking_data.get("total_users", 0)
        generated_at = ranking_data.get("generated_at", "")
        
        # Construir mensaje
        msg = "🏆 *TOP 10 SYNERGIX — Mente Colmena*\n\n"
        
        for i, user in enumerate(ranking[:10], 1):
            name = user.get("name", "Usuario")
            points = user.get("points", 0)
            rank = user.get("rank", "Iniciado")
            
            emoji = "👑" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
            msg += f"{emoji} `{rank}` **{name}** — {points} pts\n"
        
        msg += f"\n📊 Total de nodos activos: *{total_users}*\n"
        if generated_at:
            msg += f"🕐 Actualizado: {generated_at}"
        
        await message.answer(msg, parse_mode="Markdown")
        logger.info(f"Ranking mostrado (#S) - {total_users} usuarios totales")
        
    except json.JSONDecodeError as e:
        logger.error(f"Error parseando top10.json: {e}")
        await message.answer(
            "⚠️ Error cargando el ranking. El cerebro se está sincronizando...",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Error en ranking_handler: {e}", exc_info=True)
        await message.answer(
            "⚠️ Error temporal obteniendo el ranking.",
            parse_mode="Markdown"
        )


async def contribute_handler(message: types.Message, ctx: UserContext, state: FSMContext):
    """
    Handler para botón 'Contribuir' - Inicia modo aporte.
    """
    locale = LOCALES.get(ctx.language, LOCALES["es"])
    
    # Verificar cuota diaria
    if ctx.daily_quota <= 0:
        await message.answer(
            locale["error_quota"].format(rank=ctx.rank),
            parse_mode="Markdown"
        )
        return
    
    # Actualizar estado FSM
    await update_user_fsm_state(ctx.uid_ofuscado, SynergixStates.AWAITING_APORTE, ctx)
    await state.set_state(SynergixStates.AWAITING_APORTE)
    
    await message.answer(
        locale["await_contrib"],
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="Markdown"
    )
    logger.info(f"Usuario {ctx.uid_ofuscado[:8]} entró en modo aporte")


async def status_handler(message: types.Message, ctx: UserContext):
    """
    Handler para botón 'Mi Estado' - Muestra estadísticas del usuario.
    """
    locale = LOCALES.get(ctx.language, LOCALES["es"])
    
    # Calcular progreso hacia siguiente rango
    current_threshold = 0
    next_threshold = 100  # Por defecto
    next_rank = "Activo"
    
    ranks = ["Iniciado", "Activo", "Sincronizado", "Arquitecto", "Mente Colmena", "Oráculo"]
    thresholds = [0, 100, 500, 1500, 5000, 15000]
    
    for i, rank in enumerate(ranks):
        if rank == ctx.rank:
            current_threshold = thresholds[i]
            if i + 1 < len(ranks):
                next_threshold = thresholds[i + 1]
                next_rank = ranks[i + 1]
            break
    
    progress = ctx.points - current_threshold
    total_needed = next_threshold - current_threshold
    progress_percent = (progress / total_needed * 100) if total_needed > 0 else 100
    
    # Barra de progreso ASCII
    bar_length = 20
    filled = int(progress_percent / 100 * bar_length)
    progress_bar = "[" + "█" * filled + "░" * (bar_length - filled) + "]"
    
    # Beneficio del rango actual
    benefits = {
        "Iniciado": "Acceso básico al cerebro colectivo",
        "Activo": "+5 mensajes diarios, ranking visible",
        "Sincronizado": "+10 mensajes, prioridad en cola IA",
        "Arquitecto": "+20 mensajes, acceso a retos semanales",
        "Mente Colmena": "+50 mensajes, votación en evoluciones",
        "Oráculo": "Sin límites, administración del nodo"
    }
    
    status_msg = locale["status_msg"].format(
        pts=ctx.points,
        rank=ctx.rank,
        benefit=benefits.get(ctx.rank, "Acceso completo"),
        progress_bar=progress_bar,
        next_rank=next_rank,
        mult="1.5" if ctx.rank in ["Sincronizado", "Arquitecto"] else "1.0",
        quota=ctx.daily_quota
    )
    
    await message.answer(status_msg, parse_mode="Markdown")
    logger.debug(f"Estado mostrado para {ctx.uid_ofuscado[:8]}: {ctx.rank}, {ctx.points} pts")


async def language_handler(message: types.Message, ctx: UserContext, state: FSMContext):
    """
    Handler para botón 'Idioma' - Menú de selección de idioma.
    """
    locale = LOCALES.get(ctx.language, LOCALES["es"])
    
    await update_user_fsm_state(ctx.uid_ofuscado, SynergixStates.CONFIGURING_LANGUAGE, ctx)
    await state.set_state(SynergixStates.CONFIGURING_LANGUAGE)
    
    await message.answer(
        locale["choose_lang"],
        reply_markup=get_language_keyboard(),
        parse_mode="Markdown"
    )


async def language_callback_handler(callback: types.CallbackQuery, ctx: UserContext, state: FSMContext):
    """
    Handler para selección de idioma via callback.
    """
    lang_code = callback.data.replace("lang_", "")
    
    if lang_code in LOCALES:
        ctx.language = lang_code
        locale = LOCALES[lang_code]
        
        await callback.message.edit_text(
            locale["lang_updated"],
            parse_mode="Markdown"
        )
        
        # Volver al estado IDLE
        await update_user_fsm_state(ctx.uid_ofuscado, SynergixStates.IDLE, ctx)
        await state.set_state(SynergixStates.IDLE)
        
        # Mostrar teclado principal actualizado
        await callback.message.answer(
            LOCALES[lang_code]["welcome"].format(name=ctx.first_name),
            reply_markup=get_main_keyboard(lang_code),
            parse_mode="Markdown"
        )
        
        logger.info(f"Idioma actualizado para {ctx.uid_ofuscado[:8]}: {lang_code}")
    else:
        await callback.answer("Idioma no válido")
    
    await callback.answer()


async def aporte_handler(message: types.Message, ctx: UserContext, state: FSMContext):
    """
    Handler para procesar aportes de conocimiento en estado AWAITING_APORTE.
    """
    locale = LOCALES.get(ctx.language, LOCALES["es"])
    
    # Validar longitud mínima
    if len(message.text) < 20:
        await message.answer(
            locale["contrib_short"],
            reply_markup=get_main_keyboard(ctx.language),
            parse_mode="Markdown"
        )
        await state.set_state(SynergixStates.IDLE)
        await update_user_fsm_state(ctx.uid_ofuscado, SynergixStates.IDLE, ctx)
        return
    
    # Reducir cuota diaria
    ctx.daily_quota = max(0, ctx.daily_quota - 1)
    
    # Evaluar con el Juez (con gestión de recursos)
    judge_result = await manage_ai_call(
        ctx.uid_ofuscado,
        ctx.rank,
        ask_judge(message.text)
    )
    
    # Subir a Greenfield si es de calidad
    if judge_result.is_high_quality():
        success = await upload_aporte(
            ctx.uid_ofuscado,
            message.text,
            judge_result.calificacion,
            judge_result.categoria
        )
        
        if success:
            # Premiar al usuario
            points_to_add = 10 if judge_result.calificacion >= 9 else 5
            ctx.increment_points(points_to_add)
            
            await message.answer(
                locale["contrib_ok"].format(pts=points_to_add),
                reply_markup=get_main_keyboard(ctx.language),
                parse_mode="Markdown"
            )
            
            logger.info(f"Aporte aceptado de {ctx.uid_ofuscado[:8]}: "
                       f"score={judge_result.calificacion}, +{points_to_add} pts")
        else:
            await message.answer(
                "⚠️ *Aporte aceptado pero error subiendo a Greenfield.*\n"
                "El conocimiento se procesó localmente pero no se inmortalizó.",
                reply_markup=get_main_keyboard(ctx.language),
                parse_mode="Markdown"
            )
    else:
        await message.answer(
            locale["contrib_rejected"],
            reply_markup=get_main_keyboard(ctx.language),
            parse_mode="Markdown"
        )
        logger.info(f"Aporte rechazado de {ctx.uid_ofuscado[:8]}: "
                   f"score={judge_result.calificacion}")
    
    # Volver al estado IDLE
    await state.set_state(SynergixStates.IDLE)
    await update_user_fsm_state(ctx.uid_ofuscado, SynergixStates.IDLE, ctx)


async def chat_handler(message: types.Message, ctx: UserContext):
    """
    Handler principal para mensajes de chat (preguntas al Pensador con RAG).
    """
    # Verificar cuota diaria
    if ctx.daily_quota <= 0:
        locale = LOCALES.get(ctx.language, LOCALES["es"])
        await message.answer(
            locale["error_quota"].format(rank=ctx.rank),
            parse_mode="Markdown"
        )
        return
    
    # Reducir cuota
    ctx.daily_quota = max(0, ctx.daily_quota - 1)
    
    # Obtener contexto del RAG
    context, author_uids, rag_results = rag_engine.get_context(message.text)
    
    # Sumar puntos residuales a autores (lazy update)
    if author_uids:
        logger.info(f"RAG usado para '{message.text[:30]}...': {len(author_uids)} autores")
        
        # Para cada autor, añadir punto residual
        for author_uid in set(author_uids):  # Usar set para evitar duplicados
            # Aquí necesitaríamos obtener los metadatos del autor
            # Por simplicidad, usamos un diccionario básico
            author_metadata = {"points": "0"}  # Placeholder
            await add_residual_points(author_uid, author_metadata, points=1)
    
    # Consultar al Pensador
    response = await manage_ai_call(
        ctx.uid_ofuscado,
        ctx.rank,
        ask_thinker(
            prompt=message.text,
            context=context,
            language_hint=ctx.language
        )
    )
    
    await message.answer(response, parse_mode="Markdown")
    logger.info(f"Pensador consultado por {ctx.uid_ofuscado[:8]}: "
               f"'{message.text[:30]}...' ({len(response)} chars)")


# ─────────────────────────────────────────────────────────────────────────────
# SCHEDULER - TAREAS PERIÓDICAS
# ─────────────────────────────────────────────────────────────────────────────

async def daily_notification_task(bot: Bot):
    """
    Tarea diaria (23:59) - Notifica a usuarios sobre puntos residuales.
    """
    logger.info("[Scheduler] Ejecutando notificación diaria de puntos residuales...")
    
    # En producción, esto consultaría Greenfield para usuarios activos
    # Por ahora es un placeholder
    notification_count = 0
    
    logger.info(f"[Scheduler] Notificaciones diarias enviadas: {notification_count}")


async def fusion_brain_task():
    """
    Tarea de fusión cerebral cada 10 minutos.
    En producción ejecutaría scripts/fusion_brain.py
    """
    logger.info("[Scheduler] Iniciando fusión del cerebro (10m)...")
    
    # Placeholder - En producción se llamaría al script real
    try:
        # Simular trabajo
        stats = rag_engine.get_stats()
        logger.info(f"[Scheduler] Estadísticas RAG: {stats}")
        
        # Aquí iría la lógica de:
        # 1. Filtrar aportes con quality_score > 7
        # 2. Reconstruir índice FAISS
        # 3. Subir a DCellar
        # 4. Generar top10.json
        
    except Exception as e:
        logger.error(f"[Scheduler] Error en fusión cerebral: {e}", exc_info=True)


async def system_stats_task(bot: Bot):
    """
    Tarea periódica para loguear estadísticas del sistema.
    """
    cache_stats = get_cache_stats()
    orch_stats = get_orchestrator_stats()
    rag_stats = rag_engine.get_stats()
    
    logger.info(
        f"[Stats] Cache: hit_rate={cache_stats['hit_rate']:.2%}, "
        f"size={cache_stats['size']}/{cache_stats['maxsize']} | "
        f"Orchestrator: active={orch_stats['active_tasks']}, "
        f"processed={orch_stats['total_processed']} | "
        f"RAG: contributions={rag_stats['total_contributions']}, "
        f"index_size={rag_stats['index_size']}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# SETUP Y EJECUCIÓN DEL BOT
# ─────────────────────────────────────────────────────────────────────────────

async def setup_bot() -> Dispatcher:
    """
    Configura y retorna el dispatcher del bot con todos los handlers.
    
    Returns:
        Dispatcher: Dispatcher configurado
    """
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    
    # Registrar Middleware de Identidad
    dp.update.middleware(IdentityMiddleware())
    
    # Registrar Handlers de Comandos
    dp.message.register(start_handler, Command("start"))
    dp.message.register(ranking_handler, F.text == "#S")
    
    # Registrar Handlers de Botones
    dp.message.register(contribute_handler, F.text == LOCALES["es"]["btn_contribute"])
    dp.message.register(status_handler, F.text == LOCALES["es"]["btn_status"])
    dp.message.register(language_handler, F.text == LOCALES["es"]["btn_language"])
    
    # Registrar Handlers de Estados
    dp.message.register(aporte_handler, StateFilter(SynergixStates.AWAITING_APORTE))
    dp.callback_query.register(
        language_callback_handler,
        StateFilter(SynergixStates.CONFIGURING_LANGUAGE)
    )
    
    # Registrar Handler de Callbacks (idioma)
    dp.callback_query.register(language_callback_handler, F.data.startswith("lang_"))
    
    # Handler por defecto (chat normal)
    dp.message.register(chat_handler)
    
    return dp, bot


async def start_bot():
    """
    Punto de entrada principal del bot. Inicia el bot y el scheduler.
    """
    logger.info("=== SYNERGIX GHOST NODE BOT — INICIANDO ===")
    
    # Configurar bot
    dp, bot = await setup_bot()
    
    # Configurar scheduler
    scheduler = AsyncIOScheduler()
    
    # Fusión cerebral cada 10 minutos
    scheduler.add_job(
        fusion_brain_task,
        IntervalTrigger(minutes=FUSION_INTERVAL_MINUTES),
        id="fusion_brain",
        replace_existing=True
    )
    
    # Notificación diaria a las 23:59
    hour, minute = map(int, DAILY_NOTIFICATION_TIME.split(":"))
    scheduler.add_job(
        daily_notification_task,
        CronTrigger(hour=hour, minute=minute),
        args=[bot],
        id="daily_notification",
        replace_existing=True
    )
    
    # Estadísticas del sistema cada 5 minutos
    scheduler.add_job(
        system_stats_task,
        IntervalTrigger(minutes=5),
        args=[bot],
        id="system_stats",
        replace_existing=True
    )
    
    # Iniciar scheduler
    scheduler.start()
    logger.info(f"Scheduler iniciado con {len(scheduler.get_jobs())} tareas")
    
    # Asegurar que existe el directorio de datos
    os.makedirs(LOCAL_DATA_DIR, exist_ok=True)
    
    # Iniciar bot
    logger.info("Bot Synergix encendido. Escuchando mensajes...")
    
    try:
        await dp.start_polling(bot)
    except Exception as e:
        logger.critical(f"Error fatal en el bot: {e}", exc_info=True)
        raise
    finally:
        # Apagado limpio
        scheduler.shutdown()
        logger.info("Bot y scheduler apagados")


if __name__ == "__main__":
    # Configurar logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler("synergix_bot.log", encoding="utf-8")
        ]
    )
    
    # Ejecutar bot
    asyncio.run(start_bot())
