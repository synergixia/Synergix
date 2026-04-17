"""
Bot principal de Synergix (Aiogram V3) con arquitectura Nodo Fantasma.
Implementa menús permanentes, comando secreto 'S', y UX supersónica.
100% stateless: toda la persistencia está en Greenfield.
"""

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from aiogram.utils.markdown import hbold, hitalic, hlink
from dotenv import load_dotenv

from aisynergix.ai.manager import evaluate_contribution, process_user_query
from aisynergix.bot.fsm import ensure_menu_state, get_user_state, set_user_state
from aisynergix.bot.identity import (
    add_points,
    get_daily_remaining,
    hydrate_user,
    increment_daily_contributions,
)
from aisynergix.services.greenfield import get_object, hash_uid, upload_aporte
from config import cfg

# ──────────────────────────────────────────────────────────────────────────────
# CONFIGURACIÓN
# ──────────────────────────────────────────────────────────────────────────────

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../../.env"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("synergix.bot")

TOKEN = cfg.credentials.TELEGRAM_TOKEN
if not TOKEN:
    logger.error("Falta TELEGRAM_TOKEN en .env")
    sys.exit(1)

bot = Bot(token=TOKEN, parse_mode="HTML")
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

# ──────────────────────────────────────────────────────────────────────────────
# TRADUCCIONES Y MENÚS PERMANENTES (spec oficial, mantenidos del bot actual)
# ──────────────────────────────────────────────────────────────────────────────

