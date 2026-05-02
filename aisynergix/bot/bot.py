import os
import asyncio
import logging
from typing import Optional, Dict, Any

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    BotCommand,
)
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from aisynergix.bot.locales import (
    t,
    get_lang_name,
    get_lang_flag,
    LANG_NAMES,
    TELEGRAM_LANG_MAP,
    load_all_locales,
)
from aisynergix.bot.fsm import get_ghost_state_manager
from aisynergix.ai.manager import get_ai_manager
from aisynergix.bot.identity import get_identity_manager


logger = logging.getLogger("synergix.bot")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    raise EnvironmentError("TELEGRAM_TOKEN no configurada en el entorno.")

bot = Bot(token=TELEGRAM_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()


def get_main_keyboard(lang: str) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=t("btn_contribute", lang)),
                KeyboardButton(text=t("btn_status", lang)),
            ],
            [
                KeyboardButton(text=t("btn_memory", lang)),
                KeyboardButton(text=t("btn_language", lang)),
            ],
        ],
        resize_keyboard=True,
        persistent=True,
    )


def get_language_inline_keyboard() -> InlineKeyboardMarkup:
    buttons = []
    row = []
    
    for idx, (code, name) in enumerate(LANG_NAMES.items()):
        row.append(InlineKeyboardButton(text=name, callback_data=f"lang:{code}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    
    if row:
        buttons.append(row)
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_welcome_inline_keyboard(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("btn_contribute", lang),
                    callback_data="action:contribute",
                ),
                InlineKeyboardButton(
                    text=t("btn_status", lang),
                    callback_data="action:status",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t("btn_memory", lang),
                    callback_data="action:memory",
                ),
                InlineKeyboardButton(
                    text=t("btn_language", lang),
                    callback_data="action:language",
                ),
            ],
        ]
    )


async def detect_user_language(message: Message) -> str:
    if message.from_user and message.from_user.language_code:
        telegram_lang = message.from_user.language_code.lower()
        mapped = TELEGRAM_LANG_MAP.get(telegram_lang)
        if mapped:
            return mapped
        if telegram_lang in LANG_NAMES:
            return telegram_lang
    
    return "es"


async def get_user_language(uid: int) -> str:
    identity = get_identity_manager()
    return await identity.get_language(uid)


@dp.message(Command("start"))
async def cmd_start(message: Message, state: FsmContext = None) -> None:
    if not message.from_user:
        return
    
    uid = message.from_user.id
    
    identity = get_identity_manager()
    ghost = get_ghost_state_manager()
    ai = get_ai_manager()
    
    detected_lang = await detect_user_language(message)
    
    exists = await identity.get_profile(uid)
    is_new = exists.points == 0 and exists.total_uses_count == 0
    
    if is_new:
        await ai.set_language(uid, detected_lang)
        lang = detected_lang
        welcome_text = t("welcome", lang)
    else:
        lang = exists.language
        welcome_text = t("welcome_back", lang)
    
    await ghost.reset_state(uid)
    
    keyboard = get_main_keyboard(lang)
    inline_kb = get_welcome_inline_keyboard(lang)
    
    await message.answer(welcome_text, reply_markup=keyboard)
    await message.answer("⚡ ¿Qué deseas hacer?", reply_markup=inline_kb)


@dp.message(F.text == "🔥 Contribuir")
@dp.message(F.text == "🔥 Contribute")
@dp.message(F.text == "🔥 贡献")
@dp.message(F.text == "🔥 योगदान")
@dp.message(F.text == "🔥 ساهم")
@dp.message(F.text == "🔥 Contribuer")
@dp.message(F.text == "🔥 অবদান")
@dp.message(F.text == "🔥 Contribuir")
@dp.message(F.text == "🔥 Berkontribusi")
@dp.message(F.text == "🔥 تعاون کریں")
async def handle_contribute_button(message: Message) -> None:
    if not message.from_user:
        return
    
    uid = message.from_user.id
    lang = await get_user_language(uid)
    
    ghost = get_ghost_state_manager()
    await ghost.enter_contribution_mode(uid)
    
    contribute_text = t("contribution_mode", lang)
    await message.answer(contribute_text)


