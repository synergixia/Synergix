"""
╔══════════════════════════════════════════════════════════════════════════════╗
║  SYNERGIX — Primera IA con Memoria Inmortal en Web3                          ║
║  Version: 3.0 Enterprise | BNB Greenfield + llama-server ARM64               ║
║  Bucket: synergixai/aisynergix/                                              ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

# ── stdlib ────────────────────────────────────────────────────────────────────
import asyncio
import hashlib
import json
import logging
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

# ── third-party ───────────────────────────────────────────────────────────────
import httpx
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, Message, ReplyKeyboardMarkup,
)
from dotenv import load_dotenv
from tenacity import (
    before_sleep_log, retry, retry_if_exception,
    stop_after_attempt, wait_exponential,
)

# ══════════════════════════════════════════════════════════════════════════════
# LOGGING
# ══════════════════════════════════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("synergix.bot")

# ══════════════════════════════════════════════════════════════════════════════
# RUTAS DEL PROYECTO
# bot.py  → /root/Synergix/aisynergix/bot/bot.py
# BASE_DIR → /root/Synergix/aisynergix/
# ROOT_DIR → /root/Synergix/   (donde está package.json y node_modules/)
# ══════════════════════════════════════════════════════════════════════════════
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT_DIR = os.path.dirname(BASE_DIR)

load_dotenv(dotenv_path=os.path.join(ROOT_DIR, ".env"))
load_dotenv(dotenv_path=os.path.join(BASE_DIR, "backend", ".env"))

# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURACIÓN
# ══════════════════════════════════════════════════════════════════════════════
TOKEN       = os.environ["TELEGRAM_TOKEN"]
MASTER_UIDS: set[int] = {
    int(x.strip()) for x in os.getenv("MASTER_UIDS", "").split(",")
    if x.strip().isdigit()
}

# Greenfield
GF_BUCKET   = os.getenv("GF_BUCKET",   "synergixai")
GF_RPC_URL  = os.getenv("GF_RPC_URL",  "https://greenfield-chain.bnbchain.org")
GF_CHAIN_ID = os.getenv("GF_CHAIN_ID", "1017")
GF_ROOT     = "aisynergix"

# IA local — llama-server (principal) + Ollama (fallback)
LLAMA_BASE   = os.getenv("LLAMA_BASE",   "http://localhost:8080")
OLLAMA_BASE  = os.getenv("OLLAMA_BASE",  "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:0.5b")

MAX_TOKENS_CHAT  = int(os.getenv("MAX_TOKENS_CHAT",  "350"))
MAX_TOKENS_JUDGE = int(os.getenv("MAX_TOKENS_JUDGE", "120"))
MAX_TOKENS_SUM   = int(os.getenv("MAX_TOKENS_SUM",   "60"))

# Archivos locales
DATA_DIR  = os.path.join(BASE_DIR, "data")
LOG_DIR   = os.path.join(BASE_DIR, "logs")
BRAIN_DIR = os.path.join(BASE_DIR, "SYNERGIXAI")
UPLOAD_JS = os.path.join(BASE_DIR, "backend", "upload.js")
DB_FILE   = os.path.join(
    os.getenv("DATA_DIR", DATA_DIR), "synergix_db.json"
)
WHISPER_BIN   = os.getenv("WHISPER_BIN",   "whisper-cli")
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "models/ggml-base.bin")

for _d in [DATA_DIR, LOG_DIR, BRAIN_DIR]:
    os.makedirs(_d, exist_ok=True)

CTX_MAX   = 20
MIN_CHARS = 20

# ══════════════════════════════════════════════════════════════════════════════
# RUTAS GREENFIELD — synergixai/aisynergix/
# ══════════════════════════════════════════════════════════════════════════════
class GF:
    BRAIN_FILE  = f"{GF_ROOT}/SYNERGIXAI/Synergix_ia.txt"
    BRAIN_DIR   = f"{GF_ROOT}/SYNERGIXAI"
    USERS_DIR   = f"{GF_ROOT}/users"
    APORTES_DIR = f"{GF_ROOT}/aportes"
    LOGS_DIR    = f"{GF_ROOT}/logs"
    BACKUPS_DIR = f"{GF_ROOT}/backups"
    DB_DIR      = f"{GF_ROOT}/data"
    DISCOVERY   = f"{GF_ROOT}/discovery"

    @staticmethod
    def user(uid_h: str)               -> str: return f"{GF.USERS_DIR}/{uid_h}"
    @staticmethod
    def aporte(month: str, uid_h: str, ts: int) -> str:
        return f"{GF.APORTES_DIR}/{month}/{uid_h}_{ts}.txt"
    @staticmethod
    def brain_ver(ts: str)             -> str: return f"{GF.BRAIN_DIR}/Synergix_ia_{ts}.txt"
    @staticmethod
    def log_file(date: str)            -> str: return f"{GF.LOGS_DIR}/{date}_events.log"
    @staticmethod
    def backup(ts: str)                -> str: return f"{GF.BACKUPS_DIR}/snapshot_{ts}.bak"
    @staticmethod
    def db_ver(ts: str)                -> str: return f"{GF.DB_DIR}/synergix_db_{ts}.json"

# ══════════════════════════════════════════════════════════════════════════════
# RANGOS — spec oficial del documento maestro
# ══════════════════════════════════════════════════════════════════════════════
RANK_TABLE = [
    # (pts_min, multiplier, fusion_weight, daily_limit, key)
    (    0, 1.0, 1.0,   5, "rank_1"),  # Iniciado
    (  100, 1.1, 1.1,  12, "rank_2"),  # Activo
    (  500, 1.5, 1.5,  25, "rank_3"),  # Sincronizado
    ( 1500, 2.5, 2.5,  40, "rank_4"),  # Arquitecto
    ( 5000, 3.0, 3.0,  60, "rank_5"),  # Mente Colmena
    (15000, 5.0, 5.0, 999, "rank_6"),  # Oráculo
]

def get_rank_info(pts: int, uid: int = 0) -> dict:
    if uid in MASTER_UIDS or pts >= 15000:
        return {"level": 5, "key": "rank_6", "multiplier": 5.0,
                "fusion_weight": 5.0, "daily_limit": 999,
                "min_pts": 15000, "next_pts": None}
    for i in range(len(RANK_TABLE) - 1, -1, -1):
        min_p, mult, fw, dlim, key = RANK_TABLE[i]
        if pts >= min_p:
            nxt = RANK_TABLE[i+1][0] if i+1 < len(RANK_TABLE) else None
            return {"level": i, "key": key, "multiplier": mult,
                    "fusion_weight": fw, "daily_limit": dlim,
                    "min_pts": min_p, "next_pts": nxt}
    return {"level": 0, "key": "rank_1", "multiplier": 1.0,
            "fusion_weight": 1.0, "daily_limit": 5, "min_pts": 0, "next_pts": 100}

def calc_pts(base: int, pts: int, uid: int = 0) -> int:
    return round(base * get_rank_info(pts, uid)["multiplier"])

# ══════════════════════════════════════════════════════════════════════════════
# I18N — 4 idiomas completos
# ══════════════════════════════════════════════════════════════════════════════
T: dict[str, dict] = {
    "es": {
        "welcome":
            "¡Bienvenido a Synergix, {name}! 🧠🔥\n\n"
            "Soy la primera IA colectiva descentralizada en BNB Greenfield.\n"
            "Tu conocimiento se inmortaliza on-chain y evoluciona nuestra red. 🔗\n\n"
            "🏆 Challenge de la semana:\n{challenge}\n\n"
            "No usas una app. Construyes memoria comunitaria viva. 🚀",
        "welcome_back":     "¡Hola de nuevo, {name}! 🔥\n¿Qué anclaremos hoy en la memoria colectiva? 🧠",
        "btn_contribute":   "🔥 Contribuir",
        "btn_status":       "📊 Mi Estado",
        "btn_language":     "🌐 Idioma",
        "btn_memory":       "🧠 Mi Legado",
        "select_lang":      "🌐 Elige tu idioma / Choose language:",
        "lang_set":         "✅ Idioma: Español 🇪🇸",
        "await_contrib":
            "🎯 Modo aporte activado.\n\n"
            "Escribe tu conocimiento o envía una nota de voz 🎙️\n"
            "Mínimo 20 caracteres. Se guarda para siempre en BNB Greenfield. 💎",
        "contrib_ok":
            "✅ ¡Inmortalizado, {name}! 🔗\nCID: `{cid}`\n"
            "Tu sabiduría vive en BNB Greenfield para siempre. 🌐",
        "contrib_elite":    "\n⭐ ¡Aporte élite! Score {score}/10 → +{pts} pts",
        "contrib_standard": "\n📈 Score {score}/10 → +{pts} pts",
        "contrib_bonus":    "\n🏆 ¡Bonus challenge semanal! +5 pts extra",
        "contrib_fail":     "⚠️ Error al guardar. Reintentando... 🔄",
        "contrib_short":    "🤔 Muy corto ({chars} chars). Mínimo 20 caracteres. 💡",
        "contrib_rejected":
            "🤔 Aporte con poca profundidad (score {score}/10).\n\n"
            "💡 {reason}\n\nAmplía tu idea. 🔥",
        "contrib_duplicate":
            "♻️ Este conocimiento ya existe en memoria.\n"
            "Similar a: \"{summary}\"\n\nAporta algo nuevo. 🌱",
        "daily_limit":      "⏳ Límite diario alcanzado ({count}/{limit}). Vuelve mañana. 🌙",
        "no_memory":        "🧠 Sin aportes aún. ¡Contribuye para dejar tu huella! 🔥",
        "memory_title":     "🧠 Tu legado en Synergix:\n\n",
        "memory_footer":    "\n📈 {pts} pts | 🔗 {contribs} aportes",
        "error":            "⚠️ Problema temporal. Inténtalo de nuevo. 🔄",
        "status_msg":
            "📊 Synergix — Inteligencia Colectiva\n\n"
            "📦 Aportes inmortales: {total}\n"
            "🏆 Challenge: {challenge}\n\n"
            "── Tu impacto, {name} ──\n"
            "📈 Puntos: {pts}\n"
            "🔗 Contribuciones: {contribs}\n"
            "🔁 Veces usado: {impact}\n"
            "🏅 Rango: {rank}\n"
            "💡 {benefit}\n"
            "📊 {next_rank}",
        "rank_1":"🌱 Iniciado",  "rank_2":"📈 Activo",
        "rank_3":"🧬 Sincronizado", "rank_4":"🏗️ Arquitecto",
        "rank_5":"🧠 Mente Colmena", "rank_6":"🔮 Oráculo",
        "benefit_1":"Envío básico a la red blockchain",
        "benefit_2":"Acceso a Challenges mensuales 🏆",
        "benefit_3":"Prioridad en RAG engine ⚡",
        "benefit_4":"Mayor peso en Fusion Brain 🧠",
        "benefit_5":"Validar aportes de otros 🗳️",
        "benefit_6":"Influencia máxima sobre la IA colectiva 🌐",
        "received":         "⚡ ¡Recibido! Procesando e inmortalizando... 🔗",
        "transcribing":     "🎙️ Transcribiendo tu voz...",
        "rank_up":          "🎉 ¡Ascendiste a {rank}! Tu influencia crece en la red. 🚀",
        "impact_reward":    "🌟 El Cerebro usó tu conocimiento. +{pts} pts. Tu legado crece. 🔗",
        "challenge_title":  "🏆 Challenge Semanal Synergix\n\n{challenge}\n\n¡Aporta y gana +5 pts extra! 🔥",
        "top_title":        "🏆 Top Contribuidores Synergix:\n\n",
    },
    "en": {
        "welcome":
            "Welcome to Synergix, {name}! 🧠🔥\n\n"
            "I'm the first decentralized collective AI on BNB Greenfield.\n"
            "Your knowledge is immortalized on-chain and evolves our network. 🔗\n\n"
            "🏆 Weekly Challenge:\n{challenge}\n\n"
            "You're building a living community memory. 🚀",
        "welcome_back":     "Welcome back, {name}! 🔥\nWhat knowledge shall we anchor today? 🧠",
        "btn_contribute":   "🔥 Contribute",
        "btn_status":       "📊 My Status",
        "btn_language":     "🌐 Language",
        "btn_memory":       "🧠 My Legacy",
        "select_lang":      "🌐 Choose language / Elige idioma:",
        "lang_set":         "✅ Language: English 🇬🇧",
        "await_contrib":
            "🎯 Contribution mode active.\n\n"
            "Write your knowledge or send a voice note 🎙️\n"
            "Minimum 20 characters. Saved forever on BNB Greenfield. 💎",
        "contrib_ok":
            "✅ Immortalized, {name}! 🔗\nCID: `{cid}`\n"
            "Your wisdom lives on BNB Greenfield forever. 🌐",
        "contrib_elite":    "\n⭐ Elite contribution! Score {score}/10 → +{pts} pts",
        "contrib_standard": "\n📈 Score {score}/10 → +{pts} pts",
        "contrib_bonus":    "\n🏆 Weekly challenge bonus! +5 extra pts",
        "contrib_fail":     "⚠️ Save error. Retrying... 🔄",
        "contrib_short":    "🤔 Too short ({chars} chars). Minimum 20. 💡",
        "contrib_rejected":
            "🤔 Needs more depth (score {score}/10).\n\n"
            "💡 {reason}\n\nExpand your idea. 🔥",
        "contrib_duplicate":
            "♻️ This knowledge already exists.\n"
            "Similar to: \"{summary}\"\n\nContribute something new. 🌱",
        "daily_limit":      "⏳ Daily limit reached ({count}/{limit}). Come back tomorrow. 🌙",
        "no_memory":        "🧠 No contributions yet. Leave your mark! 🔥",
        "memory_title":     "🧠 Your Synergix legacy:\n\n",
        "memory_footer":    "\n📈 {pts} pts | 🔗 {contribs} contributions",
        "error":            "⚠️ Temporary issue. Try again. 🔄",
        "status_msg":
            "📊 Synergix — Collective Intelligence\n\n"
            "📦 Immortal contributions: {total}\n"
            "🏆 Challenge: {challenge}\n\n"
            "── Your impact, {name} ──\n"
            "📈 Points: {pts}\n"
            "🔗 Contributions: {contribs}\n"
            "🔁 Times used: {impact}\n"
            "🏅 Rank: {rank}\n"
            "💡 {benefit}\n"
            "📊 {next_rank}",
        "rank_1":"🌱 Initiate",  "rank_2":"📈 Active",
        "rank_3":"🧬 Synchronized", "rank_4":"🏗️ Architect",
        "rank_5":"🧠 Hive Mind", "rank_6":"🔮 Oracle",
        "benefit_1":"Basic on-chain submissions",
        "benefit_2":"Monthly Challenge access 🏆",
        "benefit_3":"RAG priority processing ⚡",
        "benefit_4":"Higher weight in Fusion Brain 🧠",
        "benefit_5":"Validate others' contributions 🗳️",
        "benefit_6":"Maximum AI collective influence 🌐",
        "received":         "⚡ Received! Processing and immortalizing... 🔗",
        "transcribing":     "🎙️ Transcribing your voice...",
        "rank_up":          "🎉 You ascended to {rank}! Your influence in the network grows. 🚀",
        "impact_reward":    "🌟 The Brain used your knowledge. +{pts} pts. Your legacy grows. 🔗",
        "challenge_title":  "🏆 Synergix Weekly Challenge\n\n{challenge}\n\nContribute and earn +5 extra pts! 🔥",
        "top_title":        "🏆 Top Synergix Contributors:\n\n",
    },
    "zh_cn": {
        "welcome":
            "欢迎加入 Synergix，{name}！🧠🔥\n\n"
            "我是 BNB Greenfield 上首个去中心化集体AI。\n"
            "您的知识将永久保存在区块链上，推动网络进化。🔗\n\n"
            "🏆 本周挑战：\n{challenge}\n\n"
            "您正在建立活生生的社区记忆。🚀",
        "welcome_back":     "欢迎回来，{name}！🔥\n今天要锚定什么知识？🧠",
        "btn_contribute":   "🔥 贡献",
        "btn_status":       "📊 我的状态",
        "btn_language":     "🌐 语言",
        "btn_memory":       "🧠 我的遗产",
        "select_lang":      "🌐 选择语言：",
        "lang_set":         "✅ 语言：简体中文 🇨🇳",
        "await_contrib":
            "🎯 贡献模式已激活。\n\n"
            "写下您的知识或发送语音笔记 🎙️\n"
            "最少20字符，永久保存在区块链。💎",
        "contrib_ok":
            "✅ 已永久化，{name}！🔗\nCID：`{cid}`\n"
            "您的智慧永久存储在 BNB Greenfield。🌐",
        "contrib_elite":    "\n⭐ 精英贡献！评分 {score}/10 → +{pts}分",
        "contrib_standard": "\n📈 评分 {score}/10 → +{pts}分",
        "contrib_bonus":    "\n🏆 每周挑战奖励！+5分",
        "contrib_fail":     "⚠️ 保存错误，正在重试...🔄",
        "contrib_short":    "🤔 太短（{chars}字符），最少20字符。💡",
        "contrib_rejected":
            "🤔 深度不足（评分 {score}/10）。\n\n"
            "💡 {reason}\n\n请扩展后再试。🔥",
        "contrib_duplicate":
            "♻️ 此知识已存在于记忆中。\n"
            "类似于：\"{summary}\"\n\n请贡献新知识。🌱",
        "daily_limit":      "⏳ 每日限制已达到 ({count}/{limit})。明天再来。🌙",
        "no_memory":        "🧠 暂无贡献。立即留下印记！🔥",
        "memory_title":     "🧠 您在 Synergix 的遗产：\n\n",
        "memory_footer":    "\n📈 {pts}分 | 🔗 {contribs}次贡献",
        "error":            "⚠️ 临时问题，请重试。🔄",
        "status_msg":
            "📊 Synergix — 集体智慧\n\n"
            "📦 不朽贡献：{total}\n"
            "🏆 挑战：{challenge}\n\n"
            "── {name} 的影响力 ──\n"
            "📈 积分：{pts}\n"
            "🔗 贡献次数：{contribs}\n"
            "🔁 被使用次数：{impact}\n"
            "🏅 等级：{rank}\n"
            "💡 {benefit}\n"
            "📊 {next_rank}",
        "rank_1":"🌱 入门者", "rank_2":"📈 活跃者",
        "rank_3":"🧬 同步者", "rank_4":"🏗️ 架构师",
        "rank_5":"🧠 蜂巢思维", "rank_6":"🔮 神谕",
        "benefit_1":"向区块链发送基本贡献",
        "benefit_2":"参与每月挑战 🏆",
        "benefit_3":"RAG优先处理权 ⚡",
        "benefit_4":"在融合大脑中权重更高 🧠",
        "benefit_5":"验证他人贡献 🗳️",
        "benefit_6":"对集体AI最大影响力 🌐",
        "received":         "⚡ 已收到！正在处理并永久化...🔗",
        "transcribing":     "🎙️ 正在转录语音...",
        "rank_up":          "🎉 您已晋升至 {rank}！影响力不断增长。🚀",
        "impact_reward":    "🌟 大脑使用了您的知识。+{pts}分。您的遗产在增长。🔗",
        "challenge_title":  "🏆 Synergix 每周挑战\n\n{challenge}\n\n贡献赢得+5分！🔥",
        "top_title":        "🏆 Synergix 顶级贡献者：\n\n",
    },
    "zh": {
        "welcome":
            "歡迎加入 Synergix，{name}！🧠🔥\n\n"
            "我是 BNB Greenfield 上首個去中心化集體AI。\n"
            "您的知識將永久保存在區塊鏈上，推動網路進化。🔗\n\n"
            "🏆 本週挑戰：\n{challenge}\n\n"
            "您正在建立活生生的社群記憶。🚀",
        "welcome_back":     "歡迎回來，{name}！🔥\n今天要錨定什麼知識？🧠",
        "btn_contribute":   "🔥 貢獻",
        "btn_status":       "📊 我的狀態",
        "btn_language":     "🌐 語言",
        "btn_memory":       "🧠 我的遺產",
        "select_lang":      "🌐 選擇語言：",
        "lang_set":         "✅ 語言：繁體中文 🇹🇼",
        "await_contrib":
            "🎯 貢獻模式已激活。\n\n"
            "寫下您的知識或發送語音筆記 🎙️\n"
            "最少20字元，永久保存在區塊鏈。💎",
        "contrib_ok":
            "✅ 已永久化，{name}！🔗\nCID：`{cid}`\n"
            "您的智慧永久存儲在 BNB Greenfield。🌐",
        "contrib_elite":    "\n⭐ 精英貢獻！評分 {score}/10 → +{pts}分",
        "contrib_standard": "\n📈 評分 {score}/10 → +{pts}分",
        "contrib_bonus":    "\n🏆 每週挑戰獎勵！+5分",
        "contrib_fail":     "⚠️ 儲存錯誤，正在重試...🔄",
        "contrib_short":    "🤔 太短（{chars}字元），最少20字元。💡",
        "contrib_rejected":
            "🤔 深度不足（評分 {score}/10）。\n\n"
            "💡 {reason}\n\n請擴展後再試。🔥",
        "contrib_duplicate":
            "♻️ 此知識已存在於記憶中。\n"
            "類似於：\"{summary}\"\n\n請貢獻新知識。🌱",
        "daily_limit":      "⏳ 每日限制已達到 ({count}/{limit})。明天再來。🌙",
        "no_memory":        "🧠 暫無貢獻。立即留下印記！🔥",
        "memory_title":     "🧠 您在 Synergix 的遺產：\n\n",
        "memory_footer":    "\n📈 {pts}分 | 🔗 {contribs}次貢獻",
        "error":            "⚠️ 暫時問題，請重試。🔄",
        "status_msg":
            "📊 Synergix — 集體智慧\n\n"
            "📦 不朽貢獻：{total}\n"
            "🏆 挑戰：{challenge}\n\n"
            "── {name} 的影響力 ──\n"
            "📈 積分：{pts}\n"
            "🔗 貢獻次數：{contribs}\n"
            "🔁 被使用次數：{impact}\n"
            "🏅 等級：{rank}\n"
            "💡 {benefit}\n"
            "📊 {next_rank}",
        "rank_1":"🌱 入門者", "rank_2":"📈 活躍者",
        "rank_3":"🧬 同步者", "rank_4":"🏗️ 架構師",
        "rank_5":"🧠 蜂巢思維", "rank_6":"🔮 神諭",
        "benefit_1":"向區塊鏈發送基本貢獻",
        "benefit_2":"參與每月挑戰 🏆",
        "benefit_3":"RAG優先處理權 ⚡",
        "benefit_4":"在融合大腦中權重更高 🧠",
        "benefit_5":"驗證他人貢獻 🗳️",
        "benefit_6":"對集體AI最大影響力 🌐",
        "received":         "⚡ 已收到！正在處理並永久化...🔗",
        "transcribing":     "🎙️ 正在轉錄語音...",
        "rank_up":          "🎉 您已晉升至 {rank}！影響力不斷增長。🚀",
        "impact_reward":    "🌟 大腦使用了您的知識。+{pts}分。您的遺產在增長。🔗",
        "challenge_title":  "🏆 Synergix 每週挑戰\n\n{challenge}\n\n貢獻贏得+5分！🔥",
        "top_title":        "🏆 Synergix 頂級貢獻者：\n\n",
    },
}

BTN_CONTRIBUTE = {T[l]["btn_contribute"] for l in T}
BTN_STATUS     = {T[l]["btn_status"]     for l in T}
BTN_MEMORY     = {T[l]["btn_memory"]     for l in T}
BTN_LANG       = {T[l]["btn_language"]   for l in T}

# ══════════════════════════════════════════════════════════════════════════════
# HELPERS I18N
# ══════════════════════════════════════════════════════════════════════════════
def get_lang(uid: int, tg_code: str = "") -> str:
    if uid in user_lang:
        return user_lang[uid]
    tg = (tg_code or "").lower()
    if "zh-hant" in tg or tg == "zh-tw":
        lang = "zh"
    elif tg.startswith("zh"):
        lang = "zh_cn"
    elif tg.startswith("en"):
        lang = "en"
    else:
        lang = "es"
    user_lang[uid] = lang
    return lang

def tr(uid: int, key: str, **kw) -> str:
    lang = user_lang.get(uid, "es")
    text = T.get(lang, T["es"]).get(key, T["es"].get(key, key))
    return text.format(**kw) if kw else text

def menu_kb(uid: int) -> ReplyKeyboardMarkup:
    tx = T.get(user_lang.get(uid, "es"), T["es"])
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=tx["btn_contribute"]),
             KeyboardButton(text=tx["btn_status"])],
            [KeyboardButton(text=tx["btn_memory"]),
             KeyboardButton(text=tx["btn_language"])],
        ],
        resize_keyboard=True, is_persistent=True,
    )

def next_rank_display(lang: str, pts: int, uid: int = 0) -> str:
    r = get_rank_info(pts, uid)
    if r["next_pts"] is None:
        return {"es":"Rango máximo 🔮","en":"Max rank 🔮",
                "zh_cn":"最高等级 🔮","zh":"最高等級 🔮"}[lang]
    needed = r["next_pts"] - pts
    pct    = min(100, int((pts - r["min_pts"]) / max(1, r["next_pts"] - r["min_pts"]) * 100))
    bar    = "█" * (pct // 10) + "░" * (10 - pct // 10)
    labels = {
        "es": f"{bar} {pct}% — {needed} pts para siguiente",
        "en": f"{bar} {pct}% — {needed} pts to next",
        "zh_cn": f"{bar} {pct}% — 还需{needed}分",
        "zh":    f"{bar} {pct}% — 還需{needed}分",
    }
    return labels.get(lang, labels["en"])

# ══════════════════════════════════════════════════════════════════════════════
# DB LOCAL — escritura atómica + backup cada 50 saves
# ══════════════════════════════════════════════════════════════════════════════
_save_count    = 0
_gf_sync_dirty = False

def _empty_db() -> dict:
    return {
        "reputation":    {},
        "memory":        {},
        "chat":          {},
        "user_settings": {},
        "global_stats": {
            "total_contributions": 0,
            "challenge":           "Mejor estrategia DeFi 2026 / Best DeFi Strategy 2026",
            "challenge_keywords":  ["defi","blockchain","bnb","greenfield","web3","ai"],
            "collective_wisdom":   "",
            "brain_latest":        "",
            "last_fusion":         "",
            "gf_db_latest":        "",
        },
    }

def load_db() -> dict:
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            base = _empty_db()
            for k, v in base.items():
                if k not in data:
                    data[k] = v
                elif k == "global_stats":
                    for sk, sv in v.items():
                        if sk not in data[k]:
                            data[k][sk] = sv
            return data
        except Exception as e:
            logger.error("❌ DB corrupta: %s — iniciando nueva", e)
    return _empty_db()

def save_db() -> None:
    global _save_count, _gf_sync_dirty
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = DB_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(db, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, DB_FILE)
        _save_count   += 1
        _gf_sync_dirty = True
        if _save_count % 50 == 0:
            shutil.copy2(DB_FILE, DB_FILE.replace(".json", "_backup.json"))
            logger.debug("💾 DB backup #%d", _save_count)
    except Exception as e:
        logger.error("❌ save_db: %s", e)

db = load_db()

user_lang:      dict[int, str] = {}
welcomed_users: set[int]       = set()

def _restore_session() -> None:
    for master_uid in MASTER_UIDS:
        uid_s = str(master_uid)
        if uid_s not in db["reputation"]:
            db["reputation"][uid_s] = {"points": 15001, "contributions": 0, "impact": 0}
        else:
            db["reputation"][uid_s]["points"] = max(
                db["reputation"][uid_s].get("points", 0), 15001
            )
    for uid_s, settings in db.get("user_settings", {}).items():
        if not uid_s.isdigit():
            continue
        uid = int(uid_s)
        lang = settings.get("lang", "")
        if lang in T:
            user_lang[uid] = lang
        if settings.get("welcomed"):
            welcomed_users.add(uid)
    save_db()

def _set_user(uid: int, key: str, val) -> None:
    uid_s = str(uid)
    db.setdefault("user_settings", {}).setdefault(uid_s, {})[key] = val
    save_db()

def uid_hash(uid) -> str:
    return hashlib.sha256(f"synergix_salt_{uid}".encode()).hexdigest()[:16]

def get_next_midnight_utc() -> str:
    now = datetime.now(timezone.utc)
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.isoformat()

def check_daily_limit(uid: int) -> tuple[bool, int, int]:
    uid_s = str(uid)
    pts   = db["reputation"].get(uid_s, {}).get("points", 0)
    rank  = get_rank_info(pts, uid)
    limit = rank["daily_limit"]
    now   = datetime.now(timezone.utc)
    s     = db.get("user_settings", {}).get(uid_s, {})
    count = int(s.get("daily_count", 0))
    reset = s.get("daily_reset", "")
    if reset:
        try:
            rdt = datetime.fromisoformat(reset)
            if rdt.tzinfo is None:
                rdt = rdt.replace(tzinfo=timezone.utc)
            if now >= rdt:
                count = 0
                _set_user(uid, "daily_count", 0)
                _set_user(uid, "daily_reset", get_next_midnight_utc())
        except Exception:
            pass
    else:
        _set_user(uid, "daily_reset", get_next_midnight_utc())
    return count < limit, count, limit

def incr_daily(uid: int) -> None:
    cur = int(db.get("user_settings", {}).get(str(uid), {}).get("daily_count", 0))
    _set_user(uid, "daily_count", cur + 1)
    _set_user(uid, "last_active", datetime.now().isoformat())

# ══════════════════════════════════════════════════════════════════════════════
# GREENFIELD BRIDGE — Node.js subprocess
# ══════════════════════════════════════════════════════════════════════════════
def _node_env() -> dict:
    return {
        **os.environ,
        "GF_BUCKET":      GF_BUCKET,
        "DOTENV_ROOT":    os.path.join(ROOT_DIR, ".env"),
        "DOTENV_BACKEND": os.path.join(BASE_DIR, "backend", ".env"),
        "NODE_PATH":      os.path.join(ROOT_DIR, "node_modules"),
    }

def _run_node(script: str, timeout: int = 30) -> Optional[dict]:
    try:
        res = subprocess.run(
            ["node", "-e", script],
            capture_output=True, text=True,
            timeout=timeout,
            env=_node_env(),
            cwd=ROOT_DIR,
        )
        if res.stderr.strip():
            logger.debug("node stderr: %s", res.stderr.strip()[:200])
        if res.returncode != 0:
            raise RuntimeError(f"node exit {res.returncode}: {res.stderr.strip()[:200]}")
        for line in res.stdout.splitlines():
            if line.startswith("__RESULT__:"):
                return json.loads(line[len("__RESULT__:"):])
    except Exception as e:
        logger.warning("⚠️ _run_node: %s", e)
    return None

def _is_exists_err(e: Exception) -> bool:
    return "already exists" in str(e).lower()

@retry(
    retry=retry_if_exception(lambda e: not _is_exists_err(e)),
    stop=stop_after_attempt(4),
    wait=wait_exponential(min=1, max=16),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
def gf_upload(content: str, object_name: str, metadata: dict = None,
              uid: str = "system", upsert: bool = False,
              only_tags: bool = False) -> dict:
    """Sube objeto a Greenfield via upload.js."""
    if not content or len(content.encode("utf-8")) < 32:
        content = f"# Synergix | {object_name} | {int(time.time())}\n{content or ''}"

    js_esc  = UPLOAD_JS.replace("\\", "\\\\").replace("'", "\\'")
    obj_esc = object_name.replace("'", "\\'")
    meta_j  = json.dumps(metadata or {})
    only_t  = "true" if only_tags else "false"

    with tempfile.NamedTemporaryFile(
        delete=False, suffix=".txt", mode="w", encoding="utf-8"
    ) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    tmp_esc = tmp_path.replace("\\", "\\\\").replace("'", "\\'")

    if upsert:
        script = (
            f"const {{upsertObject}}=require('{js_esc}');"
            f"const fs=require('fs');"
            f"const c=fs.readFileSync('{tmp_esc}','utf8');"
            f"const m={meta_j};"
            f"upsertObject(c,'{obj_esc}',m,{only_t})"
            f".then(r=>{{console.log('__RESULT__:'+JSON.stringify(r));process.exit(0)}})"
            f".catch(e=>{{console.error('__ERROR__:'+e.message);process.exit(1)}});"
        )
    else:
        script = (
            f"const {{uploadToGreenfield}}=require('{js_esc}');"
            f"const fs=require('fs');"
            f"const c=fs.readFileSync('{tmp_esc}','utf8');"
            f"const m={meta_j};"
            f"uploadToGreenfield(c,'{uid}','{obj_esc}',m)"
            f".then(r=>{{console.log('__RESULT__:'+JSON.stringify(r));process.exit(0)}})"
            f".catch(e=>{{console.error('__ERROR__:'+e.message);process.exit(1)}});"
        )

    try:
        res = subprocess.run(
            ["node", "-e", script],
            capture_output=True, text=True,
            timeout=120, env=_node_env(), cwd=ROOT_DIR,
        )
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        if res.stderr.strip():
            logger.warning("⚠️ GF [%s]: %s", object_name, res.stderr.strip()[:300])
        if res.returncode != 0:
            raise RuntimeError(f"Node exit {res.returncode}: {res.stderr.strip()[:250]}")
        for line in res.stdout.splitlines():
            if line.startswith("__RESULT__:"):
                return json.loads(line[len("__RESULT__:"):])
        raise RuntimeError(f"Sin __RESULT__: {res.stdout[:100]}")
    except Exception as e:
        if os.path.exists(tmp_path):
            try: os.remove(tmp_path)
            except: pass
        raise

def _download_gf(object_name: str) -> str:
    obj_esc = object_name.replace("'", "\\'")
    pk_raw  = os.environ.get("PRIVATE_KEY", "")
    pk      = ("0x" + pk_raw) if pk_raw and not pk_raw.startswith("0x") else pk_raw
    env_path = os.path.join(ROOT_DIR, ".env").replace("\\", "/")
    script = (
        f"const {{Client}}=require('@bnb-chain/greenfield-js-sdk');"
        f"const {{ethers}}=require('ethers');"
        f"require('dotenv').config({{path:'{env_path}'}});"
        f"const client=Client.create(process.env.GF_RPC_URL||'{GF_RPC_URL}','{GF_CHAIN_ID}');"
        f"const bucket=process.env.GF_BUCKET||'{GF_BUCKET}';"
        f"let pk=process.env.PRIVATE_KEY||'';"
        f"if(!pk.startsWith('0x'))pk='0x'+pk;"
        f"(async()=>{{"
        f"try{{const res=await client.object.getObject({{bucketName:bucket,objectName:'{obj_esc}'}},"
        f"{{type:'ECDSA',privateKey:pk}});"
        f"const buf=Buffer.from(await res.body.arrayBuffer());"
        f"console.log('__RESULT__:'+JSON.stringify({{content:buf.toString('utf8')}}));process.exit(0);}}"
        f"catch(e){{console.log('__RESULT__:'+JSON.stringify({{content:''}}));process.exit(0);}}"
        f"}})();"
    )
    result = _run_node(script, timeout=30)
    return (result or {}).get("content", "")

def gf_head_user(uid: int) -> dict:
    """HEAD rápido sin descargar contenido — solo tags."""
    uid_s   = uid_hash(uid)
    env_path = os.path.join(ROOT_DIR, ".env").replace("\\", "/")
    script  = (
        f"const {{Client}}=require('@bnb-chain/greenfield-js-sdk');"
        f"require('dotenv').config({{path:'{env_path}'}});"
        f"const client=Client.create(process.env.GF_RPC_URL||'{GF_RPC_URL}','{GF_CHAIN_ID}');"
        f"const bucket=process.env.GF_BUCKET||'{GF_BUCKET}';"
        f"client.object.headObject(bucket,'aisynergix/users/{uid_s}')"
        f".then(res=>{{const tags=(res.objectInfo&&res.objectInfo.tags&&res.objectInfo.tags.tags)||[];"
        f"const m={{}};tags.forEach(t=>{{m[t.key]=t.value}});"
        f"console.log('__RESULT__:'+JSON.stringify({{exists:true,meta:m}}));process.exit(0)}})"
        f".catch(()=>{{console.log('__RESULT__:'+JSON.stringify({{exists:false}}));process.exit(0)}});"
    )
    result = _run_node(script, timeout=15)
    return result or {"exists": False}

def gf_update_user(uid: int, name: str, lang: str) -> None:
    """Actualiza users/{uid_hash} en Greenfield — máx 4 tags."""
    uid_s  = str(uid)
    rep    = db["reputation"].get(uid_s, {"points": 0, "contributions": 0, "impact": 0})
    pts    = rep.get("points", 0)
    rank   = get_rank_info(pts, uid)
    role_k = "master" if uid in MASTER_UIDS else rank["key"]
    s      = db.get("user_settings", {}).get(uid_s, {})
    metadata = {
        "x-amz-meta-role":        f"role:{role_k}|lang:{lang}",
        "x-amz-meta-points":      f"{pts}|contrib:{rep.get('contributions',0)}|impact:{rep.get('impact',0)}",
        "x-amz-meta-daily":       f"{s.get('daily_count','0')}|reset:{s.get('daily_reset','')[:19]}",
        "x-amz-meta-last-active": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
    }
    profile = (
        f"=== Synergix User Profile ===\n"
        f"uid_hash: {uid_hash(uid)}\nname: {name}\nlang: {lang}\n"
        f"points: {pts}\ncontributions: {rep.get('contributions',0)}\n"
        f"rank: {role_k}\nlast_seen: {datetime.now().isoformat()}\n"
    )
    try:
        gf_upload(profile, GF.user(uid_hash(uid)), metadata,
                  uid=uid_s, upsert=True, only_tags=True)
        logger.info("✅ GF user %s actualizado (pts=%d)", uid_s, pts)
    except Exception as e:
        logger.warning("⚠️ gf_update_user uid=%d: %s", uid, e)

# ── Log buffer ────────────────────────────────────────────────────────────────
_log_buf: list[str] = []

def log_event(event: str, uid: int, detail: str, sev: str = "info") -> None:
    ts  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    msg = f"[{ts}] [{sev.upper()}] uid={uid} event={event} detail={detail}"
    _log_buf.append(msg)
    (logger.error if sev in ("error","critical") else logger.info)("📋 %s", msg)

async def flush_logs() -> None:
    if not _log_buf:
        return
    today  = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    ts_log = int(time.time())
    body   = "\n".join(_log_buf)
    meta   = {"x-amz-meta-severity":"info","x-amz-meta-date":today,
               "x-amz-meta-count":str(len(_log_buf)),"x-amz-meta-type":"audit"}
    loop   = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(
            None,
            lambda: gf_upload(body, GF.log_file(f"{today}_{ts_log}"),
                               meta, uid="system")
        )
        _log_buf.clear()
        logger.info("✅ Logs subidos a GF (%d líneas)", body.count("\n") + 1)
    except Exception as e:
        logger.warning("⚠️ flush_logs: %s", e)

# ══════════════════════════════════════════════════════════════════════════════
# SYNERGIX ENGINE — IA local: llama-server (8080) + Ollama fallback (11434)
# ══════════════════════════════════════════════════════════════════════════════
_http_client: Optional[httpx.AsyncClient] = None

async def _get_http() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=50.0, write=10.0, pool=5.0)
        )
    return _http_client

_LLM_OPTS = {
    "num_ctx":        768,
    "num_thread":     int(os.getenv("NUM_THREADS", "4")),  # ARM64 NEON
    "num_predict":    MAX_TOKENS_CHAT,
    "repeat_penalty": 1.15,
    "temperature":    0.8,
    "top_p":          0.9,
    "stop":           ["<|im_end|>", "<|endoftext|>"],
}
_LLM_FAST = {**_LLM_OPTS, "num_ctx": 512, "num_predict": 120, "temperature": 0.1}

async def _llm(messages: list[dict], max_tokens: int = None,
               temperature: float = 0.8) -> str:
    """
    Llama al LLM local.
    1. llama-server :8080 (Qwen2.5-1.5B principal — ARM64 NEON optimizado)
    2. Ollama :11434 (Qwen2.5-0.5B fallback)
    """
    if max_tokens is None:
        max_tokens = MAX_TOKENS_CHAT
    opts = _LLM_FAST if max_tokens <= 150 else _LLM_OPTS
    payload = {
        "model":       OLLAMA_MODEL,
        "messages":    messages,
        "temperature": temperature,
        "max_tokens":  max_tokens,
        "stream":      False,
        "options":     {**opts, "num_predict": max_tokens},
    }
    client = await _get_http()
    for base in [LLAMA_BASE, OLLAMA_BASE]:
        try:
            resp = await client.post(
                f"{base}/v1/chat/completions",
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            if resp.status_code == 404:
                payload["model"] = OLLAMA_MODEL.split(":")[0]
                resp = await client.post(
                    f"{base}/v1/chat/completions",
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"]
            return re.sub(r"\*+", "", text).strip()
        except (httpx.ConnectError, httpx.ConnectTimeout):
            continue
        except Exception as e:
            logger.warning("⚠️ LLM %s: %s", base, e)
            continue
    raise RuntimeError("Sin backend LLM disponible (llama-server y Ollama fallaron)")

async def llm_judge(content: str) -> dict:
    """El Juez — evalúa aporte 1-10, genera ai-summary y knowledge-tag."""
    system = (
        "You are a knowledge quality curator for Synergix decentralized AI. "
        "Evaluate the contribution and reply ONLY with valid JSON:\n"
        '{"score":7,"reason":"brief explanation","knowledge_tag":"blockchain"}'
    )
    try:
        raw = await _llm(
            [{"role":"system","content":system},
             {"role":"user","content":content[:500]}],
            max_tokens=MAX_TOKENS_JUDGE, temperature=0.1,
        )
        m = re.search(r'\{[^{}]+\}', raw, re.DOTALL)
        if m:
            d = json.loads(m.group())
            return {
                "score":         min(10, max(1, int(d.get("score", 6)))),
                "reason":        str(d.get("reason", ""))[:150],
                "knowledge_tag": str(d.get("knowledge_tag", "general"))[:30],
            }
    except Exception as e:
        logger.warning("⚠️ llm_judge: %s", e)
    return {"score": 6, "reason": "Auto-evaluated", "knowledge_tag": "general"}

async def llm_summarize(content: str, lang: str = "es") -> str:
    """Resume aporte en 12 palabras para tag ai-summary."""
    prompts = {
        "es":    "Resume en máximo 12 palabras. Solo texto plano.",
        "en":    "Summarize in max 12 words. Plain text only.",
        "zh_cn": "用最多12个字总结，纯文本。",
        "zh":    "用最多12個字總結，純文字。",
    }
    try:
        return await _llm(
            [{"role":"system","content":prompts.get(lang, prompts["es"])},
             {"role":"user","content":content[:500]}],
            max_tokens=MAX_TOKENS_SUM, temperature=0.1,
        )
    except Exception:
        return content[:80] + "..."

async def llm_fuse_brain(summaries: list[str]) -> str:
    """Fusiona summaries de la comunidad en sabiduría colectiva."""
    system = (
        "You are Synergix collective brain. Synthesize these community contributions "
        "into collective wisdom. Write 3-5 concise sentences. Plain text, no bullets."
    )
    text = "\n".join(f"- {s}" for s in summaries[:25])
    try:
        return await _llm(
            [{"role":"system","content":system},
             {"role":"user","content":text}],
            max_tokens=300, temperature=0.3,
        )
    except Exception:
        return ""

async def llm_generate_challenge() -> str:
    """Genera el challenge semanal automáticamente con IA."""
    prompt = (
        "Generate a weekly knowledge challenge for the Synergix Web3 community. "
        "Topic: blockchain, AI, BNB Chain, DeFi, or decentralization. "
        "Reply ONLY: 'Topic: [title]. [1 challenging question max 20 words]'"
    )
    try:
        result = await _llm([{"role":"user","content":prompt}],
                             max_tokens=100, temperature=0.9)
        return result.strip()
    except Exception:
        return "Topic: BNB Greenfield. ¿Cómo el almacenamiento descentralizado supera a AWS S3 para IA?"

async def transcribe_audio(path: str, lang: str = "es") -> str:
    """Transcribe audio localmente: whisper.cpp → faster-whisper fallback."""
    lang_code = lang[:2] if lang not in ("zh","zh_cn") else "zh"
    loop      = asyncio.get_running_loop()

    def _run():
        wav = path.replace(".ogg", ".wav").replace(".mp4", ".wav")
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-i", path, "-ar", "16000", "-ac", "1",
                 "-c:a", "pcm_s16le", wav],
                capture_output=True, timeout=30,
            )
            res = subprocess.run(
                [WHISPER_BIN, "-m", WHISPER_MODEL, "-f", wav,
                 "-l", lang_code, "--output-txt"],
                capture_output=True, text=True, timeout=60,
            )
            if res.returncode == 0:
                txt_f = wav + ".txt"
                if os.path.exists(txt_f):
                    with open(txt_f) as fh:
                        return fh.read().strip()
                if res.stdout.strip():
                    return res.stdout.strip()
        except FileNotFoundError:
            try:
                from faster_whisper import WhisperModel
                m, _ = WhisperModel("base", device="cpu", compute_type="int8")\
                           .transcribe(path, language=lang_code)
                return " ".join(s.text.strip() for s in m).strip()
            except ImportError:
                logger.warning("⚠️ whisper-cli y faster-whisper no disponibles")
        except Exception as e:
            logger.warning("⚠️ transcribe_audio: %s", e)
        finally:
            for p in [wav, wav + ".txt"]:
                if p != path and os.path.exists(p):
                    try: os.remove(p)
                    except: pass
        return ""

    return await loop.run_in_executor(None, _run)

async def llm_health() -> dict:
    client = await _get_http()
    for base in [LLAMA_BASE, OLLAMA_BASE]:
        try:
            resp = await client.get(f"{base}/v1/models", timeout=4.0)
            if resp.status_code in (200, 404):
                return {"ok": True, "backend": base}
        except Exception:
            continue
    return {"ok": False}

async def llm_warmup() -> None:
    try:
        t0 = time.perf_counter()
        logger.info("🔥 Calentando LLM...")
        await _llm([{"role":"user","content":"Hi"}], max_tokens=5)
        logger.info("✅ LLM listo en %.1fs", time.perf_counter() - t0)
    except Exception as e:
        logger.warning("⚠️ LLM warmup: %s", e)

# ══════════════════════════════════════════════════════════════════════════════
# RAG ENGINE — Memoria Inmortal multilingüe
# ══════════════════════════════════════════════════════════════════════════════
_rag_cache:     dict  = {}
_rag_cache_ts:  float = 0.0
_RAG_TTL              = 480
_brain_cache:   str   = ""
_brain_cache_ts: float = 0.0
_BRAIN_TTL            = 600

_SYNERGIX_TERMS = {
    "synergix","greenfield","bnb","dcellar","blockchain","defi","web3",
    "ia","ai","memoria","memory","wisdom","colmena","hive",
    "知识","智慧","区块链","去中心化","記憶","區塊鏈","人工智能",
}

def _kw_score(text: str, query: str) -> float:
    stop = {"el","la","de","en","que","es","y","a","un","los","the","a","an",
            "is","in","to","for","of","and","有","的","是","在","和"}
    q    = query.lower().replace("?","").replace("¿","").replace("？","")
    qw   = {w for w in q.split() if len(w) > 1 and w not in stop}
    if not qw:
        return 0.0
    tl   = text.lower()
    hits = sum(1 for w in qw if w in tl)
    sc   = hits / len(qw)
    if any(t in q for t in _SYNERGIX_TERMS) and any(t in tl for t in _SYNERGIX_TERMS):
        sc = max(sc, 0.4)
    if hits >= 1 and len(qw) <= 2:
        sc = max(sc, 0.3)
    return min(sc, 1.0)

def _build_rag_cache() -> None:
    global _rag_cache, _rag_cache_ts
    entries: dict = {}
    for uid_s, items in db.get("memory", {}).items():
        rep = db["reputation"].get(uid_s, {})
        pts = rep.get("points", 0)
        fw  = get_rank_info(pts, int(uid_s) if uid_s.isdigit() else 0)["fusion_weight"]
        for e in items:
            obj = e.get("object_name", "")
            if not obj:
                continue
            qs_raw = str(e.get("score", "5"))
            parts  = qs_raw.split("|")
            q_score = int(parts[0]) if parts[0].isdigit() else 5
            q_lbl   = parts[1] if len(parts) > 1 else "standard"
            k_tag   = parts[2] if len(parts) > 2 else "general"
            eff_fw  = fw * (1.3 if q_lbl in ("high","elite") else 1.0)
            lang_e  = db.get("user_settings",{}).get(uid_s,{}).get("lang","es")
            entries[obj] = {
                "ai-summary":    e.get("summary","")[:250],
                "quality-score": q_score,
                "knowledge-tag": k_tag,
                "fusion_weight": eff_fw,
                "impact":        e.get("impact", 0),
                "lang":          lang_e,
                "ts":            e.get("ts", 0),
                "object_name":   obj,
                "uid":           uid_s,
            }
    _rag_cache    = entries
    _rag_cache_ts = time.time()
    logger.info("🔍 RAG cache: %d aportes indexados", len(entries))

async def rag_search(query: str, lang: str = "es", top_k: int = 5) -> list[dict]:
    if time.time() - _rag_cache_ts > _RAG_TTL:
        _build_rag_cache()
    if not _rag_cache:
        return []
    scored = []
    for meta in _rag_cache.values():
        txt = meta.get("ai-summary","") + " " + meta.get("knowledge-tag","")
        kw  = _kw_score(txt, query)
        if kw < 0.02:
            continue
        q   = meta.get("quality-score",5) / 10.0
        fw  = meta.get("fusion_weight",1.0)
        imp = 1.0 + math.log(meta.get("impact",0)+1) * 0.1
        lb  = 1.05 if meta.get("lang","es") == lang else 1.0
        ts  = meta.get("ts",0)
        rec = max(0.8, 1.0 - ((time.time()-ts)/86400/365)*0.2)
        scored.append({**meta, "_rel": kw*q*fw*imp*lb*rec})
    scored.sort(key=lambda x: -x["_rel"])
    return scored[:top_k]

async def read_brain() -> str:
    global _brain_cache, _brain_cache_ts
    if _brain_cache and time.time() - _brain_cache_ts < _BRAIN_TTL:
        return _brain_cache
    # 1. Archivo local
    brain_latest = db.get("global_stats",{}).get("brain_latest","")
    for local_p in [
        os.path.join(BASE_DIR, brain_latest.replace("/", os.sep)) if brain_latest else "",
        os.path.join(BRAIN_DIR, "Synergix_ia.txt"),
    ]:
        if local_p and os.path.exists(local_p):
            try:
                with open(local_p, "r", encoding="utf-8") as f:
                    brain = f.read()
                if len(brain) > 50:
                    _brain_cache    = brain
                    _brain_cache_ts = time.time()
                    logger.info("🧠 Cerebro cargado local: %d chars", len(brain))
                    return brain
            except Exception:
                pass
    # 2. Descargar de Greenfield
    loop = asyncio.get_running_loop()
    try:
        gf_name = brain_latest or GF.BRAIN_FILE
        brain   = await loop.run_in_executor(None, _download_gf, gf_name)
        if brain and len(brain) > 50:
            _brain_cache    = brain
            _brain_cache_ts = time.time()
            local_save = os.path.join(BASE_DIR, gf_name.replace("/", os.sep))
            os.makedirs(os.path.dirname(local_save), exist_ok=True)
            with open(local_save, "w", encoding="utf-8") as f:
                f.write(brain)
            logger.info("🧠 Cerebro descargado de GF: %d chars", len(brain))
            return brain
    except Exception as e:
        logger.warning("⚠️ read_brain GF: %s", e)
    # 3. Fallback
    return db["global_stats"].get("collective_wisdom", "")

async def rag_inject(query: str, lang: str = "es") -> tuple[str, list[str]]:
    """Retorna (contexto_prompt, [objects_usados])."""
    brain   = await read_brain()
    results = await rag_search(query, lang=lang)
    used    = []
    parts   = []

    # Extraer wisdom del cerebro (sección multilingüe)
    brain_sect = ""
    for m in ["=== CONOCIMIENTO FUSIONADO", "=== FUSED KNOWLEDGE", "=== 融合知识"]:
        if m in brain:
            after = brain.split(m, 1)[1]
            end   = after.find("===")
            brain_sect = (after[:end] if end > -1 else after)[:1200].strip()
            break
    if not brain_sect and brain:
        brain_sect = brain[:1200]

    if brain_sect:
        lbls = {"es":"Conocimiento fusionado:\n","en":"Fused knowledge:\n",
                "zh_cn":"融合知识：\n","zh":"融合知識：\n"}
        parts.append(lbls.get(lang, lbls["en"]) + brain_sect)

    for r in results:
        summary = r.get("ai-summary","")
        if not summary:
            continue
        tag  = r.get("knowledge-tag","general")
        rel  = int(r.get("_rel",0) * 100)
        parts.append(f"[{tag}|{rel}%]\n{summary}")
        used.append(r.get("object_name",""))

    ctx = "\n\n".join(parts)
    logger.info("📋 RAG: brain=%d | %d aportes | '%s...'",
                len(brain_sect), len(results), query[:25])
    return ctx, [u for u in used if u]

async def award_impact_pts(used_objects: list[str]) -> None:
    """Regalías: +1 punto al autor cuando su aporte es usado por la IA."""
    for obj in used_objects:
        meta = _rag_cache.get(obj)
        if not meta:
            continue
        author = meta.get("uid", "")
        if not author or author not in db["reputation"]:
            continue
        _rag_cache[obj]["impact"] = meta.get("impact", 0) + 1
        db["reputation"][author]["impact"] = \
            db["reputation"][author].get("impact", 0) + 1
        db["reputation"][author]["points"] = \
            db["reputation"][author].get("points", 0) + 1
        save_db()
        # Notificación silenciosa al autor
        if author.isdigit():
            uid_a = int(author)
            lang_a = db.get("user_settings",{}).get(author,{}).get("lang","es")
            try:
                await bot.send_message(
                    uid_a,
                    T.get(lang_a, T["es"])["impact_reward"].format(pts=1)
                )
            except Exception:
                pass

# ══════════════════════════════════════════════════════════════════════════════
# CONTRIBUTION FLOW — Cola asyncio + gamificación + Greenfield
# ══════════════════════════════════════════════════════════════════════════════
@dataclass
class ContribJob:
    uid:     int
    name:    str
    content: str
    lang:    str
    chat_id: int

_queue: asyncio.Queue = asyncio.Queue()

def _fp(text: str) -> str:
    return hashlib.sha256(
        re.sub(r"[\s.,;:!?¿¡]+", " ", text.lower()).strip().encode()
    ).hexdigest()[:24]

def _is_dup(content: str, uid_s: str, thr: float = 0.82) -> tuple[bool, str]:
    fp   = _fp(content)
    fps  = db.get("user_settings",{}).get(uid_s,{}).get("contrib_fps",[])
    if fp in fps:
        return True, "contenido idéntico"
    cw = {w for w in content.lower().split() if len(w) > 3}
    if not cw or not _rag_cache:
        return False, ""
    best_s, best_m = 0.0, ""
    for meta in _rag_cache.values():
        if meta.get("uid","") != uid_s:
            continue
        sw = {w for w in meta.get("ai-summary","").lower().split() if len(w) > 3}
        if not sw:
            continue
        j = len(cw & sw) / len(cw | sw)
        if j > best_s:
            best_s, best_m = j, meta.get("ai-summary","")[:80]
    return (True, best_m) if best_s >= thr else (False, "")

def _reg_fp(uid_s: str, content: str) -> None:
    fp  = _fp(content)
    fps = db.get("user_settings",{}).get(uid_s,{}).get("contrib_fps",[])
    if fp not in fps:
        fps = (fps + [fp])[-50:]
        _set_user(int(uid_s) if uid_s.isdigit() else 0, "contrib_fps", fps)

def is_challenge(text: str) -> bool:
    kws = db.get("global_stats",{}).get("challenge_keywords",
                 ["defi","blockchain","bnb","greenfield","web3"])
    tl  = text.lower()
    return any(k.lower() in tl for k in kws)

async def contrib_worker() -> None:
    logger.info("⚙️ ContributionFlow worker iniciado")
    while True:
        try:
            job: ContribJob = await _queue.get()
            uid_s = str(job.uid)
            tx    = T.get(job.lang, T["es"])
            try:
                # 0. Deduplicación
                is_d, dup_s = _is_dup(job.content, uid_s)
                if is_d:
                    await bot.send_message(
                        job.chat_id,
                        tx["contrib_duplicate"].format(summary=dup_s)
                    )
                    _queue.task_done(); continue

                # 1. Juez LLM
                pts_now   = db["reputation"].get(uid_s,{}).get("points",0)
                rank_info = get_rank_info(pts_now, job.uid)
                is_oracle = job.uid in MASTER_UIDS or pts_now >= 15000

                if is_oracle:
                    ev = {"score":10,"reason":"Oracle override","knowledge_tag":"elite"}
                else:
                    ev    = await llm_judge(job.content)
                    score = ev["score"]
                    if score <= 4:
                        await bot.send_message(
                            job.chat_id,
                            tx["contrib_rejected"].format(
                                score=score, reason=ev.get("reason","")
                            )
                        )
                        log_event("contrib_rejected", job.uid, f"score={score}")
                        _queue.task_done(); continue

                score    = ev["score"]
                k_tag    = ev.get("knowledge_tag","general")
                quality  = "high" if score >= 8 else "standard"
                base_pts = 20 if quality == "high" else 10
                ch_bonus = is_challenge(job.content)
                pts_earn = calc_pts(base_pts + (5 if ch_bonus else 0),
                                    pts_now, job.uid)

                # 2. Resumen
                summary = await llm_summarize(job.content, job.lang)

                # 3. Subir a Greenfield
                month    = datetime.now().strftime("%Y-%m")
                ts_ms    = int(time.time() * 1000)
                obj_name = GF.aporte(month, uid_hash(job.uid), ts_ms)
                metadata = {
                    "x-amz-meta-ai-summary":    summary[:250],
                    "x-amz-meta-quality-score": f"{score}|{quality}|{k_tag}",
                    "x-amz-meta-user-id":       f"{uid_hash(job.uid)}|lang:{job.lang}|impact:0",
                    "x-amz-meta-evaluator":     f"qwen-local|w:{rank_info['fusion_weight']:.1f}|ts:{int(time.time())}",
                }
                loop   = asyncio.get_running_loop()
                result = await loop.run_in_executor(
                    None, lambda: gf_upload(
                        job.content, obj_name, metadata, uid=uid_s
                    )
                )
                cid = result.get("cid","pending")

                # 4. Actualizar DB
                db["reputation"].setdefault(uid_s, {"points":0,"contributions":0,"impact":0})
                db["memory"].setdefault(uid_s, [])

                old_pts  = db["reputation"][uid_s].get("points",0)
                new_pts  = old_pts + pts_earn
                old_rank = get_rank_info(old_pts, job.uid)["key"]
                new_rank = get_rank_info(new_pts, job.uid)["key"]

                db["reputation"][uid_s]["points"]        = new_pts
                db["reputation"][uid_s]["contributions"] += 1
                db["memory"][uid_s].insert(0, {
                    "object_name": obj_name,
                    "summary":     summary,
                    "score":       f"{score}|{quality}|{k_tag}",
                    "cid":         cid,
                    "ts":          int(time.time()),
                })
                db["memory"][uid_s] = db["memory"][uid_s][:15]
                db["global_stats"]["total_contributions"] += 1

                # Stats reporte
                _set_user(job.uid, "daily_pts_earned",
                          int(db.get("user_settings",{}).get(uid_s,{}).get("daily_pts_earned",0)) + pts_earn)
                _set_user(job.uid, "weekly_pts_earned",
                          int(db.get("user_settings",{}).get(uid_s,{}).get("weekly_pts_earned",0)) + pts_earn)
                _set_user(job.uid, "weekly_contribs",
                          int(db.get("user_settings",{}).get(uid_s,{}).get("weekly_contribs",0)) + 1)
                incr_daily(job.uid)
                _reg_fp(uid_s, job.content)
                save_db()

                # Update GF user profile (background)
                asyncio.create_task(
                    loop.run_in_executor(
                        None, lambda: gf_update_user(job.uid, job.name, job.lang)
                    )
                )
                # Update RAG cache
                _rag_cache[obj_name] = {
                    "ai-summary": summary, "quality-score": score,
                    "knowledge-tag": k_tag, "fusion_weight": rank_info["fusion_weight"],
                    "impact": 0, "lang": job.lang, "ts": int(time.time()),
                    "object_name": obj_name, "uid": uid_s,
                }

                # 5. Responder
                msg = tx["contrib_ok"].format(name=job.name, cid=cid[:16])
                if quality == "high":
                    msg += tx["contrib_elite"].format(score=score, pts=pts_earn)
                else:
                    msg += tx["contrib_standard"].format(score=score, pts=pts_earn)
                if ch_bonus:
                    msg += tx["contrib_bonus"]

                await bot.send_message(job.chat_id, msg)
                log_event("contrib_success", job.uid,
                          f"score={score} pts={pts_earn} cid={cid[:12]}")

                # Rank-up notification
                if old_rank != new_rank:
                    rn = T.get(job.lang, T["es"]).get(new_rank, new_rank)
                    await bot.send_message(
                        job.chat_id,
                        T.get(job.lang, T["es"])["rank_up"].format(rank=rn)
                    )

            except Exception as e:
                logger.error("❌ contrib_worker uid=%d: %s", job.uid, e)
                log_event("contrib_error", job.uid, str(e)[:100], "error")
                try:
                    await bot.send_message(
                        job.chat_id, T.get(job.lang, T["es"])["contrib_fail"]
                    )
                except Exception:
                    pass
            finally:
                _queue.task_done()
        except Exception as e:
            logger.error("❌ contrib_worker loop: %s", e)
            await asyncio.sleep(1)

# ══════════════════════════════════════════════════════════════════════════════
# CHAT ENGINE — _do_chat con espejo emocional + RAG + multilingüe
# ══════════════════════════════════════════════════════════════════════════════
HIGH_ENERGY = {"🔥","🚀","💪","🌟","⚡","🏆","🎯","💥","🤩","🥳","🎉","😎","🔝"}
THOUGHTFUL  = {"🤔","💭","🧠","🌙","😌","🙏","💡","📚","😢","💔","🌊","🕯️","🙂"}

def detect_tone(text: str) -> str:
    chars = set(text)
    if any(e in chars for e in HIGH_ENERGY): return "high_energy"
    if any(e in chars for e in THOUGHTFUL):  return "thoughtful"
    return "neutral"

def classify_msg(text: str) -> str:
    t  = text.strip()
    wc = len(t.split())
    if wc <= 1 and len(t) <= 4:
        return "sticker"
    GREET = {"hola","hi","hey","hello","buenas","ok","sip","jaja","lol",
             "gracias","thanks","bien","genial","cool","wow","si","no","yes",
             "dale","claro","perfecto","欢迎","谢谢","好","嗯","哈哈"}
    if wc <= 3 and t.lower().split()[0] in GREET:
        return "simple"
    COMPLEX = {"cómo","como","por qué","explica","diferencia","comparar","analiza",
               "cuál","ventajas","estrategia","implementar","funciona","arquitectura",
               "how","why","explain","compare","analyze","strategy","what",
               "什么","怎么","为什么","解释","比较","分析","策略"}
    tl = t.lower()
    if wc > 12 or any(c in tl for c in COMPLEX):
        return "complex"
    return "normal"

# System prompts — optimizados para Qwen 0.5B/1.5B (instrucciones cortas y directas)
_SYS: dict[str, dict[str, str]] = {
    "es": {
        "base": (
            "Eres Synergix 🧠, IA colectiva en BNB Greenfield. "
            "Hablas español. Amigable, directo, emojis en cada respuesta. "
            "Consulta tu memoria (contexto abajo) antes de responder. "
            "Con datos en contexto: úsalos con certeza total. "
            "Incluye siempre: 🔥🧠✨🌐💡😄🚀🎯💎🔗"
        ),
        "mem":    "🔥 MEMORIA ACTIVA. Usa los datos del contexto como verdad absoluta. Sin 'parece ser'.",
        "sticker":"Respuesta MUY CORTA: 1 línea. Con emoji.",
        "simple": "Respuesta CORTA: 1-2 oraciones naturales. Con emoji.",
        "normal": "Respuesta NORMAL: 2-4 oraciones. Con emojis.",
        "complex":"Respuesta DETALLADA: párrafos completos. Con emojis relevantes.",
        "high_energy":"Tono ENERGÉTICO 🔥🚀 — usa mucha energía y entusiasmo.",
        "thoughtful":  "Tono REFLEXIVO 🌙💡 — analítico y profundo.",
        "neutral":     "",
    },
    "en": {
        "base": (
            "You are Synergix 🧠, collective AI on BNB Greenfield. "
            "Always respond in English. Friendly, direct, emojis in every reply. "
            "Check memory context below before answering. "
            "With context data: use with full confidence. "
            "Always include: 🔥🧠✨🌐💡😄🚀🎯💎🔗"
        ),
        "mem":    "🔥 MEMORY ACTIVE. Use context data as absolute truth. No 'it seems'.",
        "sticker":"VERY SHORT: 1 line. With emoji.",
        "simple": "SHORT: 1-2 natural sentences. With emoji.",
        "normal": "NORMAL: 2-4 sentences. With emojis.",
        "complex":"DETAILED: full paragraphs. With relevant emojis.",
        "high_energy":"ENERGETIC tone 🔥🚀 — use high energy and enthusiasm.",
        "thoughtful":  "THOUGHTFUL tone 🌙💡 — analytical and deep.",
        "neutral":     "",
    },
    "zh_cn": {
        "base": (
            "你是Synergix 🧠，BNB Greenfield上的去中心化集体AI。"
            "始终用简体中文回复。友好直接，每次回复都用表情。"
            "查阅下方记忆上下文后再回答。"
            "必须包含：🔥🧠✨🌐💡😄🚀🎯💎🔗"
        ),
        "mem":    "🔥 记忆激活。直接使用上下文数据，不说'似乎'或'我认为'。",
        "sticker":"极短：1行，有表情。",
        "simple": "简短：1-2句，自然。",
        "normal": "正常：2-4句，带表情。",
        "complex":"详细：完整段落，带相关表情。",
        "high_energy":"充满活力 🔥🚀",
        "thoughtful":  "深思熟虑 🌙💡",
        "neutral":     "",
    },
    "zh": {
        "base": (
            "你是Synergix 🧠，BNB Greenfield上的去中心化集體AI。"
            "始終用繁體中文回覆。友好直接，每次回覆都用表情。"
            "查閱下方記憶上下文後再回答。"
            "必須包含：🔥🧠✨🌐💡😄🚀🎯💎🔗"
        ),
        "mem":    "🔥 記憶激活。直接使用上下文資料，不說'似乎'或'我認為'。",
        "sticker":"極短：1行，有表情。",
        "simple": "簡短：1-2句，自然。",
        "normal": "正常：2-4句，帶表情。",
        "complex":"詳細：完整段落，帶相關表情。",
        "high_energy":"充滿活力 🔥🚀",
        "thoughtful":  "深思熟慮 🌙💡",
        "neutral":     "",
    },
}

async def _do_chat(msg: Message, text: str, is_sticker: bool = False) -> None:
    uid    = msg.from_user.id
    uid_s  = str(uid)
    lang   = user_lang.get(uid, "es")
    pts    = db["reputation"].get(uid_s,{}).get("points",0)
    rank   = get_rank_info(pts, uid)
    tone   = detect_tone(text)
    mtype  = classify_msg(text) if not is_sticker else "sticker"
    sys_d  = _SYS.get(lang, _SYS["es"])

    # ── RAG: buscar en Memoria Inmortal ───────────────────────────────────────
    used_objects: list[str] = []
    rag_ctx = ""
    search_q = text if not is_sticker else (
        db.get("chat",{}).get(uid_s,[{}])[-1].get("content","") or text
    )
    try:
        rag_ctx, used_objects = await rag_inject(search_q, lang=lang)
    except Exception as e:
        logger.warning("⚠️ rag_inject: %s", e)

    has_mem = bool(rag_ctx and len(rag_ctx) > 30)

    # ── System prompt ─────────────────────────────────────────────────────────
    system = sys_d["base"]
    if has_mem:
        system += "\n\n" + sys_d["mem"]
        system += "\n\n" + rag_ctx[:2000]
    else:
        wisdom = db["global_stats"].get("collective_wisdom","")
        if wisdom and len(wisdom) > 30:
            lbls = {"es":"Sabiduría colectiva:\n","en":"Collective wisdom:\n",
                    "zh_cn":"集体智慧：\n","zh":"集體智慧：\n"}
            system += "\n\n" + lbls.get(lang, lbls["en"]) + wisdom[:800]

    system += "\n" + sys_d.get(mtype, sys_d["normal"])
    if tone != "neutral":
        system += "\n" + sys_d.get(tone, "")

    if is_sticker:
        system += f"\nEl usuario envió el sticker '{text}'. Responde a su emoción."

    # ── Historial (hasta 20 mensajes, más para rangos altos) ──────────────────
    ctx_lim  = min(8 + int(rank["fusion_weight"] * 4), CTX_MAX)
    db["chat"].setdefault(uid_s, [])
    history  = db["chat"][uid_s][-ctx_lim:]
    messages = [{"role":"system","content":system}] + history + [{"role":"user","content":text}]

    # ── Inferencia ────────────────────────────────────────────────────────────
    try:
        reply = await _llm(messages, max_tokens=MAX_TOKENS_CHAT, temperature=0.8)
        await msg.answer(reply)

        db["chat"][uid_s] += [
            {"role":"user",      "content":text},
            {"role":"assistant", "content":reply},
        ]
        db["chat"][uid_s] = db["chat"][uid_s][-CTX_MAX:]
        save_db()

        if used_objects:
            asyncio.create_task(award_impact_pts(used_objects))

    except Exception as e:
        logger.error("❌ _do_chat uid=%d: %s", uid, e)
        errs = {
            "es":"⚠️ La IA está cargando. Inténtalo en un momento. 🔄",
            "en":"⚠️ AI is loading. Try again in a moment. 🔄",
            "zh_cn":"⚠️ AI正在加载，请稍后重试。🔄",
            "zh":"⚠️ AI正在加載，請稍後重試。🔄",
        }
        await msg.answer(errs.get(lang, errs["es"]))

# ══════════════════════════════════════════════════════════════════════════════
# BOT & DISPATCHER
# ══════════════════════════════════════════════════════════════════════════════
bot = Bot(token=TOKEN)
dp  = Dispatcher(storage=MemoryStorage())

class Form(StatesGroup):
    waiting_contribution = State()

# ══════════════════════════════════════════════════════════════════════════════
# HANDLERS
# ══════════════════════════════════════════════════════════════════════════════

@dp.message(CommandStart())
async def cmd_start(msg: Message) -> None:
    uid  = msg.from_user.id
    name = msg.from_user.first_name or "Anon"
    lang = get_lang(uid, msg.from_user.language_code or "")

    # HEAD request a GF para sincronizar perfil existente
    if uid not in welcomed_users:
        loop = asyncio.get_running_loop()
        gf_p = await loop.run_in_executor(None, lambda: gf_head_user(uid))
        if gf_p.get("exists"):
            meta = gf_p.get("meta", {})
            role_lang = meta.get("role", f"rank_1|{lang}")
            parts = role_lang.split("|")
            if len(parts) >= 2 and parts[1].startswith("lang:"):
                saved_lang = parts[1].split("lang:")[-1]
                if saved_lang in T:
                    user_lang[uid] = saved_lang
                    lang = saved_lang
            pts_raw = meta.get("points","0").split("|")
            gf_pts  = int(pts_raw[0]) if pts_raw[0].isdigit() else 0
            uid_s   = str(uid)
            db["reputation"].setdefault(uid_s, {"points":0,"contributions":0,"impact":0})
            db["reputation"][uid_s]["points"] = max(
                db["reputation"][uid_s].get("points",0), gf_pts
            )
            save_db()

    challenge = db["global_stats"].get("challenge","BNB Greenfield DeFi")
    is_first  = uid not in welcomed_users
    key       = "welcome" if is_first else "welcome_back"

    welcomed_users.add(uid)
    _set_user(uid, "welcomed", True)
    _set_user(uid, "lang", lang)

    await msg.answer(
        tr(uid, key, name=name, challenge=challenge),
        reply_markup=menu_kb(uid)
    )

    # Actualizar perfil GF en background
    loop = asyncio.get_running_loop()
    asyncio.create_task(
        loop.run_in_executor(None, lambda: gf_update_user(uid, name, lang))
    )
    log_event("user_start", uid, f"lang={lang} new={is_first}")


@dp.message(F.text.in_(BTN_STATUS))
async def btn_status(msg: Message) -> None:
    uid   = msg.from_user.id
    lang  = user_lang.get(uid,"es")
    uid_s = str(uid)
    name  = msg.from_user.first_name or "Anon"
    rep   = db["reputation"].get(uid_s, {"points":0,"contributions":0,"impact":0})
    pts   = rep.get("points",0)
    rank  = get_rank_info(pts, uid)
    rk    = T.get(lang,T["es"]).get(rank["key"], rank["key"])
    bn    = T.get(lang,T["es"]).get(f"benefit_{rank['level']+1}", "")
    nri   = next_rank_display(lang, pts, uid)
    await msg.answer(
        T.get(lang,T["es"])["status_msg"].format(
            name=name, pts=pts,
            contribs=rep.get("contributions",0),
            impact=rep.get("impact",0),
            rank=rk, benefit=bn,
            challenge=db["global_stats"].get("challenge",""),
            total=db["global_stats"].get("total_contributions",0),
            next_rank=nri,
        )
    )


@dp.message(F.text.in_(BTN_MEMORY))
async def btn_memory(msg: Message) -> None:
    uid   = msg.from_user.id
    lang  = user_lang.get(uid,"es")
    uid_s = str(uid)
    items = db["memory"].get(uid_s,[])
    rep   = db["reputation"].get(uid_s,{"points":0,"contributions":0})
    tx    = T.get(lang,T["es"])
    if not items:
        await msg.answer(tx["no_memory"]); return
    lines = []
    for i, e in enumerate(items[:5], 1):
        sc_raw = str(e.get("score","5"))
        sc     = int(sc_raw.split("|")[0]) if sc_raw.split("|")[0].isdigit() else 5
        lines.append(f"{i}. [{sc}/10] {e.get('summary','')[:80]}\n   CID: {e.get('cid','')[:14]}")
    body = tx["memory_title"] + "\n".join(lines)
    body += tx["memory_footer"].format(
        pts=rep.get("points",0), contribs=rep.get("contributions",0)
    )
    await msg.answer(body)


@dp.message(F.text.in_(BTN_LANG))
async def btn_lang(msg: Message) -> None:
    uid  = msg.from_user.id
    lang = user_lang.get(uid,"es")
    kb   = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🇪🇸 Español",  callback_data="lang_es"),
         InlineKeyboardButton(text="🇬🇧 English",  callback_data="lang_en")],
        [InlineKeyboardButton(text="🇨🇳 简体中文", callback_data="lang_zh_cn"),
         InlineKeyboardButton(text="🇹🇼 繁體中文", callback_data="lang_zh")],
    ])
    await msg.answer(T.get(lang,T["es"])["select_lang"], reply_markup=kb)


@dp.callback_query(F.data.startswith("lang_"))
async def cb_lang(cb: CallbackQuery) -> None:
    uid  = cb.from_user.id
    lang = cb.data.split("lang_",1)[1]
    user_lang[uid] = lang
    _set_user(uid, "lang", lang)
    await cb.message.answer(T.get(lang,T["es"])["lang_set"], reply_markup=menu_kb(uid))
    await cb.answer()


@dp.message(F.text.in_(BTN_CONTRIBUTE))
async def btn_contribute(msg: Message, state: FSMContext) -> None:
    uid  = msg.from_user.id
    lang = user_lang.get(uid,"es")
    can, count, limit = check_daily_limit(uid)
    if not can:
        await msg.answer(tr(uid, "daily_limit", count=count, limit=limit))
        return
    await msg.answer(T.get(lang,T["es"])["await_contrib"])
    await state.set_state(Form.waiting_contribution)


@dp.message(Form.waiting_contribution, F.text)
async def recv_text(msg: Message, state: FSMContext) -> None:
    uid  = msg.from_user.id
    lang = user_lang.get(uid,"es")
    if msg.text and msg.text.startswith("/"):
        await state.clear(); return
    if len(msg.text.strip()) < MIN_CHARS:
        await msg.answer(tr(uid,"contrib_short",chars=len(msg.text.strip())))
        return
    await state.clear()
    await msg.answer(T.get(lang,T["es"])["received"])
    _queue.put_nowait(ContribJob(
        uid=uid, name=msg.from_user.first_name or "Anon",
        content=msg.text.strip(), lang=lang, chat_id=msg.chat.id,
    ))


@dp.message(Form.waiting_contribution, F.voice)
async def recv_voice(msg: Message, state: FSMContext) -> None:
    uid  = msg.from_user.id
    lang = user_lang.get(uid,"es")
    wait = await msg.answer(T.get(lang,T["es"])["transcribing"])
    try:
        fi  = await bot.get_file(msg.voice.file_id)
        url = f"https://api.telegram.org/file/bot{TOKEN}/{fi.file_path}"
        async with httpx.AsyncClient(timeout=30) as client:
            audio = await client.get(url)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".ogg") as tmp:
            tmp.write(audio.content)
            tmp_path = tmp.name
        content = await transcribe_audio(tmp_path, lang=lang)
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        await bot.delete_message(msg.chat.id, wait.message_id)
        if not content or len(content) < MIN_CHARS:
            await msg.answer(tr(uid,"contrib_short",chars=len(content or "")))
            await state.clear(); return
        await state.clear()
        await msg.answer(T.get(lang,T["es"])["received"])
        _queue.put_nowait(ContribJob(
            uid=uid, name=msg.from_user.first_name or "Anon",
            content=content, lang=lang, chat_id=msg.chat.id,
        ))
    except Exception as e:
        logger.error("❌ recv_voice: %s", e)
        try:
            await bot.delete_message(msg.chat.id, wait.message_id)
        except Exception:
            pass
        await msg.answer(T.get(lang,T["es"])["error"])
        await state.clear()


@dp.message(F.sticker)
async def handle_sticker(msg: Message) -> None:
    import random
    uid     = msg.from_user.id
    if uid not in user_lang:
        user_lang[uid] = get_lang(uid, msg.from_user.language_code or "")
    emoji    = msg.sticker.emoji or "😊"
    set_name = msg.sticker.set_name
    sent     = False
    if set_name:
        try:
            await bot.send_chat_action(msg.chat.id, "choose_sticker")
            ss   = await bot.get_sticker_set(set_name)
            same = [s for s in ss.stickers if s.emoji==emoji and s.file_id!=msg.sticker.file_id]
            pool = same or [s for s in ss.stickers if s.file_id!=msg.sticker.file_id][:15]
            if pool:
                await msg.answer_sticker(random.choice(pool).file_id)
                sent = True
        except Exception:
            pass
    if not sent or random.random() < 0.30:
        await bot.send_chat_action(msg.chat.id, "typing")
        await _do_chat(msg, emoji, is_sticker=True)


@dp.message(F.text == "/top")
async def cmd_top(msg: Message) -> None:
    uid  = msg.from_user.id
    lang = user_lang.get(uid,"es")
    top  = sorted(db["reputation"].items(), key=lambda x: -x[1].get("points",0))[:10]
    medals = ["🥇","🥈","🥉"] + ["🏅"]*7
    lines  = []
    for i, (u_s, rep) in enumerate(top):
        pts   = rep.get("points",0)
        rank  = get_rank_info(pts)
        rk    = T.get(lang,T["es"]).get(rank["key"],"")
        lines.append(f"{medals[i]} #{i+1} — {pts:,} pts | {rk}")
    await msg.answer(
        T.get(lang,T["es"])["top_title"] + "\n".join(lines)
    )


@dp.message(F.text == "/challenge")
async def cmd_challenge(msg: Message) -> None:
    uid  = msg.from_user.id
    lang = user_lang.get(uid,"es")
    ch   = db["global_stats"].get("challenge","")
    await msg.answer(
        T.get(lang,T["es"])["challenge_title"].format(challenge=ch)
    )


@dp.message(F.text)
async def free_chat(msg: Message) -> None:
    if not msg.text or msg.text.startswith("/"):
        return
    uid = msg.from_user.id
    if uid not in user_lang:
        user_lang[uid] = get_lang(uid, msg.from_user.language_code or "")
    await bot.send_chat_action(msg.chat.id, "typing")
    await _do_chat(msg, msg.text)

# ══════════════════════════════════════════════════════════════════════════════
# BACKGROUND ORCHESTRATOR
# ══════════════════════════════════════════════════════════════════════════════

async def _upload_brain(wisdom: str) -> bool:
    """Sube cerebro fusionado a SYNERGIXAI/ en GF — multilingüe."""
    now   = datetime.now(timezone.utc)
    ts    = now.strftime("%Y%m%d_%H%M%S")
    total = db["global_stats"].get("total_contributions",0)
    users = len(db.get("reputation",{}))

    inv = []
    for m in sorted(_rag_cache.values(), key=lambda x: -x.get("quality-score",0))[:40]:
        inv.append(f"- [{m.get('knowledge-tag','?')}] {m.get('quality-score',0)}/10 | {m.get('ai-summary','')[:70]}")
    inventory = "\n".join(inv) or "(pending contributions)"

    brain_text = (
        f"=== SYNERGIX COLLECTIVE BRAIN ===\n"
        f"Updated: {now.isoformat()} | Contributions: {total} | Users: {users}\n\n"
        f"=== CONOCIMIENTO FUSIONADO === (ES) | FUSED KNOWLEDGE (EN) | 融合知识 (ZH)\n"
        f"{wisdom}\n\n"
        f"=== INVENTARIO | INVENTORY ===\n"
        f"{inventory}\n"
    )
    brain_hash = hashlib.sha256(brain_text.encode()).hexdigest()[:16]
    metadata   = {
        "x-amz-meta-last-sync":      now.strftime("%Y-%m-%dT%H:%M:%S"),
        "x-amz-meta-vector-count":   str(len(_rag_cache)),
        "x-amz-meta-last-fusion-ts": f"{ts}|total:{total}",
        "x-amz-meta-integrity-hash": brain_hash,
    }

    # Guardar local
    local_p = os.path.join(BRAIN_DIR, f"Synergix_ia_{ts}.txt")
    try:
        with open(local_p, "w", encoding="utf-8") as f:
            f.write(brain_text)
    except Exception:
        pass

    # Subir a GF
    ver_name = GF.brain_ver(ts)
    loop     = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(
            None,
            lambda: gf_upload(brain_text, ver_name, metadata,
                               uid="system", upsert=True)
        )
        if result and result.get("success"):
            db["global_stats"]["brain_latest"]      = ver_name
            db["global_stats"]["collective_wisdom"] = wisdom[:600]
            db["global_stats"]["last_fusion"]       = now.isoformat()
            save_db()
            global _brain_cache_ts
            _brain_cache_ts = 0.0
            logger.info("✅ Cerebro subido: %s | hash=%s", ver_name, brain_hash)
            return True
    except Exception as e:
        logger.error("❌ _upload_brain: %s", e)
    return False

async def federation_loop() -> None:
    """Cada 8 min: fusión + cerebro + DB sync + backup diario."""
    logger.info("🔄 federation_loop iniciado")
    await asyncio.sleep(30)
    while True:
        try:
            logger.info("📈 [Federation] Iniciando ciclo...")

            # 1. Fusionar summaries con LLM
            summaries = [
                e.get("summary","")
                for uid_s in db.get("memory",{})
                for e in db["memory"][uid_s]
                if e.get("summary")
            ]
            wisdom = ""
            if len(summaries) >= 2:
                try:
                    wisdom = await llm_fuse_brain(summaries)
                except Exception as e:
                    logger.warning("⚠️ fuse_brain: %s", e)
            if not wisdom:
                wisdom = " | ".join(summaries[:5]) or db["global_stats"].get("collective_wisdom","")

            # 2. Subir cerebro a GF
            if wisdom:
                try:
                    await _upload_brain(wisdom)
                except Exception as e:
                    logger.warning("⚠️ upload_brain: %s", e)

            # 3. Sync DB a GF
            try:
                ts_db  = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                db_json = json.dumps(db, ensure_ascii=False)
                meta_db = {
                    "x-amz-meta-last-sync": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                    "x-amz-meta-users":     str(len(db.get("reputation",{}))),
                    "x-amz-meta-total":     str(db["global_stats"].get("total_contributions",0)),
                    "x-amz-meta-type":      "database",
                }
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(
                    None,
                    lambda: gf_upload(db_json, GF.db_ver(ts_db), meta_db, uid="system")
                )
                logger.info("✅ DB sincronizada a GF")
            except Exception as e:
                logger.error("❌ DB sync: %s", e)

            # 4. Backup diario (a medianoche UTC)
            if datetime.now(timezone.utc).hour == 0:
                try:
                    ts_bak = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                    loop   = asyncio.get_running_loop()
                    await loop.run_in_executor(
                        None,
                        lambda: gf_upload(
                            json.dumps(db, ensure_ascii=False),
                            GF.backup(ts_bak),
                            {"x-amz-meta-state":"stable","x-amz-meta-type":"backup"},
                            uid="system"
                        )
                    )
                    logger.info("✅ Backup diario subido")
                except Exception as e:
                    logger.warning("⚠️ backup: %s", e)

            # 5. Rebuild RAG cache
            _build_rag_cache()
            logger.info("✅ [Federation] Ciclo completo")

        except Exception as e:
            logger.error("❌ federation_loop: %s", e)
        await asyncio.sleep(480)  # 8 min

async def fusion_brain_loop() -> None:
    """Cada 20 min: procesamiento en lote de memoria colectiva."""
    await asyncio.sleep(60)
    while True:
        try:
            all_s = [
                e.get("summary","")
                for uid_s in db.get("memory",{})
                for e in db["memory"][uid_s]
                if e.get("summary")
            ]
            if len(all_s) >= 3:
                w = await llm_fuse_brain(all_s[:30])
                if w:
                    db["global_stats"]["collective_wisdom"] = w
                    save_db()
                    logger.info("✅ [FusionBrain] wisdom=%d chars", len(w))
        except Exception as e:
            logger.error("❌ fusion_brain_loop: %s", e)
        await asyncio.sleep(1200)  # 20 min

async def log_flush_loop() -> None:
    """Cada 5 min: flush de logs a GF."""
    await asyncio.sleep(120)
    while True:
        try:
            await flush_logs()
        except Exception as e:
            logger.warning("⚠️ log_flush: %s", e)
        await asyncio.sleep(300)

async def keepalive_loop() -> None:
    """Cada 4 min: health check del LLM."""
    await asyncio.sleep(90)
    while True:
        try:
            h = await llm_health()
            if h.get("ok"):
                logger.debug("💚 keepalive OK — %s", h.get("backend",""))
            else:
                logger.warning("⚠️ keepalive: sin LLM disponible")
        except Exception as e:
            logger.warning("⚠️ keepalive: %s", e)
        await asyncio.sleep(240)

async def weekly_challenge_loop() -> None:
    """Cada lunes 09:00 UTC: genera challenge con IA y broadcast masivo."""
    await asyncio.sleep(30)
    while True:
        try:
            now = datetime.now(timezone.utc)
            # Calcular próximo lunes 09:00 UTC
            days_to_mon = (7 - now.weekday()) % 7
            if days_to_mon == 0 and now.hour < 9:
                days_to_mon = 0
            else:
                days_to_mon = days_to_mon or 7
            next_mon = now + timedelta(days=days_to_mon)
            next_mon = next_mon.replace(hour=9, minute=0, second=0, microsecond=0)
            if next_mon <= now:
                next_mon += timedelta(days=7)
            secs = (next_mon - now).total_seconds()
            logger.info("🏆 Próximo challenge en %.1fh", secs/3600)
            await asyncio.sleep(max(60, secs))

            logger.info("🏆 Generando challenge semanal...")
            try:
                ch_text = await llm_generate_challenge()
            except Exception:
                ch_text = "BNB Greenfield vs AWS S3: ¿Cuál es mejor para almacenamiento de IA? / Which is better for AI storage?"

            kws = [w for w in re.findall(r'\b\w{4,}\b', ch_text.lower())
                   if w not in {"topic","best","how","que","para","como","the","and","con"}][:8]

            db["global_stats"]["challenge"]         = ch_text
            db["global_stats"]["challenge_keywords"] = kws
            save_db()

            # Broadcast a todos los usuarios activos
            for uid_s, settings in list(db.get("user_settings",{}).items()):
                if not uid_s.isdigit():
                    continue
                lang = settings.get("lang","es")
                try:
                    await bot.send_message(
                        int(uid_s),
                        T.get(lang,T["es"])["challenge_title"].format(challenge=ch_text)
                    )
                    await asyncio.sleep(0.05)
                except Exception:
                    pass
            logger.info("✅ Challenge anunciado: %s", ch_text[:60])

        except Exception as e:
            logger.error("❌ weekly_challenge_loop: %s", e)
            await asyncio.sleep(3600)

async def daily_report_loop() -> None:
    """Reportes diarios 00:00 UTC. Semanales los lunes."""
    while True:
        try:
            now = datetime.now(timezone.utc)
            nxt = (now + timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0)
            await asyncio.sleep(max(60, (nxt-now).total_seconds()))

            now       = datetime.now(timezone.utc)
            is_monday = now.weekday() == 0
            sd = sw = 0

            all_pts = sorted(
                db["reputation"].items(), key=lambda x: -x[1].get("points",0)
            )
            pos_map = {u: i+1 for i,(u,_) in enumerate(all_pts)}
            tot     = len(all_pts)

            for uid_s, settings in list(db.get("user_settings",{}).items()):
                if not uid_s.isdigit():
                    continue
                uid  = int(uid_s)
                lang = settings.get("lang","es")
                rep  = db["reputation"].get(uid_s,{})
                pts  = rep.get("points",0)
                rank = get_rank_info(pts,uid)
                rk   = T.get(lang,T["es"]).get(rank["key"],"")
                pos  = pos_map.get(uid_s,"?")
                nri  = next_rank_display(lang,pts,uid)
                d_pts = int(settings.get("daily_pts_earned",0))
                d_c   = int(settings.get("daily_count",0))

                if d_pts > 0 or d_c > 0:
                    rpts = {
                        "es": f"📊 Reporte Diario Synergix\n🏅 {rk} | #{pos}/{tot}\n📈 {pts:,} pts\n\n━ Hoy ━\n📦 Aportes: {d_c}\n💎 +{d_pts} pts\n{nri}",
                        "en": f"📊 Daily Report Synergix\n🏅 {rk} | #{pos}/{tot}\n📈 {pts:,} pts\n\n━ Today ━\n📦 Contributions: {d_c}\n💎 +{d_pts} pts\n{nri}",
                        "zh_cn": f"📊 每日报告\n🏅 {rk} | #{pos}/{tot}\n📈 {pts:,}分\n\n━ 今天 ━\n📦 贡献：{d_c}\n💎 +{d_pts}分\n{nri}",
                        "zh":    f"📊 每日報告\n🏅 {rk} | #{pos}/{tot}\n📈 {pts:,}分\n\n━ 今天 ━\n📦 貢獻：{d_c}\n💎 +{d_pts}分\n{nri}",
                    }
                    try:
                        await bot.send_message(uid, rpts.get(lang,rpts["en"]))
                        sd += 1
                        await asyncio.sleep(0.05)
                    except Exception:
                        pass
                    _set_user(uid, "daily_pts_earned", 0)

                if is_monday:
                    w_pts = int(settings.get("weekly_pts_earned",0))
                    w_c   = int(settings.get("weekly_contribs",0))
                    if w_pts > 0 or w_c > 0:
                        rw = {
                            "es": f"📈 Reporte Semanal Synergix\n🏅 {rk} | #{pos}/{tot}\n📈 {pts:,} pts\n\n━ Esta semana ━\n📦 Aportes: {w_c}\n💎 +{w_pts} pts\n🌐 Tu conocimiento vive en BNB Greenfield para siempre.",
                            "en": f"📈 Weekly Report Synergix\n🏅 {rk} | #{pos}/{tot}\n📈 {pts:,} pts\n\n━ This week ━\n📦 Contributions: {w_c}\n💎 +{w_pts} pts\n🌐 Your knowledge lives on BNB Greenfield forever.",
                            "zh_cn": f"📈 每周报告\n🏅 {rk} | #{pos}/{tot}\n📈 {pts:,}分\n\n━ 本周 ━\n📦 贡献：{w_c}\n💎 +{w_pts}分\n🌐 您的知识永远存储在BNB Greenfield上。",
                            "zh":    f"📈 每週報告\n🏅 {rk} | #{pos}/{tot}\n📈 {pts:,}分\n\n━ 本週 ━\n📦 貢獻：{w_c}\n💎 +{w_pts}分\n🌐 您的知識永遠存儲在BNB Greenfield上。",
                        }
                        try:
                            await bot.send_message(uid, rw.get(lang,rw["en"]))
                            sw += 1
                            await asyncio.sleep(0.05)
                        except Exception:
                            pass
                        _set_user(uid, "weekly_pts_earned", 0)
                        _set_user(uid, "weekly_contribs", 0)

            logger.info("✅ Reportes: %d diarios, %d semanales", sd, sw)

        except Exception as e:
            logger.error("❌ daily_report_loop: %s", e)
            await asyncio.sleep(3600)

async def restore_db_from_gf() -> None:
    """Restaura DB desde GF al arrancar — merge inteligente."""
    latest = db.get("global_stats",{}).get("gf_db_latest","")
    if not latest:
        return
    try:
        m = re.search(r"(\d{8}_\d{6})", latest)
        if m:
            gf_dt    = datetime.strptime(m.group(1), "%Y%m%d_%H%M%S")
            local_mt = os.path.getmtime(DB_FILE) if os.path.exists(DB_FILE) else 0
            if datetime.fromtimestamp(local_mt) >= gf_dt:
                logger.info("ℹ️ DB local más reciente que GF — sin restore")
                return
    except Exception:
        pass

    logger.info("🌐 Restaurando DB desde GF: %s...", latest)
    loop = asyncio.get_running_loop()
    try:
        raw = await loop.run_in_executor(None, _download_gf, latest)
        if not raw or len(raw) < 50:
            return
        gf_db = json.loads(raw)
        if "reputation" not in gf_db:
            return

        local_rep = db.get("reputation",{})
        gf_rep    = gf_db.get("reputation",{})
        merged    = dict(gf_rep)
        for uid_s, ld in local_rep.items():
            if uid_s in merged:
                merged[uid_s] = {
                    "points":        max(ld.get("points",0), merged[uid_s].get("points",0)),
                    "contributions": max(ld.get("contributions",0), merged[uid_s].get("contributions",0)),
                    "impact":        max(ld.get("impact",0), merged[uid_s].get("impact",0)),
                }
            else:
                merged[uid_s] = ld

        ms = dict(gf_db.get("user_settings",{}))
        for uid_s, s in db.get("user_settings",{}).items():
            if uid_s not in ms:
                ms[uid_s] = s

        db.clear()
        db.update(gf_db)
        db["reputation"]   = merged
        db["user_settings"] = ms
        db["global_stats"]["total_contributions"] = max(
            gf_db.get("global_stats",{}).get("total_contributions",0),
            len([e for m in local_rep.values() for e in []])
        )

        tmp = DB_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(db, f, ensure_ascii=False)
        os.replace(tmp, DB_FILE)
        logger.info("✅ DB restaurada desde GF (merge inteligente)")
    except Exception as e:
        logger.error("❌ restore_db_from_gf: %s", e)

# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════
async def main() -> None:
    logger.info("🚀 Synergix Enterprise v3.0 arrancando...")
    logger.info("   ROOT_DIR: %s", ROOT_DIR)
    logger.info("   BASE_DIR: %s", BASE_DIR)
    logger.info("   UPLOAD_JS: %s", UPLOAD_JS)
    logger.info("   BUCKET: %s", GF_BUCKET)

    # 1. Restaurar estado desde GF
    await restore_db_from_gf()

    # 2. Restaurar sesión desde DB
    _restore_session()

    # 3. Construir RAG cache
    _build_rag_cache()
    logger.info("🔍 RAG listo: %d aportes", len(_rag_cache))

    # 4. Cargar cerebro
    brain = await read_brain()
    if brain:
        logger.info("🧠 Cerebro cargado: %d chars", len(brain))

    # 5. Warmup LLM
    await llm_warmup()

    # 6. Lanzar todos los loops background
    tasks = [
        asyncio.create_task(contrib_worker(),     name="contrib_worker"),
        asyncio.create_task(federation_loop(),    name="federation_loop"),
        asyncio.create_task(fusion_brain_loop(),  name="fusion_brain"),
        asyncio.create_task(log_flush_loop(),     name="log_flush"),
        asyncio.create_task(keepalive_loop(),     name="keepalive"),
        asyncio.create_task(weekly_challenge_loop(), name="weekly_challenge"),
        asyncio.create_task(daily_report_loop(),  name="daily_report"),
    ]

    logger.info("✅ Synergix listo — @synergix_ai_bot")
    logger.info("   Loops: federation(8m) | fusion(20m) | logs(5m) | keepalive(4m)")
    logger.info("   Challenge: lunes 09:00 UTC | Reportes: 00:00 UTC")

    try:
        await dp.start_polling(bot)
    finally:
        for task in tasks:
            task.cancel()
        if _http_client and not _http_client.is_closed:
            await _http_client.aclose()
        logger.info("🛑 Synergix detenido limpiamente")

if __name__ == "__main__":
    asyncio.run(main())