TEXTS = {
    "es": {
        "welcome": "¡Bienvenido, {name}! 🌟\n\nSoy Synergix, inteligencia colectiva descentralizada.\nTu conocimiento se guarda para siempre en BNB Greenfield y evoluciona nuestra IA. 🔗\n\n🏆 Challenge de la semana:\n{challenge}\n\nNo usas una app. Construyes una memoria comunitaria viva. 🚀",
        "welcome_back": "¡Hola de nuevo, {name}! 🔥\n\n¿Qué conocimiento anclaremos hoy? 🚀",
        "btn_contribute": "🔥 Contribuir",
        "btn_chat": "💭 Chat Libre",
        "btn_status": "📊 Ver estado",
        "btn_language": "🌐 Idioma",
        "btn_memory": "🧠 Mi memoria",
        "select_lang": "🌐 Elige tu idioma:",
        "lang_set": "✅ Idioma configurado a Español 🇪🇸",
        "await_contrib": "🎯 Modo aporte activado!\n\nEscribe tu idea o envía una nota de voz. Quedará grabado en la red para siempre. 💡\n\nMínimo 20 caracteres.",
        "contrib_ok": "¡Gracias, {name}! 🌟\n\nTu aporte forma parte de la Memoria Inmortal Synergix 🔗\nRuta: {path}\n\nTu conocimiento vive para siempre y fortalece la red. 🔥",
        "contrib_elite": "\n\n⭐ ¡Aporte de élite! Score {score}/10 → +{points} puntos",
        "contrib_bonus": "\n\n🏆 ¡Relacionado al Challenge semanal! +5 puntos extra.",
        "contrib_fail": "⚠️ Hubo un problema al guardar tu aporte. Inténtalo de nuevo.",
        "contrib_short": "🤔 Muy corto ({chars} chars). Mínimo 20 caracteres. 🔥",
        "contrib_rejected": "🤔 Aporte con poca profundidad (score: {score}/10).\n\n💡 {reason}\n\nAmplía tu idea. 🔥",
        "no_memory": "🧠 Sin aportes aún. ¡Contribuye para dejar tu huella! 🔥",
        "memory_title": "🧠 Tu legado en la Memoria Inmortal Synergix:\n\n",
        "memory_footer": "\n\n📈 Score: {pts} pts | Contribuciones: {contribs}",
        "error": "⚠️ Problema temporal. Inténtalo de nuevo. 🔄",
        "status_msg": "📊 Synergix Inteligencia Colectiva\n\n📦 Aportes Inmortales: {total}\n🏆 Challenge:\n{challenge}\n\n── Tu Impacto, {name} ──\n📈 Score: {pts} pts\n🔗 Contribuciones: {contribs}\n🔁 Usos de tus aportes: {impact}\n🏅 Rango: {rank}\n💡 Beneficio: {benefit}",
        "rank_1": "🌱 Iniciado",
        "rank_2": "📈 Activo",
        "rank_3": "🧬 Sincronizado",
        "rank_4": "🏗️ Arquitecto",
        "rank_5": "🧠 Mente Colmena",
        "rank_6": "🔮 Oráculo",
        "challenge_text": "Mejor estrategia DeFi 2026",
        "benefit_1": "Envío de aportes básicos a la red",
        "benefit_2": "Acceso a Challenges mensuales 🏆",
        "benefit_3": "Prioridad de procesamiento en el RAG ⚡",
        "benefit_4": "Tus aportes pesan más en el Fusion Brain 🧠",
        "benefit_5": "Puedes validar o rechazar ideas de otros 🗳️",
        "benefit_6": "Influencia máxima sobre la inteligencia colectiva 🌐",
        "received": "¡Recibido! Tu sabiduría está siendo procesada e inmortalizada. 🔗",
        "transcribing": "🎙️ Transcribiendo tu nota de voz...",
        "leaderboard_title": "🏆 LEADERBOARD SYNERGIX 🏆\n\n👥 Usuarios totales: {total_users}\n\n",
        "leaderboard_row": "{rank}. {rank_tag} {points} pts\n",
        "leaderboard_footer": "\n✨ ¡Sigue contribuyendo para subir en el ranking!",
        "chat_welcome": "💭 Modo Chat Libre activado\n\nEscribe tu pregunta y conversaré contigo usando todo el conocimiento colectivo de Synergix. 🧠\n\n(Para volver al menú, usa /menu)",
        "menu_welcome": "🏠 Menú Principal\n\nElige una opción:",
        "daily_limit_reached": "⏳ Has alcanzado tu límite diario de aportes ({used}/{limit}).\n\nVuelve mañana o mejora tu rango para aumentar el límite. ⭐",
    },
    "en": {
        "welcome": "Welcome, {name}! 🌟\n\nI'm Synergix, decentralized collective intelligence.\nYour knowledge is saved forever on BNB Greenfield. 🔗\n\n🏆 Weekly Challenge:\n{challenge}\n\nYou're building a living community memory. 🚀",
        "welcome_back": "Welcome back, {name}! 🔥\n\nWhat knowledge will we anchor today? 🚀",
        "btn_contribute": "🔥 Contribute",
        "btn_chat": "💭 Free Chat",
        "btn_status": "📊 Status",
        "btn_language": "🌐 Language",
        "btn_memory": "🧠 My memory",
        "select_lang": "🌐 Choose your language:",
        "lang_set": "✅ Language set to English 🇬🇧",
        "await_contrib": "🎯 Contribution mode activated!\n\nWrite your idea or send a voice note. 💡\n\nMinimum 20 characters.",
        "contrib_ok": "Thank you, {name}! 🌟\n\nYour contribution is now part of the Immortal Synergix Memory 🔗\nPath: {path}\n\nYour knowledge lives forever. 🔥",
        "contrib_elite": "\n\n⭐ Elite contribution! Score {score}/10 → +{points} points",
        "contrib_bonus": "\n\n🏆 Related to the weekly Challenge! +5 extra points.",
        "contrib_fail": "⚠️ Problem saving your contribution. Please try again.",
        "contrib_short": "🤔 Too short ({chars} chars). Minimum 20 characters. 🔥",
        "contrib_rejected": "🤔 Needs more depth (score: {score}/10).\n\n💡 {reason}\n\nExpand your idea. 🔥",
        "no_memory": "🧠 No contributions yet. Contribute to leave your mark! 🔥",
        "memory_title": "🧠 Your legacy in the Immortal Synergix Memory:\n\n",
        "memory_footer": "\n\n📈 Score: {pts} pts | Contributions: {contribs}",
        "error": "⚠️ Temporary issue. Please try again. 🔄",
        "status_msg": "📊 Synergix Collective Intelligence\n\n📦 Immortal Contributions: {total}\n🏆 Challenge:\n{challenge}\n\n── Your Impact, {name} ──\n📈 Score: {pts} pts\n🔗 Contributions: {contribs}\n🔁 Times used by community: {impact}\n🏅 Rank: {rank}\n💡 Benefit: {benefit}",
        "rank_1": "🌱 Initiate",
        "rank_2": "📈 Active",
        "rank_3": "🧬 Synchronized",
        "rank_4": "🏗️ Architect",
        "rank_5": "🧠 Hive Mind",
        "rank_6": "🔮 Oracle",
        "challenge_text": "Best DeFi Strategy 2026",
        "benefit_1": "Send basic contributions to the network",
        "benefit_2": "Access to monthly Challenges 🏆",
        "benefit_3": "Priority processing in the RAG ⚡",
        "benefit_4": "Your contributions weigh more in the Fusion Brain 🧠",
        "benefit_5": "You can validate or reject others' ideas 🗳️",
        "benefit_6": "Maximum influence over collective intelligence 🌐",
        "received": "Received! Your wisdom is being processed and immortalized. 🔗",
        "transcribing": "🎙️ Transcribing your voice note...",
        "leaderboard_title": "🏆 SYNERGIX LEADERBOARD 🏆\n\n👥 Total users: {total_users}\n\n",
        "leaderboard_row": "{rank}. {rank_tag} {points} pts\n",
        "leaderboard_footer": "\n✨ Keep contributing to climb the ranks!",
        "chat_welcome": "💭 Free Chat mode activated\n\nWrite your question and I'll chat with you using all of Synergix's collective knowledge. 🧠\n\n(Use /menu to return to main menu)",
        "menu_welcome": "🏠 Main Menu\n\nChoose an option:",
        "daily_limit_reached": "⏳ You've reached your daily contribution limit ({used}/{limit}).\n\nCome back tomorrow or improve your rank to increase the limit. ⭐",
    },
    "zh-hans": {
        "welcome": "欢迎，{name}！🌟\n\n我是 Synergix，去中心化集体智慧。\n您的知识永久保存在 BNB Greenfield。🔗\n\n🏆 本周挑战：\n{challenge}\n\n您正在建立活生生的社区记忆。🚀",
        "welcome_back": "欢迎回来，{name}！🔥\n\n今天锚定什么知识？🚀",
        "btn_contribute": "🔥 贡献",
        "btn_chat": "💭 自由聊天",
        "btn_status": "📊 查看状态",
        "btn_language": "🌐 语言",
        "btn_memory": "🧠 我的记忆",
        "select_lang": "🌐 选择语言：",
        "lang_set": "✅ 语言设定为简体中文 🇨🇳",
        "await_contrib": "🎯 贡献模式已启动！\n\n写下想法或发送语音。💡\n\n最少20个字符。",
        "contrib_ok": "谢谢，{name}！🌟\n\n贡献已永久保存 🔗\n路径：{path}\n\n您的知识永远存在。🔥",
        "contrib_elite": "\n\n⭐ 精英贡献！评分 {score}/10 → +{points} 分",
        "contrib_bonus": "\n\n🏆 与每周挑战相关！+5 分。",
        "contrib_fail": "⚠️ 保存失败，请重试。",
        "contrib_short": "🤔 太短（{chars} 字符）。最少20字符。🔥",
        "contrib_rejected": "🤔 需要更多深度（{score}/10）。\n💡 {reason}\n🔥",
        "no_memory": "🧠 尚无贡献。立即贡献！🔥",
        "memory_title": "🧠 Synergix 不朽记忆：\n\n",
        "memory_footer": "\n\n📈 总分：{pts} 分 | 贡献：{contribs}",
        "error": "⚠️ 临时问题，请重试。🔄",
        "status_msg": "📊 Synergix 集体智慧\n\n📦 不朽贡献：{total}\n🏆 挑战：\n{challenge}\n\n── {name} 的影响力 ──\n📈 分数：{pts}\n🔗 贡献：{contribs}\n🔁 被使用次数：{impact}\n🏅 等级：{rank}\n💡 权益：{benefit}",
        "rank_1": "🌱 入门",
        "rank_2": "📈 活跃",
        "rank_3": "🧬 同步者",
        "rank_4": "🏗️ 架构师",
        "rank_5": "🧠 蜂巢思维",
        "rank_6": "🔮 神谕",
        "challenge_text": "2026年最佳DeFi策略",
        "benefit_1": "向网络发送基本贡献",
        "benefit_2": "参与每月挑战 🏆",
        "benefit_3": "RAG处理优先权 ⚡",
        "benefit_4": "您的贡献在融合大脑中权重更高 🧠",
        "benefit_5": "可以验证或拒绝他人的想法 🗳️",
        "benefit_6": "对集体智慧的最大影响力 🌐",
        "received": "已收到！正在处理。🔗",
        "transcribing": "🎙️ 转录中...",
        "leaderboard_title": "🏆 SYNERGIX 排行榜 🏆\n\n👥 总用户数：{total_users}\n\n",
        "leaderboard_row": "{rank}. {rank_tag} {points} 分\n",
        "leaderboard_footer": "\n✨ 继续贡献以提升排名！",
        "chat_welcome": "💭 自由聊天模式已激活\n\n写下您的问题，我将使用 Synergix 的所有集体知识与您聊天。🧠\n\n（使用 /menu 返回主菜单）",
        "menu_welcome": "🏠 主菜单\n\n请选择：",
        "daily_limit_reached": "⏳ 您已达到每日贡献限制（{used}/{limit}）。\n\n明天再来或提升等级以增加限制。⭐",
    },
    "zh-hant": {
        "welcome": "歡迎，{name}！🌟\n\n我是 Synergix，去中心化集體智慧。\n您的知識永久保存在 BNB Greenfield。🔗\n\n🏆 本週挑戰：\n{challenge}\n\n您正在建立活生生的社群記憶。🚀",
        "welcome_back": "歡迎回來，{name}！🔥\n\n今天錨定什麼知識？🚀",
        "btn_contribute": "🔥 貢獻",
        "btn_chat": "💭 自由聊天",
        "btn_status": "📊 查看狀態",
        "btn_language": "🌐 語言",
        "btn_memory": "🧠 我的記憶",
        "select_lang": "🌐 選擇語言：",
        "lang_set": "✅ 語言設定為繁體中文 🇹🇼",
        "await_contrib": "🎯 貢獻模式已啟動！\n\n寫下想法或發送語音。💡\n\n最少20個字元。",
        "contrib_ok": "謝謝，{name}！🌟\n\n貢獻已永久保存 🔗\n路徑：{path}\n\n您的知識永遠存在。🔥",
        "contrib_elite": "\n\n⭐ 精英貢獻！評分 {score}/10 → +{points} 分",
        "contrib_bonus": "\n\n🏆 與每週挑戰相關！+5 分。",
        "contrib_fail": "⚠️ 儲存失敗，請重試。",
        "contrib_short": "🤔 太短（{chars} 字元）。最少20字元。🔥",
        "contrib_rejected": "🤔 需要更多深度（{score}/10）。\n💡 {reason}\n🔥",
        "no_memory": "🧠 尚無貢獻。立即貢獻！🔥",
        "memory_title": "🧠 Synergix 不朽記憶：\n\n",
        "memory_footer": "\n\n📈 總分：{pts} 分 | 貢獻：{contribs}",
        "error": "⚠️ 暫時問題，請重試。🔄",
        "status_msg": "📊 Synergix 集體智慧\n\n📦 不朽貢獻：{total}\n🏆 挑戰：\n{challenge}\n\n── {name} 的影響力 ──\n📈 分數：{pts}\n🔗 貢獻：{contribs}\n🔁 被使用次數：{impact}\n🏅 等級：{rank}\n💡 權益：{benefit}",
        "rank_1": "🌱 入門",
        "rank_2": "📈 活躍",
        "rank_3": "🧬 同步者",
        "rank_4": "🏗️ 架構師",
        "rank_5": "🧠 蜂巢思維",
        "rank_6": "🔮 神諭",
        "challenge_text": "2026年最佳DeFi策略",
        "benefit_1": "向網路發送基本貢獻",
        "benefit_2": "參與每月挑戰 🏆",
        "benefit_3": "RAG處理優先權 ⚡",
        "benefit_4": "您的貢獻在融合大腦中權重更高 🧠",
        "benefit_5": "可以驗證或拒絕他人的想法 🗳️",
        "benefit_6": "對集體智慧的最大影響力 🌐",
        "received": "已收到！正在處理。🔗",
        "transcribing": "🎙️ 轉錄中...",
        "leaderboard_title": "🏆 SYNERGIX 排行榜 🏆\n\n👥 總用戶數：{total_users}\n\n",
        "leaderboard_row": "{rank}. {rank_tag} {points} 分\n",
        "leaderboard_footer": "\n✨ 繼續貢獻以提升排名！",
        "chat_welcome": "💭 自由聊天模式已激活\n\n寫下您的問題，我將使用 Synergix 的所有集體知識與您聊天。🧠\n\n（使用 /menu 返回主選單）",
        "menu_welcome": "🏠 主選單\n\n請選擇：",
        "daily_limit_reached": "⏳ 您已達到每日貢獻限制（{used}/{limit}）。\n\n明天再來或提升等級以增加限制。⭐",
    },
}


