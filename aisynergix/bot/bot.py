import os
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict

from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from aisynergix.services.greenfield import GreenfieldClient
from aisynergix.services.rag_engine import RAGEngine
from aisynergix.bot.identity import IdentityHydrator, UserContext
from aisynergix.ai.local_ia import LocalIA
from aisynergix.ai.manager import AIManager
from aisynergix.bot.fsm import SynergixStates

# Configuración de Logs
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(f"logs/{datetime.now().strftime('%Y-%m-%d')}.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("Synergix.Bot")

# --- Localización (I18N) ---
STRINGS = {
    "es": {
        "welcome": "<b>Soberanía Synergix Activa.</b>\nNodo Fantasma: {uid}\nRango: {rango}\nPuntos: {puntos}",
        "chat_mode": "<i>Modo Chat Libre activado. La IA te escucha...</i>",
        "contribution_ok": "✅ Aporte validado por el Juez (+{puntos} pts). Conocimiento inyectado.",
        "contribution_fail": "❌ El Juez ha rechazado el aporte por baja calidad.",
        "quota_exceeded": "⚠️ Cuota diaria agotada. Sube de rango para expandir tus límites.",
        "challenge": "🏆 <b>Reto Semanal:</b>\n{challenge}"
    },
    "en": {
        "welcome": "<b>Synergix Sovereignty Active.</b>\nGhost Node: {uid}\nRank: {rank}\nPoints: {points}",
        "chat_mode": "<i>Free Chat mode active. AI is listening...</i>",
        "contribution_ok": "✅ Contribution validated by the Judge (+{points} pts). Knowledge injected.",
        "contribution_fail": "❌ The Judge has rejected the contribution due to low quality.",
        "quota_exceeded": "⚠️ Daily quota reached. Increase your rank to expand limits.",
        "challenge": "🏆 <b>Weekly Challenge:</b>\n{challenge}"
    },
    "zh-hans": {
        "welcome": "<b>Synergix 主权已激活。</b>\n幽灵节点: {uid}\n等级: {rank}\n积分: {points}",
        "chat_mode": "<i>自由聊天模式已开启。AI 正在倾听...</i>",
        "contribution_ok": "✅ 贡献已通过法官验证 (+{points} 分)。知识已注入。",
        "contribution_fail": "❌ 法官因质量低而拒绝了该贡献。",
        "quota_exceeded": "⚠️ 每日配额已用完。升级等级以扩大限制。",
        "challenge": "🏆 <b>每周挑战:</b>\n{challenge}"
    },
    "zh-hant": {
        "welcome": "<b>Synergix 主權已激活。</b>\n幽靈節點: {uid}\n等級: {rank}\n積分: {points}",
        "chat_mode": "<i>自由聊天模式已開啟。AI 正在傾聽...</i>",
        "contribution_ok": "✅ 貢獻已通過法官驗證 (+{points} 分)。知識已注入。",
        "contribution_fail": "❌ 法官因質量低而拒絕了該貢獻。",
        "quota_exceeded": "⚠️ 每日配額已用完。升級等級以擴大限制。",
        "challenge": "🏆 <b>每周挑戰:</b>\n{challenge}"
    }
}

def get_lang(user_lang: str) -> str:
    if not user_lang: return "es"
    if user_lang.startswith("en"): return "en"
    if user_lang == "zh-hant": return "zh-hant"
    if user_lang.startswith("zh"): return "zh-hans"
    return "es"

# --- Inicialización ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
GF_ENDPOINT = os.getenv("GF_ENDPOINT")
GF_BUCKET = os.getenv("GF_BUCKET")
GF_KEY = os.getenv("PRIVATE_KEY")

greenfield = GreenfieldClient(GF_ENDPOINT, GF_BUCKET, GF_KEY)
hydrator = IdentityHydrator(greenfield)
rag = RAGEngine()
ai_local = LocalIA(os.getenv("THINKER_URL"), os.getenv("JUDGE_URL"))
manager = AIManager(ai_local, greenfield)

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# --- Handlers ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user = await hydrator.hydrate(message.from_user.id)
    lang = get_lang(message.from_user.language_code)
    text = STRINGS[lang]["welcome"].format(
        uid=user.uid, rango=user.rango, rank=user.rango, 
        puntos=user.puntos, points=user.puntos
    )
    await message.answer(text)

@dp.message(F.text & ~F.text.startswith("/"))
async def handle_message(message: types.Message):
    user = await hydrator.hydrate(message.from_user.id)
    lang = get_lang(message.from_user.language_code)
    
    # RAG Search
    context, author_uids = rag.get_context(message.text)
    
    # Response (Sin mensaje de "procesando")
    response = await manager.process_chat(message.text, context, author_uids)
    await message.answer(response)

# --- Background Tasks ---
async def loop_fusion_10m():
    """Llama a la lógica de fusión cerebral cada 10 minutos."""
    from scripts.fusion_brain import fuse_now
    await fuse_now(greenfield, rag)

async def loop_logs_24h():
    """Sube logs a Greenfield cada 24 horas."""
    yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    log_file = f"logs/{yesterday}.log"
    if os.path.exists(log_file):
        await greenfield.upload_log(log_file)
        logger.info(f"Logs de {yesterday} subidos a la Web3.")

async def loop_weekly_challenge():
    """Genera un reto técnico los lunes."""
    if datetime.now().weekday() == 0: # Lunes
        prompt = "Genera un reto técnico de programación Web3/IA complejo para la comunidad."
        challenge = await ai_local.ask_thinker(prompt, "Eres el Arquitecto de Synergix.")
        await greenfield.put_object("aisynergix/desafios/weekly.txt", challenge.encode())
        logger.info("Nuevo Reto Semanal generado.")

async def main():
    # Sincronización Inicial
    from scripts.sync_brain import sync_now
    await sync_now(greenfield, rag)
    
    # Scheduler
    scheduler = AsyncIOScheduler()
    scheduler.add_job(loop_fusion_10m, 'interval', minutes=10)
    scheduler.add_job(loop_logs_24h, 'cron', hour=0, minute=5)
    scheduler.add_job(loop_weekly_challenge, 'cron', day_of_week='mon', hour=0, minute=0)
    scheduler.start()
    
    # Iniciar Bot
    logger.info("Synergix Ghost Node iniciado con éxito.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    os.makedirs("logs", exist_ok=True)
    asyncio.run(main())
