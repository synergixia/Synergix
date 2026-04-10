import asyncio
import json
import logging
import os
import sys
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from dotenv import load_dotenv

from aisynergix.bot.identity import hydrate_user, dehydrate_user
from aisynergix.bot.fsm import set_state
from aisynergix.services.greenfield import upload_aporte, update_user_metadata
from aisynergix.services.rag_engine import get_related_context
from aisynergix.ai.local_ia import ask_judge, ask_thinker
from aisynergix.config.constants import MASTER_UIDS, RANK_TABLE
from aisynergix.ai.manager import sem

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("SynergixGhostNode")

HEADER = """
╔══════════════════════════════════════════════════════════════════════════════╗
║  SYNERGIX — Primera IA con Memoria Inmortal en Web3                          ║
║  Arquitectura: Stateless / Ghost Protocol (Zero-Knowledge)                   ║
║  Servidor: Hetzner 8GB | Blockchain: BNB Greenfield | LLM: Qwen Local        ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN")
bot = Bot(token=TOKEN)
dp = Dispatcher()

with open("aisynergix/config/locales.json", "r", encoding="utf-8") as f:
    T = json.load(f)

def get_menu_kb(lang: str) -> ReplyKeyboardMarkup:
    """Recrea la interfaz 2x2 original de Synergix adaptada al idioma."""
    kb = [
        [KeyboardButton(text=T[lang]["btn_contribute"]), KeyboardButton(text=T[lang]["btn_status"])],
        [KeyboardButton(text=T[lang]["btn_memory"]), KeyboardButton(text=T[lang]["btn_language"])]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True, is_persistent=True)

def get_progress_bar(pts: int, next_pts: int) -> str:
    """Calcula la barra de progreso ASCII para la gamificación visual."""
    if next_pts == 0: 
        return "██████████ 100%"
    percent = min(int((pts / next_pts) * 10), 10)
    bar = "█" * percent + "░" * (10 - percent)
    return f"{bar} {percent * 10}%"

@dp.message.outer_middleware()
async def identity_middleware(handler, event, data):
    """
    Middleware Core: Resucita la identidad fantasma del usuario desde BNB Greenfield 
    antes de que procese cualquier mensaje, y guarda los cambios al terminar.
    """
    uid = str(event.from_user.id)
    user_context = await hydrate_user(uid)
    data["user"] = user_context
    try:
        return await handler(event, data)
    finally:
        await dehydrate_user(user_context)

@dp.message(CommandStart())
async def cmd_start(message: types.Message, user):
    lang = user.language
    welcome_txt = T[lang]["welcome"].format(
        name=message.from_user.first_name,
        challenge="Arquitectura Stateless Web3"
    )
    await message.answer(welcome_txt, reply_markup=get_menu_kb(lang), parse_mode="MarkdownV2")

@dp.message(F.text.in_([T["es"]["btn_status"], T["en"]["btn_status"], T["zh_cn"]["btn_status"], T["zh"]["btn_status"]]))
async def view_status(message: types.Message, user):
    lang = user.language
    rank_info = user.get_rank_info()
    next_pts = rank_info["next_pts"]
    progress = get_progress_bar(user.points, next_pts)
    
    status_txt = T[lang]["status_msg"].format(
        total="4.2k",
        challenge="Ghost Node Protocol",
        name=message.from_user.first_name,
        pts=user.points,
        contribs=user.impact_index,
        impact=user.impact_index * 2,
        rank=rank_info["name"].replace(" ", "\\ "),
        benefit=rank_info["benefit"],
        progress_bar=progress,
        next_rank=f"{next_pts - user.points} pts para {rank_info['next_rank']}" if rank_info['next_rank'] else "Nivel Máximo"
    )
    await message.answer(status_txt, parse_mode="MarkdownV2")

@dp.message(F.text.in_([T["es"]["btn_language"], T["en"]["btn_language"], T["zh_cn"]["btn_language"], T["zh"]["btn_language"]]))
async def change_lang_menu(message: types.Message, user):
    lang = user.language
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Español 🇪🇸", callback_data="setlang_es"), InlineKeyboardButton(text="English 🇺🇸", callback_data="setlang_en")],
        [InlineKeyboardButton(text="简体中文 🇨🇳", callback_data="setlang_zh_cn"), InlineKeyboardButton(text="繁體中文 🇭🇰", callback_data="setlang_zh")]
    ])
    await message.answer(T[lang]["choose_lang"], reply_markup=kb)

@dp.callback_query(F.data.startswith("setlang_"))
async def set_lang(callback: types.CallbackQuery, user):
    new_lang = callback.data.split("_")[1]
    if new_lang == "zh" and callback.data == "setlang_zh_cn": 
        new_lang = "zh_cn"
    
    user.language = new_lang
    await update_user_metadata(user.uid, {"language": new_lang})
    await callback.message.edit_text(T[new_lang]["lang_updated"])
    await callback.message.answer("Synergix 🔄", reply_markup=get_menu_kb(new_lang))

@dp.message(F.text.in_([T["es"]["btn_contribute"], T["en"]["btn_contribute"], T["zh_cn"]["btn_contribute"], T["zh"]["btn_contribute"]]))
async def start_contrib(message: types.Message, user):
    await set_state(user, "AWAITING_CONTRIB")
    await message.answer(T[user.language]["await_contrib"], parse_mode="MarkdownV2")

@dp.message(F.text)
async def nlp_router(message: types.Message, user):
    lang = user.language
    
    # Control de Límite de Inferencia (Daily Quota)
    if int(user.uid) not in MASTER_UIDS and user.daily_quota <= 0:
        rank_info = user.get_rank_info()
        await message.answer(T[lang]["error_quota"].format(rank=rank_info["name"]), parse_mode="MarkdownV2")
        return

    # Flujo de Aportes Inmortales (Evaluado por el Juez 0.5B)
    if user.fsm_state == "AWAITING_CONTRIB":
        if len(message.text) < 20:
            await message.answer(T[lang]["contrib_short"], parse_mode="MarkdownV2")
            return
        
        async with sem:
            judgment = await ask_judge(message.text)
        
        if judgment.get("score", 0) >= 5:
            await upload_aporte(user.uid, message.text, {"score": judgment["score"]})
            pts_earned = int(judgment["score"] * 10)
            user.points += pts_earned
            user.impact_index += 1
            await set_state(user, "IDLE")
            
            # Actualiza rango en RAM para verificar ascensos
            for i, rank in enumerate(RANK_TABLE):
                if user.points >= rank["min_pts"]:
                    user.rank = rank["name"]
            
            await message.answer(T[lang]["contrib_ok"].format(pts=pts_earned), parse_mode="MarkdownV2")
        else:
            await set_state(user, "IDLE")
            await message.answer(T[lang]["contrib_rejected"], parse_mode="MarkdownV2")
        return

    # Flujo de Interacción Libre con la Memoria RAG (Pensador 1.5B)
    async with sem:
        context = await get_related_context(message.text)
        response = await ask_thinker(message.text, context, lang)
        if int(user.uid) not in MASTER_UIDS:
            user.daily_quota -= 1
        await message.answer(response, parse_mode="MarkdownV2")

async def main():
    print(HEADER)
    logger.info("Secuencia de ignición completada. Synergix Node Online.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