def get_text(user_lang: str, key: str, **kwargs) -> str:
    """Obtiene texto traducido, con fallback a español."""
    lang_dict = TEXTS.get(user_lang, TEXTS["es"])
    text = lang_dict.get(key, TEXTS["es"].get(key, key))
    return text.format(**kwargs) if kwargs else text


def build_menu_keyboard(user_lang: str) -> ReplyKeyboardMarkup:
    """Construye el menú permanente (ReplyKeyboardMarkup)."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=get_text(user_lang, "btn_contribute")),
                KeyboardButton(text=get_text(user_lang, "btn_chat")),
            ],
            [
                KeyboardButton(text=get_text(user_lang, "btn_status")),
                KeyboardButton(text=get_text(user_lang, "btn_memory")),
            ],
            [
                KeyboardButton(text=get_text(user_lang, "btn_language")),
            ],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


# ──────────────────────────────────────────────────────────────────────────────
# HANDLERS PRINCIPALES
# ──────────────────────────────────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message):
    """Handler para /start - Bienvenida y menú principal."""
    user = message.from_user
    telegram_uid = user.id
    # Hidratar usuario (crea perfil si no existe)
    user_info = await hydrate_user(telegram_uid, language_hint=user.language_code)
    user_lang = user_info["language"]
    # Establecer estado menu_principal
    await set_user_state(telegram_uid, "menu_principal", sync_now=False)
    # Determinar si es primera vez (por last_seen_ts)
    first_time = user_info["last_seen_ts"] < (time.time() - 3600)  # >1 hora sin actividad
    if first_time:
        welcome_text = get_text(
            user_lang,
            "welcome",
            name=user.first_name,
            challenge=get_text(user_lang, "challenge_text"),
        )
    else:
        welcome_text = get_text(user_lang, "welcome_back", name=user.first_name)
    # Enviar mensaje con menú
    await message.answer(
        welcome_text,
        reply_markup=build_menu_keyboard(user_lang),
    )
    logger.info("👋 Usuario %d (%s) inició sesión", telegram_uid, user.first_name)


@router.message(F.text == "🔥 Contribuir")
@router.message(F.text == "🔥 Contribute")
@router.message(F.text == "🔥 贡献")
async def btn_contribute(message: Message):
    """Handler para botón 'Contribuir'."""
    telegram_uid = message.from_user.id
    user_info = await hydrate_user(telegram_uid)
    user_lang = user_info["language"]
    # Verificar límite diario
    remaining = await get_daily_remaining(telegram_uid)
    if remaining <= 0:
        daily_used = user_info["daily_aportes_count"]
        daily_limit = user_info["daily_limit"]
        await message.answer(
            get_text(
                user_lang,
                "daily_limit_reached",
                used=daily_used,
                limit=daily_limit,
            ),
            reply_markup=build_menu_keyboard(user_lang),
        )
        return
    # Cambiar estado a esperando_aporte
    await set_user_state(telegram_uid, "esperando_aporte", sync_now=False)
    await message.answer(
        get_text(user_lang, "await_contrib"),
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(F.text == "💭 Chat Libre")
@router.message(F.text == "💭 Free Chat")
@router.message(F.text == "💭 自由聊天")
async def btn_chat(message: Message):
    """Handler para botón 'Chat Libre'."""
    telegram_uid = message.from_user.id
    user_info = await hydrate_user(telegram_uid)
    user_lang = user_info["language"]
    # Cambiar estado a chat_libre
    await set_user_state(telegram_uid, "chat_libre", sync_now=False)
    await message.answer(
        get_text(user_lang, "chat_welcome"),
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="/menu")]],
            resize_keyboard=True,
            is_persistent=True,
        ),
    )


@router.message(F.text == "📊 Ver estado")
@router.message(F.text == "📊 Status")
@router.message(F.text == "📊 查看狀態")
async def btn_status(message: Message):
    """Handler para botón 'Ver estado'."""
    telegram_uid = message.from_user.id
    user_info = await hydrate_user(telegram_uid)
    user_lang = user_info["language"]
    # TODO: Obtener estadísticas globales (total de aportes)
    total_contributions = 0  # Placeholder - se obtendrá de Greenfield
    benefit_key = f"benefit_{user_info['rank_level'] + 1}"
    status_text = get_text(
        user_lang,
        "status_msg",
        name=message.from_user.first_name,
        total=total_contributions,
        challenge=get_text(user_lang, "challenge_text"),
        pts=user_info["points"],
        contribs=user_info["daily_aportes_count"],  # TODO: cambiar por contribuciones totales
        impact=user_info["total_uses_count"],
        rank=user_info["rank_tag"],
        benefit=get_text(user_lang, benefit_key),
    )
    await message.answer(
        status_text,
        reply_markup=build_menu_keyboard(user_lang),
    )
    await ensure_menu_state(telegram_uid)


@router.message(F.text == "🌐 Idioma")
@router.message(F.text == "🌐 Language")
@router.message(F.text == "🌐 语言")
async def btn_language(message: Message):
    """Handler para botón 'Idioma' - muestra selector de idioma."""
    telegram_uid = message.from_user.id
    user_info = await hydrate_user(tele
