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
from aisynergix.services.greenfield import upload_aporte
from aisynergix.services.rag_engine import get_related_context
from aisynergix.ai.local_ia import ask_judge, ask_thinker, escape_markdown_v2
from aisynergix.config.constants import MASTER_UIDS, RANK_TABLE
from aisynergix.ai.manager import sem

# Configuración de Logging Híbrido (Consola local + Archivo auditable)
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO, 
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("logs/synergix.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("SynergixCore")

load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

if not TELEGRAM_TOKEN:
    logger.critical("TELEGRAM_TOKEN no encontrado. Revisa tu archivo .env")
    sys.exit(1)

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

# Carga en RAM del diccionario maestro
with open("aisynergix/config/locales.json", "r", encoding="utf-8") as f:
    T = json.load(f)

def get_menu_kb(lang: str) -> ReplyKeyboardMarkup:
    kb = [
        [KeyboardButton(text=T[lang]["btn_contribute"]), KeyboardButton(text=T[lang]["btn_status"])],
        [KeyboardButton(text=T[lang]["btn_language"])]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

# =====================================================================
# MIDDLEWARE WEB3: Escudo Soberano (Ghost Protocol)
# =====================================================================
@dp.message.outer_middleware()
async def ghost_identity_middleware(handler, event, data):
    """Oculta la identidad del usuario, carga su estado desde Web3 y lo preserva al final."""
    if not event.from_user: 
        return await handler(event, data)
        
    uid = str(event.from_user.id)
    user_context = await hydrate_user(uid)
    data["user"] = user_context
    
    try:
        return await handler(event, data)
    finally:
        await dehydrate_user(user_context)

# =====================================================================
# RUTAS Y COMANDOS (Lógica de Interfaz Unificada)
# =====================================================================
@dp.message(CommandStart())
async def cmd_start(message: types.Message, user):
    name = escape_markdown_v2(message.from_user.first_name)
    await message.answer(
        T[user.language]["welcome"].format(name=name),
        reply_markup=get_menu_kb(user.language), parse_mode="MarkdownV2"
    )

@dp.message(F.text.in_([T["es"]["btn_status"], T["en"]["btn_status"], T["zh_cn"]["btn_status"], T["zh"]["btn_status"]]))
async def view_status(message: types.Message, user):
    info = user.get_rank_info()
    prog = min(int((user.points / info["next_pts"]) * 10), 10) if info["next_pts"] > 0 else 10
    bar = "█" * prog + "░" * (10 - prog)
    
    # Escapamos solo las variables dinámicas para no romper el formato de la plantilla
    txt = T[user.language]["status_msg"].format(
        pts=user.points, 
        rank=escape_markdown_v2(info["name"]), 
        benefit=escape_markdown_v2(info["benefit"]),
        progress_bar=bar, 
        next_rank=escape_markdown_v2(f"{info['next_pts'] - user.points} pts"),
        mult=info["multiplier"], 
        quota=user.daily_quota
    )
    await message.answer(txt, parse_mode="MarkdownV2")

@dp.message(F.text.in_([T["es"]["btn_language"], T["en"]["btn_language"], T["zh_cn"]["btn_language"], T["zh"]["btn_language"]]))
async def lang_menu(message: types.Message, user):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Español 🇪🇸", callback_data="sl_es"), InlineKeyboardButton(text="English 🇺🇸", callback_data="sl_en")],
        [InlineKeyboardButton(text="简体中文 🇨🇳", callback_data="sl_zh_cn"), InlineKeyboardButton(text="繁體中文 🇭🇰", callback_data="sl_zh")]
    ])
    await message.answer(T[user.language]["choose_lang"], reply_markup=kb, parse_mode="MarkdownV2")

@dp.callback_query(F.data.startswith("sl_"))
async def set_lang(callback: types.CallbackQuery, user):
    new_l = callback.data.split("_")[1]
    # Corrección para sub-dialectos de la UI
    if new_l == "zh" and callback.data == "sl_zh_cn": new_l = "zh_cn"
    user.language = new_l
    await callback.message.edit_text(escape_markdown_v2(T[new_l]["lang_updated"]), parse_mode="MarkdownV2")
    await callback.message.answer("Synergix Node 🔄", reply_markup=get_menu_kb(new_l))

@dp.message(F.text.in_([T["es"]["btn_contribute"], T["en"]["btn_contribute"], T["zh_cn"]["btn_contribute"], T["zh"]["btn_contribute"]]))
async def start_contribution_mode(message: types.Message, user):
    await set_state(user, "AWAITING_CONTRIB")
    await message.answer(escape_markdown_v2(T[user.language]["await_contrib"]), parse_mode="MarkdownV2")

@dp.message(F.text)
async def main_processor(message: types.Message, user):
    lang = user.language
    
    if int(user.uid) not in MASTER_UIDS and user.daily_quota <= 0:
        return await message.answer(escape_markdown_v2(T[lang]["error_quota"].format(rank=user.rank)), parse_mode="MarkdownV2")

    # CORRECCIÓN UX: El mensaje visual aparece de inmediato ANTES de evaluar IA o buscar RAG.
    loading = await message.answer("🧬🔗🔮")

    try:
        # MODO 1: Evaluación de Aportes
        if user.fsm_state == "AWAITING_CONTRIB":
            if len(message.text) < 20:
                await loading.delete()
                return await message.answer(escape_markdown_v2(T[lang]["contrib_short"]), parse_mode="MarkdownV2")
            
            # Semáforo para proteger CPU de Hetzner
            async with sem:
                res = await ask_judge(message.text)
                
            await loading.delete()
            
            if res.get("score", 0) >= 5.0:
                info = user.get_rank_info()
                # Aplicamos el multiplicador del nivel correspondiente
                pts = int(res["score"] * 10 * info["multiplier"])
                
                user.points += pts
                user.impact_index += 1
                await set_state(user, "IDLE")
                
                # Promoción de rango en caliente
                for rank in RANK_TABLE:
                    if user.points >= rank["min_pts"]: user.rank = rank["name"]
                
                await upload_aporte(user.uid, message.text, {"score": str(res["score"])})
                await message.answer(escape_markdown_v2(T[lang]["contrib_ok"].format(pts=pts)), parse_mode="MarkdownV2")
            else:
                await set_state(user, "IDLE")
                await message.answer(escape_markdown_v2(T[lang]["contrib_rejected"]), parse_mode="MarkdownV2")
            return

        # MODO 2: Chat Libre Soberano (Pensador + RAG)
        async with sem:
            # Recuperar contexto
            context = await get_related_context(message.text)
            
            # Inferencia LLM
            answer = await ask_thinker(message.text, context, lang)
            
            if int(user.uid) not in MASTER_UIDS: 
                user.daily_quota -= 1
                
            # Elimina el indicador visual e imprime
            await loading.delete()
            await message.answer(answer, parse_mode="MarkdownV2")

    except Exception as e:
        logger.error(f"Error procesando NLP: {e}")
        try: await loading.delete()
        except: pass
        await message.answer(escape_markdown_v2("⚠️ Se produjo una anomalía en el nodo. Reintenta."), parse_mode="MarkdownV2")

# =====================================================================
# INICIALIZADOR
# =====================================================================
async def main():
    logger.info("Iniciando secuencia de ignición del Nodo Synergix...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Nodo desconectado manualmente.")
