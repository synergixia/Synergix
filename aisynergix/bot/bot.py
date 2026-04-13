import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from aisynergix.bot.identity import hydrate_user
from aisynergix.bot.fsm import set_state, get_state
from aisynergix.services.greenfield import greenfield
from aisynergix.services.rag_engine import rag_engine
from aisynergix.ai.local_ia import ask_judge, ask_thinker
from aisynergix.ai.manager import brain_manager

# Configuración de Logs (Consola y Archivo para DCellar)
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO, 
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(f"logs/{datetime.now().strftime('%Y-%m-%d')}.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("SynergixNode")

bot = Bot(token=os.getenv("TELEGRAM_TOKEN"), default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# Cargar traducciones
with open("aisynergix/config/locales.json", "r", encoding="utf-8") as f:
    T = json.load(f)

# Tabla de 6 Rangos Oficial
RANK_TABLE = [
    {"name": "🌱 Iniciado",      "min_pts": 0,      "benefit": "Acceso base (5 aportes/día)", "limit": 5},
    {"name": "📈 Activo",        "min_pts": 100,    "benefit": "Contribuidor regular (12 aportes/día)", "limit": 12},
    {"name": "🧬 Sincronizado",  "min_pts": 500,    "benefit": "Conexión estable (25 aportes/día)", "limit": 25},
    {"name": "🏗️ Arquitecto",    "min_pts": 1500,   "benefit": "Constructor de la red (40 aportes/día)", "limit": 40},
    {"name": "🧠 Mente Colmena", "min_pts": 5000,   "benefit": "Sabiduría colectiva (60 aportes/día)", "limit": 60},
    {"name": "🔮 Oráculo",       "min_pts": 15000,  "benefit": "Infinidad y control total (∞)", "limit": 99999}
]

def _t(key: str, lang: str, **kwargs) -> str:
    """Helper de traducción segura a HTML"""
    raw_text = T.get(lang, T.get("es", {})).get(key, f"Missing: {key}")
    
    # Conversión del viejo MarkdownV2 de locales.json a HTML seguro
    raw_text = raw_text.replace("\\-", "-").replace("\\.", ".").replace("\\!", "!")
    # Transformar *texto* en <b>texto</b> (Implementación rápida de regex)
    import re
    raw_text = re.sub(r'\*(.*?)\*', r'<b>\1</b>', raw_text)
    
    if kwargs:
        return raw_text.format(**kwargs)
    return raw_text

def get_menu(lang: str) -> ReplyKeyboardMarkup:
    """Conserva tu interfaz visual con el selector de idioma"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=T.get(lang, T["es"]).get("btn_contribute", "🔥 Contribuir"))],
            [
                KeyboardButton(text=T.get(lang, T["es"]).get("btn_status", "📊 Mi Estado")), 
                KeyboardButton(text=T.get(lang, T["es"]).get("btn_memory", "🧠 Mi Legado"))
            ],
            [KeyboardButton(text=T.get(lang, T["es"]).get("btn_language", "🌐 Idioma"))]
        ],
        resize_keyboard=True
    )

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    user = await hydrate_user(str(message.from_user.id))
    # Recuperamos el reto actual sin bloquear
    reto_bytes = await greenfield.get_object("aisynergix/data/challenges/current.txt")
    reto_txt = reto_bytes.decode('utf-8') if reto_bytes else "Buscando la siguiente frontera..."
    
    txt = _t("welcome", user.language, name=message.from_user.first_name, challenge=reto_txt)
    await message.answer(txt, reply_markup=get_menu(user.language))

@dp.message(lambda msg: msg.text in [t.get("btn_language", "") for t in T.values()])
async def cmd_language_menu(message: types.Message):
    user = await hydrate_user(str(message.from_user.id))
    builder = InlineKeyboardBuilder()
    if "es" in T: builder.button(text="🇪🇸 Español", callback_data="lang_es")
    if "en" in T: builder.button(text="🇬🇧 English", callback_data="lang_en")
    if "zh_cn" in T: builder.button(text="🇨🇳 简体中文", callback_data="lang_zh_cn")
    if "zh" in T: builder.button(text="🇹🇼 繁體中文", callback_data="lang_zh")
    builder.adjust(2)
    
    await message.answer(_t("choose_lang", user.language), reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("lang_"))
async def process_language(callback: types.CallbackQuery):
    uid = str(callback.from_user.id)
    new_lang = callback.data.replace("lang_", "")
    user = await hydrate_user(uid)
    
    user.language = new_lang
    asyncio.create_task(greenfield.update_user_metadata(uid, {"lang": new_lang}))
    
    await callback.message.answer(_t("lang_updated", new_lang), reply_markup=get_menu(new_lang))
    await callback.answer()

@dp.message(F.text)
async def handle_message(message: types.Message):
    uid = str(message.from_user.id)
    user = await hydrate_user(uid)
    lang = user.language
    state = await get_state(user)

    # 1. Menú: Contribuir
    if message.text in [t.get("btn_contribute", "") for t in T.values()]:
        await set_state(user, "AWAITING_CONTRIB")
        await message.answer(_t("await_contrib", lang))
        return

    # 2. Menú: Estado (Integra los 6 rangos)
    if message.text in [t.get("btn_status", "") for t in T.values()]:
        next_rank_info = "🔮 Cúspide Alcanzada"
        for i, rank in enumerate(RANK_TABLE):
            if user.points < rank["min_pts"]:
                next_rank_info = f"Faltan {rank['min_pts'] - user.points} pts para {rank['name']}"
                break
                
        # Obtenemos challenge sin bloquear
        reto_b = await greenfield.get_object("aisynergix/data/challenges/current.txt")
        
        txt = _t("status_msg", lang, 
                 total="Sincronizado", 
                 challenge=reto_b.decode('utf-8')[:30]+"..." if reto_b else "Activo",
                 name=message.from_user.first_name,
                 pts=user.points, 
                 contribs="Ver On-Chain", 
                 impact="Activo", 
                 rank=user.rank, 
                 benefit=next((r["benefit"] for r in reversed(RANK_TABLE) if user.points >= r["min_pts"]), ""),
                 progress_bar="▓▓▓░░░", 
                 next_rank=next_rank_info)
        await message.answer(txt)
        return

    # 3. Flujo de FSM: Recibiendo Aporte Técnico
    if state == "AWAITING_CONTRIB":
        if len(message.text) < 20:
            await message.answer(_t("contrib_short", lang))
            return
            
        # Velocidad Letal: Evaluación de Juez local (0.5B)
        res = await ask_judge(message.text)
        if res.get("score", 0) > 7.0:
            pts_earned = int(res["score"] * 10)
            user.points += pts_earned
            await set_state(user, "IDLE")
            
            # Recalcular Rango
            for rank in reversed(RANK_TABLE):
                if user.points >= rank["min_pts"]:
                    user.rank = rank["name"]
                    break
                    
            # Lazy Updates a Greenfield (No bloquean el bot)
            tags = {"score": res["score"], "category": "code"}
            asyncio.create_task(greenfield.upload_aporte(uid, message.text, tags))
            asyncio.create_task(greenfield.update_user_metadata(uid, {"points": user.points, "rank": user.rank}))
            
            await message.answer(_t("contrib_ok", lang, pts=pts_earned))
        else:
            await set_state(user, "IDLE")
            await message.answer(_t("contrib_rejected", lang))
        return

    # 4. Flujo Chat RAG (Pregunta Libre)
    if user.daily_quota <= 0 and user.points < 15000: # Oráculos son infinitos
        await message.answer(_t("error_quota", lang, rank=user.rank))
        return

    # Procesamiento protegido por Semáforo ARM64
    async with brain_manager.sem:
        context, authors = await rag_engine.get_context(message.text)
        # Velocidad Letal: Consulta al Pensador local (1.5B)
        respuesta = await ask_thinker(message.text, context, lang)
        
        # Puntos Residuales Automáticos
        for auth_uid in authors:
            if auth_uid != uid: # Evitar granja propia
                await brain_manager.reward_queue.put(auth_uid)
                
        if user.points < 15000:
            user.daily_quota -= 1
            asyncio.create_task(greenfield.update_user_metadata(uid, {"quota": user.daily_quota}))
            
        await message.answer(respuesta)


# === TAREAS EN SEGUNDO PLANO (APScheduler) ===

async def loop_fusion_10m():
    logger.info("🧬 Ejecutando Fusión de Cerebro Asíncrona (10m)...")
    from scripts.fusion_brain import fusion_loop
    await fusion_loop()

async def loop_logs_24h():
    logger.info("📦 Ejecutando respaldo de logs a DCellar...")
    today = datetime.now().strftime('%Y-%m-%d')
    filepath = f"logs/{today}.log"
    if os.path.exists(filepath):
        await greenfield.upload_log(filepath)

async def loop_weekly_challenge():
    logger.info("🏆 Generando Reto Automático...")
    prompt = "Genera un reto de código técnico sobre Web3, IA Descentralizada o Solidity. Que sea un párrafo conciso."
    reto = await ask_thinker(prompt, "No context", "es")
    await greenfield.put_object("aisynergix/data/challenges/current.txt", reto.encode('utf-8'))

async def main():
    logger.info("🚀 Synergix Phantom Node Ignition...")
    
    # Demonios
    scheduler = AsyncIOScheduler()
    scheduler.add_job(loop_fusion_10m, 'interval', minutes=10)
    scheduler.add_job(loop_logs_24h, 'cron', hour=0)
    scheduler.add_job(loop_weekly_challenge, 'cron', day_of_week='mon', hour=0)
    scheduler.start()
    
    # Worker de regalías Web3
    asyncio.create_task(brain_manager.process_residual_rewards())
    
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