@dp.message(F.text == "📊 Ver estado")
@dp.message(F.text == "📊 View Status")
@dp.message(F.text == "📊 查看状态")
@dp.message(F.text == "📊 स्थिति देखें")
@dp.message(F.text == "📊 عرض الحالة")
@dp.message(F.text == "📊 Voir l'état")
@dp.message(F.text == "📊 অবস্থা দেখুন")
@dp.message(F.text == "📊 Ver estado")
@dp.message(F.text == "📊 Lihat Status")
@dp.message(F.text == "📊 حالت دیکھیں")
async def handle_status_button(message: Message) -> None:
    if not message.from_user:
        return
    
    uid = message.from_user.id
    lang = await get_user_language(uid)
    
    ghost = get_ghost_state_manager()
    await ghost.reset_state(uid)
    
    ai = get_ai_manager()
    status = await ai.get_user_status(uid)
    
    status_text = t(
        "status_msg",
        lang,
        rank=status["rank"],
        points=status["points"],
        daily_aportes_count=status["daily_aportes_count"],
        daily_limit=status["daily_limit"],
        contribution_count=status["contribution_count"],
        total_uses_count=status["total_uses_count"],
        language=get_lang_name(status["language"]),
    )
    
    if status.get("next_rank"):
        next_info = status["next_rank"]
        status_text += f"\n\n🔜 Próximo rango: {next_info['name']} (faltan {next_info['points_needed']} pts)"
    
    await message.answer(status_text)


@dp.message(F.text == "🧠 Mi memoria")
@dp.message(F.text == "🧠 My Memory")
@dp.message(F.text == "🧠 我的记忆")
@dp.message(F.text == "🧠 मेरी स्मृति")
@dp.message(F.text == "🧠 ذاكرتي")
@dp.message(F.text == "🧠 Ma mémoire")
@dp.message(F.text == "🧠 আমার স্মৃতি")
@dp.message(F.text == "🧠 Minha memória")
@dp.message(F.text == "🧠 Memori Saya")
@dp.message(F.text == "🧠 میری یادداشت")
async def handle_memory_button(message: Message) -> None:
    if not message.from_user:
        return
    
    uid = message.from_user.id
    lang = await get_user_language(uid)
    
    ghost = get_ghost_state_manager()
    await ghost.reset_state(uid)
    
    from aisynergix.services.greenfield import get_greenfield_client
    
    gf = await get_greenfield_client()
    contributions = await gf.list_user_contributions(uid, limit=10)
    
    if not contributions:
        await message.answer(t("memory_empty", lang))
        return
    
    memory_text = t("memory_header", lang)
    
    for contrib in contributions:
        try:
            text = await gf.get_contribution_text(contrib["object_name"])
            tags = contrib.get("tags", {})
            quality = tags.get("quality_score", "N/A")
            
            preview = text[:120] + "..." if len(text) > 120 else text
            memory_text += t("memory_entry", lang, text=preview, quality=quality) + "\n"
        except Exception:
            continue
    
    memory_text += t("memory_footer", lang, count=len(contributions))
    await message.answer(memory_text)


@dp.message(F.text == "🌐 Idioma")
@dp.message(F.text == "🌐 Language")
@dp.message(F.text == "🌐 语言")
@dp.message(F.text == "🌐 भाषा")
@dp.message(F.text == "🌐 اللغة")
@dp.message(F.text == "🌐 Langue")
@dp.message(F.text == "🌐 ভাষা")
@dp.message(F.text == "🌐 Idioma")
@dp.message(F.text == "🌐 Bahasa")
@dp.message(F.text == "🌐 زبان")
async def handle_language_button(message: Message) -> None:
    if not message.from_user:
        return
    
    uid = message.from_user.id
    lang = await get_user_language(uid)
    
    ghost = get_ghost_state_manager()
    await ghost.reset_state(uid)
    
    inline_kb = get_language_inline_keyboard()
    await message.answer(t("language_select", lang), reply_markup=inline_kb)


@dp.callback_query(F.data.startswith("lang:"))
async def handle_language_selection(callback: CallbackQuery) -> None:
    if not callback.from_user or not callback.data or not callback.message:
        return
    
    uid = callback.from_user.id
    lang_code = callback.data.split(":", 1)[1]
    
    if lang_code not in LANG_NAMES:
        await callback.answer("Idioma no soportado")
        return
    
    ai = get_ai_manager()
    success, _ = await ai.set_language(uid, lang_code)
    
    if success:
        lang_name = get_lang_name(lang_code)
        flag = get_lang_flag(lang_code)
        
        set_text = t("language_set", lang_code, lang_name=lang_name, flag=flag)
        
        keyboard = get_main_keyboard(lang_code)
        
        await callback.message.edit_text(set_text)
        await callback.message.answer(
            "✅ " + lang_name,
            reply_markup=keyboard,
        )
        
        await callback.answer()
    else:
        await callback.answer("Error al cambiar idioma")


@dp.callback_query(F.data.startswith("action:"))
async def handle_welcome_actions(callback: CallbackQuery) -> None:
    if not callback.from_user or not callback.data or not callback.message:
        return
    
    uid = callback.from_user.id
    action = callback.data.split(":", 1)[1]
    lang = await get_user_language(uid)
    
    if action == "contribute":
        ghost = get_ghost_state_manager()
        await ghost.enter_contribution_mode(uid)
        await callback.message.edit_text(t("contribution_mode", lang))
    elif action == "status":
        ai = get_ai_manager()
        status = await ai.get_user_status(uid)
        status_text = t(
            "status_msg",
            lang,
            rank=status["rank"],
            points=status["points"],
            daily_aportes_count=status["daily_aportes_count"],
            daily_limit=status["daily_limit"],
            contribution_count=status["contribution_count"],
            total_uses_count=status["total_uses_count"],
            language=get_lang_name(status["language"]),
        )
        await callback.message.edit_text(status_text)
    elif action == "memory":
        await handle_memory_button(callback.message)
    elif action == "language":
        inline_kb = get_language_inline_keyboard()
        await callback.message.edit_text(
            t("language_select", lang),
            reply_markup=inline_kb,
        )
    
    await callback.answer()


@dp.message()
async def handle_free_conversation(message: Message) -> None:
    if not message.from_user:
        return
    
    if message.text is None:
        return
    
    uid = message.from_user.id
    text = message.text.strip()
    lang = await get_user_language(uid)
    
    ghost = get_ghost_state_manager()
    current_state = await ghost.get_state(uid)
    
    if current_state == "awaiting_contribution":
        await handle_contribution_message(message, uid, text, lang)
        return
    
    await handle_conversation_message(message, uid, text, lang)


async def handle_contribution_message(
    message: Message,
    uid: int,
    text: str,
    lang: str,
) -> None:
    ghost = get_ghost_state_manager()
    ai = get_ai_manager()
    
    await ghost.exit_contribution_mode(uid)
    
    await message.answer(t("contribution_received", lang))
    
    try:
        result = await ai.process_contribution(uid, text)
        
        message_key = result.get("message_key", "contribution_success")
        
        if result["status"] == "success":
            if result.get("tier") == "legendary":
                message_key = "contribution_success_legendary"
            elif result.get("tier") == "elite":
                message_key = "contribution_success_elite"
            
            response_text = t(
                message_key,
                lang,
                cid=result.get("cid", ""),
                quality_score=result.get("quality_score", 0),
                points_gained=result.get("points_gained", 0),
                new_total_points=result.get("new_total_points", 0),
                rank=result.get("rank", "🌱 Iniciado"),
            )
            
            keyboard = get_main_keyboard(lang)
            await message.answer(response_text, reply_markup=keyboard)
            
            if result.get("new_rank"):
                new_rank_text = t(
                    "rank_up",
                    lang,
                    old_rank=result.get("rank", ""),
                    new_rank=result["new_rank"],
                )
                await message.answer(new_rank_text)
        
        elif result["status"] == "too_short":
            await message.answer(t("contribution_too_short", lang))
        elif result["status"] == "duplicate":
            await message.answer(t("contribution_duplicate", lang))
        elif result["status"] == "quota_exceeded":
            await message.answer(
                t("quota_exceeded", lang, daily_limit=result.get("daily_limit", 5))
            )
        elif result["status"] == "rejected":
            feedback = result.get("evaluation", {}).get("constructive_feedback", "")
            await message.answer(
                t("contribution_rejected", lang, feedback=feedback)
            )
    
    except Exception as e:
        logger.error(f"Error procesando aporte de {uid}: {e}")
        await message.answer(t("error_generic", lang))


async def handle_conversation_message(
    message: Message,
    uid: int,
    text: str,
    lang: str,
) -> None:
    ai = get_ai_manager()
    
    processing_msg = None
    
    try:
        response, search_results = await ai.process_conversation(uid, text)
        
        await message.answer(response)
    
    except Exception as e:
        logger.error(f"Error en conversación libre de {uid}: {e}")
        await message.answer(t("error_generic", lang))


async def set_bot_commands():
    commands = [
        BotCommand(command="start", description="🔥 Iniciar / Despertar a Synergix"),
    ]
    await bot.set_my_commands(commands)


async def on_startup():
    logger.info("🚀 Iniciando Nodo Fantasma Synergix...")
    
    await load_all_locales()
    logger.info(f"🌐 Locales cargados: {list(LOCALES.keys())}")
    
    await set_bot_commands()
    
    from aisynergix.services.rag_engine import get_rag_engine
    rag = await get_rag_engine()
    await rag.rebuild_from_bucket()
    logger.info("🧠 Motor RAG inicializado")
    
    ai = get_ai_manager()
    health = await ai.health_check()
    logger.info(f"🤖 IA Health: {health}")
    
    logger.info("✅ Synergix Nodo Fantasma listo")


async def on_shutdown():
    logger.info("🛑 Apagando Nodo Fantasma...")
    
    from aisynergix.services.greenfield import get_greenfield_client
    gf = await get_greenfield_client()
    await gf.close()


async def main():
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    
    await dp.start_polling(bot)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    asyncio.run(main())
