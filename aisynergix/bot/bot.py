import os
import re
import asyncio
import html
import logging
import random
from typing import Optional, Dict, Any

# Carga .env ANTES de importar módulos que leen os.getenv a nivel de módulo
# (services/redemption, bonds, treasury…). En Docker las variables ya vienen
# del compose y load_dotenv no las pisa; esto cubre la ejecución directa
# (Termux / bare-metal), donde el .env del repo no se leía en absoluto.
# Ruta fijada al .env del repo: evita que dotenv siga buscando en directorios
# padre si el archivo no existe (un .env plantado más arriba no se cargaría).
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

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
    BufferedInputFile,
)
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from aisynergix.bot.locales import (
    t,
    get_lang_name,
    get_lang_flag,
    LANG_NAMES,
    LOCALES,
    TELEGRAM_LANG_MAP,
    load_all_locales,
)
from aisynergix.bot.fsm import get_ghost_state_manager, get_l1_cache
from aisynergix.ai.manager import get_ai_manager
from aisynergix.bot.identity import get_identity_manager
from aisynergix.services import trading as trading_svc
from aisynergix.services.wallet_verify import (
    build_challenge,
    build_challenge_simple,
    verify_signature,
    clear_pending,
    get_verified_wallet,
    save_verified_wallet,
    _is_hex_address,
)
from aisynergix.services import dexscreener as dex_svc
from aisynergix.services import four_meme as fourmeme_svc
from aisynergix.services.irys import IRYS_GATEWAY_URL
from aisynergix.nodes import node_manager as nodes_svc
from aisynergix.nodes import knowledge_map as kmap_svc
from aisynergix.services import wallet as wallet_svc
from aisynergix.services import providers as providers_svc
from aisynergix.services import projects as projects_svc
from aisynergix.services import oracle as oracle_svc
from aisynergix.services import bounties as bounties_svc
from aisynergix.services import agent as agent_svc
from aisynergix.services import governance as gov_svc
from aisynergix.services import passport as passport_svc
from aisynergix.services import custody as custody_svc
from aisynergix.services import redemption as redeem_svc
from aisynergix.services import rewards


logger = logging.getLogger("synergix.bot")


def _is_emoji_only(text: str) -> bool:
    """True if text is short and contains no ASCII letters/digits (likely emoji-only)."""
    cleaned = text.strip()
    if not cleaned or len(cleaned) > 20:
        return False
    return not any(c.isascii() and (c.isalpha() or c.isdigit()) for c in cleaned)


# Broad Unicode emoji range — covers emoticons, symbols, dingbats, flags, extras.
_EMOJI_CHAR_RE = re.compile(
    r'[\U0001F000-\U0001FFFF☀-➿⌀-⏿]',
    re.UNICODE,
)


def _extract_emoji_reply(text: str) -> str:
    """Extract up to 2 distinct emoji chars from a model response for emoji-only replies."""
    found = _EMOJI_CHAR_RE.findall(text)
    deduped: list = []
    for e in found:
        if not deduped or e != deduped[-1]:
            deduped.append(e)
        if len(deduped) >= 2:
            break
    return " ".join(deduped) if deduped else "✨"


# Real Telegram UIDs seen during this session — used for challenge broadcast.
# Populated on every incoming message; lost on restart (intentional: ghost protocol).
_known_uids: set = set()

# ID of the last challenge that was broadcast to users — avoids re-sending on restart.
_last_broadcast_challenge_id: Optional[str] = None

# Strong refs to fire-and-forget tasks (custodial wallet creation) so the
# event loop can't garbage-collect them mid-flight.
_bot_bg_tasks: set = set()


def _spawn_bot_bg(coro) -> None:
    task = asyncio.create_task(coro)
    _bot_bg_tasks.add(task)
    task.add_done_callback(_bot_bg_tasks.discard)


async def _ensure_wallet_silent(uid: int) -> None:
    """Crea la wallet custodial en segundo plano (§3.3: invisible para el usuario)."""
    try:
        await wallet_svc.ensure_custodial_wallet(uid)
    except Exception as exc:
        logger.warning("ensure_custodial_wallet uid=%s falló: %s", uid, exc)


# PIR (§7): fuentes (aporte_tx, autor) de cada respuesta con memoria inmortal,
# indexadas por (chat_id, message_id) para el botón "✅ útil".  Un solo uso:
# el callback hace pop.  Se pierde en restart (el botón viejo responde
# "expirado") — aceptable para una confirmación efímera.
_impact_sources: Dict[tuple, str] = {}
_IMPACT_SOURCES_MAX = 500


def _register_impact_sources(chat_id: int, message_id: int, payload: str) -> None:
    _impact_sources[(chat_id, message_id)] = payload
    while len(_impact_sources) > _IMPACT_SOURCES_MAX:
        _impact_sources.pop(next(iter(_impact_sources)), None)


def _useful_button_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=t("btn_useful", lang), callback_data="impact:useful"),
    ]])


async def _challenge_broadcast_loop() -> None:
    """Background task: poll Irys every 10 min for a new challenge and notify users."""
    global _last_broadcast_challenge_id
    from aisynergix.services.irys import load_current_challenge_from_greenfield
    while True:
        await asyncio.sleep(600)  # check every 10 minutes
        try:
            challenge = await load_current_challenge_from_greenfield()
            if not challenge or not challenge.get("active"):
                continue
            challenge_id = challenge.get("id")
            if not challenge_id or challenge_id == _last_broadcast_challenge_id:
                continue  # no new challenge
            _last_broadcast_challenge_id = challenge_id

            uids_to_notify = list(_known_uids)
            if not uids_to_notify:
                logger.info("🎯 Nuevo reto [%s] detectado pero no hay UIDs conocidos aún.", challenge_id)
                continue

            ai = get_ai_manager()
            sent = 0
            for uid in uids_to_notify:
                try:
                    lang = await ai.get_language(uid)
                    desc_raw = challenge.get("description", "")
                    if isinstance(desc_raw, dict):
                        desc = desc_raw.get(lang, desc_raw.get("es", ""))
                    else:
                        desc = str(desc_raw)
                    # Strip any leftover [[STICKER:X]] token from the description.
                    desc = re.sub(r'\s*\[\[STICKER:[^\]]+\]\]\s*', ' ', desc).strip()
                    msg = t("challenge_broadcast", lang, challenge_description=desc)
                    await bot.send_message(uid, msg, parse_mode="HTML")
                    sent += 1
                    await asyncio.sleep(0.05)  # respect Telegram rate limit
                except Exception:
                    pass
            logger.info("📢 Reto [%s] enviado a %d usuarios.", challenge_id, sent)
        except Exception as exc:
            logger.warning("challenge_broadcast_loop error: %s", exc)


async def _api_settlement_loop() -> None:
    """Liquida en segundo plano los royalties de la API a los autores citados
    (Proof-of-Knowledge ③). El bot es dueño del ledger SYNX, así que el pago a
    los humanos se hace aquí, no en el proceso read-only de la API."""
    from aisynergix.services.knowledge_api import settle_pending_usage
    while True:
        await asyncio.sleep(300)  # cada 5 minutos
        try:
            await settle_pending_usage(limit=50)
        except Exception as exc:
            logger.warning("api_settlement_loop error: %s", exc)


async def _notify_oracles_new_review(aporte_tx: str) -> None:
    """Push a los Oráculos activos cuando se abre una revisión (§5.4).

    El hash de UID es irreversible, así que se recorre hacia adelante: por
    cada usuario conocido en RAM se calcula su hash y se comprueba si es un
    Oráculo con stake activo. Best-effort, respeta el rate limit de Telegram.
    """
    from aisynergix.bot.identity import _hash_uid as _h
    sent = 0
    for uid in list(_known_uids):
        try:
            if not await oracle_svc.get_stake(_h(uid)):
                continue
            lang = await get_user_language(uid)
            await bot.send_message(
                uid, t("oracle_new_review_notify", lang), parse_mode="HTML"
            )
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            pass
    if sent:
        logger.info("🔮 Notificados %d Oráculos de la revisión %s", sent, aporte_tx[:12])


# Maps internal Spanish rank key → (rank locale key, benefit locale key)
_RANK_KEY_MAP: Dict[str, tuple] = {
    "🌱 Iniciado":      ("rank_iniciado",      "benefit_iniciado"),
    "⚡ Explorador":    ("rank_explorador",     "benefit_explorador"),
    "🔥 Contribuidor":  ("rank_contribuidor",   "benefit_contribuidor"),
    "💎 Experto":       ("rank_experto",        "benefit_experto"),
    "🌌 Arquitecto":    ("rank_arquitecto",     "benefit_arquitecto"),
    "🔮 Oráculo":       ("rank_oraculo",        "benefit_oraculo"),
    # Legacy (tabla pre-v1.0): perfiles en Irys aún no re-sellados por
    # sync_brain.hydrate_ranks pueden traer estos nombres — se siguen
    # traduciendo hasta que la migración por puntos los reescriba.
    "📈 Activo":        ("rank_activo",         "benefit_activo"),
    "🧬 Sincronizado":  ("rank_sincronizado",   "benefit_sincronizado"),
    "🏗️ Arquitecto":   ("rank_arquitecto",     "benefit_arquitecto"),
    "🧠 Mente Colmena": ("rank_mente_colmena",  "benefit_mente_colmena"),
}


def _t_rank(rank: str, lang: str) -> str:
    """Translate a rank key (e.g. '🌱 Iniciado') into the user's language."""
    keys = _RANK_KEY_MAP.get(rank)
    if not keys:
        return rank
    translated = t(keys[0], lang)
    return rank if translated == keys[0] else translated  # fallback to Spanish if key missing


def _t_benefit(rank: str, lang: str, remaining: int = 0) -> str:
    """Translate the benefit string for a rank into the user's language.
    Substitutes {remaining} with the user's remaining daily contributions so
    the field counts down in real time as they post aportes.
    """
    keys = _RANK_KEY_MAP.get(rank)
    if not keys:
        return rank
    template = t(keys[1], lang)
    if template == keys[1]:
        return rank
    try:
        return template.format(remaining=remaining)
    except (KeyError, IndexError, ValueError):
        return template


async def _keep_typing(
    chat_id: int, stop_event: asyncio.Event, action: str = "typing"
) -> None:
    """Refreshes a Telegram chat action (``typing`` or ``upload_photo``) every 4 s."""
    while not stop_event.is_set():
        try:
            await bot.send_chat_action(chat_id=chat_id, action=action)
        except Exception:
            pass
        # Sleep in 0.5 s slices so we can react quickly to stop_event
        for _ in range(8):
            if stop_event.is_set():
                return
            await asyncio.sleep(0.5)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    raise EnvironmentError("TELEGRAM_TOKEN no configurada en el entorno.")

_ADMIN_IDS: set = set(
    int(x.strip())
    for x in os.getenv("SYNERGIX_ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
)

# ── Group chat config ────────────────────────────────────────────────────────
# Whitelist of group chat IDs (negative numbers) where the bot may respond.
# Empty → responds in any group it's added to. Otherwise only these groups.
_GROUP_WHITELIST: set = set(
    int(x.strip())
    for x in os.getenv("SYNERGIX_GROUP_WHITELIST", "").split(",")
    if x.strip().lstrip("-").isdigit()
)
# Light anti-spam: min seconds between bot answers to the same (group, user).
try:
    _GROUP_COOLDOWN_S = max(0, int(os.getenv("SYNERGIX_GROUP_COOLDOWN", "5")))
except ValueError:
    _GROUP_COOLDOWN_S = 5
_group_last_ts: dict = {}
# Anti-flood en DM: intervalo mínimo (s) entre mensajes de conversación de un
# mismo usuario. Solo afecta al camino que invoca al Thinker (LLM local con
# concurrencia 1): sin esto, un usuario en ráfaga llena la cola asyncio y deja
# sin servicio a los demás. 0 lo desactiva.
try:
    _DM_COOLDOWN_S = max(0, int(os.getenv("SYNERGIX_DM_COOLDOWN", "3")))
except (ValueError, TypeError):
    _DM_COOLDOWN_S = 3
_dm_last_ts: dict = {}
_dm_notified: dict = {}
# Language the bot speaks in groups (the group is multi-user; default English).
_GROUP_LANG = os.getenv("SYNERGIX_GROUP_LANG", "en").strip() or "en"

# Bot identity, filled in on_startup — needed to detect @mentions and replies.
BOT_ID: int = 0
BOT_USERNAME: str = ""

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
            [
                KeyboardButton(text=t("btn_top_mentes", lang)),
                KeyboardButton(text=t("btn_synergix", lang)),
            ],
        ],
        resize_keyboard=True,
        persistent=True,
    )


def get_synergix_inline_keyboard(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=t("btn_verify_wallet", lang), callback_data="synergix:verify"),
                InlineKeyboardButton(text=t("btn_balance", lang), callback_data="synergix:balance"),
            ],
            [
                InlineKeyboardButton(text=t("btn_buy", lang), callback_data="synergix:buy"),
                InlineKeyboardButton(text=t("btn_sell", lang), callback_data="synergix:sell"),
            ],
            [
                InlineKeyboardButton(text=t("btn_progress", lang), callback_data="synergix:progress"),
                InlineKeyboardButton(text=t("btn_market", lang), callback_data="synergix:market"),
            ],
            [
                InlineKeyboardButton(text=t("btn_deposit", lang), callback_data="synergix:deposit"),
                InlineKeyboardButton(text=t("btn_withdraw", lang), callback_data="synergix:withdraw"),
            ],
        ]
    )


def get_trade_confirm_keyboard(lang: str, trade_type: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("btn_confirm_trade", lang),
                    callback_data=f"trade:confirm_{trade_type}",
                ),
                InlineKeyboardButton(
                    text=t("btn_cancel_trade", lang),
                    callback_data="trade:cancel",
                ),
            ]
        ]
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


# ── Group chat: respond only when the trigger keyword is present ─────────────
# Trigger word (default "syn"). Matched as a WHOLE word, case-insensitive, so it
# fires on "SYN"/"syn"/"Syn" but not on "synergy", "síntoma", etc.
_GROUP_KEYWORD = os.getenv("SYNERGIX_GROUP_KEYWORD", "syn").strip()
_GROUP_KEYWORD_RE = (
    re.compile(rf'(?<!\w){re.escape(_GROUP_KEYWORD)}(?!\w)', re.IGNORECASE)
    if _GROUP_KEYWORD else None
)


def _group_allowed(chat_id: int) -> bool:
    return (not _GROUP_WHITELIST) or (chat_id in _GROUP_WHITELIST)


def _is_addressed_to_bot(message: Message) -> bool:
    """True if the message contains the trigger word (e.g. SYN) or replies to the bot."""
    r = message.reply_to_message
    if r and r.from_user and BOT_ID and r.from_user.id == BOT_ID:
        return True
    if message.text and _GROUP_KEYWORD_RE and _GROUP_KEYWORD_RE.search(message.text):
        return True
    return False


def _strip_trigger(text: str) -> str:
    """Remove the trigger word (and any @mention) so the Thinker gets the real ask."""
    if _GROUP_KEYWORD_RE:
        text = _GROUP_KEYWORD_RE.sub(' ', text)
    if BOT_USERNAME:
        text = re.sub(rf'@{re.escape(BOT_USERNAME)}\b', ' ', text, flags=re.IGNORECASE)
    return re.sub(r'\s+', ' ', text).strip()


# Curated fallbacks if the model echoes the user's emoji or returns none.
_REACTION_FALLBACKS = ["👍", "😄", "🔥", "🙌", "✨", "💡", "❤️", "👏", "🤝", "💪"]


def _pick_reaction_emoji(raw: str, exclude: str = "") -> str:
    """Pick ONE emoji that is different from the user's. Prefer the model's choice;
    fall back to a curated pool. Never returns an emoji present in ``exclude``."""
    excl = set(_EMOJI_CHAR_RE.findall(exclude or ""))
    for e in _EMOJI_CHAR_RE.findall(raw or ""):
        if e not in excl:
            return e
    for e in _REACTION_FALLBACKS:
        if e not in excl:
            return e
    return "✨"


async def _group_react_sticker(message: Message) -> None:
    """Reply to a sticker with a sticker: the Thinker picks a reaction emoji and we
    send a matching sticker from the same pack (else a different one, else echo)."""
    sticker = message.sticker
    chosen_file_id = None
    if sticker.set_name:
        try:
            sset = await bot.get_sticker_set(sticker.set_name)
            others = [s for s in sset.stickers if s.file_id != sticker.file_id]
            if others:
                try:
                    raw = await get_ai_manager().react_emoji(
                        f"a sticker showing {sticker.emoji or '🙂'}", _GROUP_LANG
                    )
                    reaction = _extract_emoji_reply(raw)
                except Exception:
                    reaction = ""
                match = [s for s in others if s.emoji and reaction and s.emoji in reaction]
                chosen_file_id = (match[0] if match else random.choice(others)).file_id
        except Exception as exc:
            logger.warning("group sticker pack fetch failed: %s", exc)
    try:
        await message.reply_sticker(chosen_file_id or sticker.file_id)
    except Exception as exc:
        logger.warning("group sticker reply failed: %s", exc)


# Registered BEFORE the DM handlers so ALL group/supergroup traffic is consumed
# here — the DM-only menu, commands, FSM and points never see group messages.
@dp.message(F.chat.type.in_({"group", "supergroup"}))
async def handle_group_message(message: Message) -> None:
    if not message.from_user:
        return
    if not _group_allowed(message.chat.id):
        return  # group not whitelisted → absolute silence
    if not _is_addressed_to_bot(message):
        return  # no trigger word / not a reply to the bot → absolute silence

    # Anti-spam cooldown per (group, user) — applies to all message types.
    key = (message.chat.id, message.from_user.id)
    now = asyncio.get_event_loop().time()
    if _GROUP_COOLDOWN_S and now - _group_last_ts.get(key, 0.0) < _GROUP_COOLDOWN_S:
        return
    _group_last_ts[key] = now

    # 1) Sticker → reply with a sticker.
    if message.sticker is not None:
        await _group_react_sticker(message)
        return

    if message.text is None:
        return
    text = _strip_trigger(message.text)
    if not text:
        return  # message was only the trigger word, nothing to answer

    from aisynergix.services.irys import check_ai_guard
    if check_ai_guard(text):
        return  # jailbreak attempt → silence

    ai = get_ai_manager()

    # 2) Emoji-only → reply with a SINGLE, DIFFERENT emoji (never echo the user's).
    if _is_emoji_only(text):
        try:
            raw = await ai.react_emoji(f"the emoji {text}", _GROUP_LANG)
            await message.reply(_pick_reaction_emoji(raw, exclude=text), parse_mode=None)
        except Exception as exc:
            logger.warning("group emoji reply failed: %s", exc)
        return

    # 3) Text → text reply in the group language (may include emojis).
    stop_typing = asyncio.Event()
    typing_task = asyncio.create_task(_keep_typing(message.chat.id, stop_typing))
    try:
        clean, sticker = await ai.process_group_message(text, _GROUP_LANG)
        if clean:
            # Render the Thinker's Markdown as Telegram HTML (bold titles, etc.)
            # so asterisks/hashes don't show literally; fall back to plain text
            # if the converted HTML fails to parse for any reason.
            html_body = _md_to_html(clean)
            html_out = f"{html_body}\n{sticker}" if sticker else html_body
            try:
                await message.reply(html_out, parse_mode="HTML")
            except Exception:
                plain_out = f"{clean}\n{sticker}" if sticker else clean
                await message.reply(plain_out, parse_mode=None)
    except Exception as exc:
        logger.exception("Error en respuesta de grupo (chat=%s): %s", message.chat.id, exc)
    finally:
        stop_typing.set()
        typing_task.cancel()
        try:
            await typing_task
        except asyncio.CancelledError:
            pass


@dp.message(Command("start"))
async def cmd_start(message: Message) -> None:
    if not message.from_user:
        return

    uid = message.from_user.id
    _known_uids.add(uid)

    identity = get_identity_manager()
    ghost = get_ghost_state_manager()
    ai = get_ai_manager()

    detected_lang = await detect_user_language(message)

    profile = await identity.get_profile(uid)
    # ¿Es realmente nuevo?  Se decide por EXISTENCIA en Irys (base de datos
    # única), no por actividad: los contadores (puntos/usos) solo crecen al
    # contribuir, así que un usuario que solo hizo /start seguiría pareciendo
    # "nuevo" en cada arranque.  has_stored_profile consulta el puntero /
    # user-profile en Irys y distingue ambos casos de forma fiable.
    is_new = not await identity.has_stored_profile(uid)
    name = message.from_user.first_name or "Mente"

    if is_new:
        profile.set_language(detected_lang)
        identity._cache.set(uid, profile)
        lang = detected_lang
        # Persistir el perfil en Irys en su PRIMER /start para reconocerlo en
        # adelante, aunque nunca contribuya o las wallets estén deshabilitadas.
        _spawn_bot_bg(identity.update_profile(uid, profile))
        welcome_text = t("welcome", lang, name=name)
    else:
        lang = profile.language
        welcome_text = t("welcome_back", lang, name=name)

    await ghost.reset_state(uid)

    # Wallet custodial silenciosa (§3.2): se crea en segundo plano para no
    # retrasar la bienvenida (scrypt + upload a Irys tardan ~1-2 s).
    if wallet_svc.wallet_enabled():
        _spawn_bot_bg(_ensure_wallet_silent(uid))

    keyboard = get_main_keyboard(lang)

    await message.answer(welcome_text, reply_markup=keyboard)


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
    name = message.from_user.first_name or ""
    lang = await get_user_language(uid)

    ghost = get_ghost_state_manager()
    await ghost.reset_state(uid)

    ai = get_ai_manager()
    status = await ai.get_user_status(uid, name=name)

    status_text = t(
        "status_msg",
        lang,
        name=html.escape(status["name"]),
        rank=_t_rank(status["rank"], lang),
        points=status["points"],
        contribuciones=status["contribuciones"],
        daily_aportes_count=status["daily_aportes_count"],
        daily_limit=status["daily_limit"],
        total_uses_count=status["total_uses_count"],
        total_aportes=status["total_aportes"],
        tema_actual=status["tema_actual"],
        points_next=status["points_next"],
        beneficio=_t_benefit(status["rank"], lang, status.get("remaining", 0)),
        multiplier=status["multiplier"],
        contribution_count=status["contribution_count"],
        language=get_lang_name(status["language"]),
    )
    status_text += "\n" + t(
        "status_trust_score", lang, trust_score=f"{status.get('trust_score', 5.0):.1f}"
    )
    status_text += "\n" + t("status_synx", lang, synx=f"{status.get('synx_balance', 0):.0f}")
    if status.get("human_verified"):
        status_text += "\n" + t("status_verified", lang)

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

    from aisynergix.services.irys import list_aportes, read_aporte
    from aisynergix.bot.identity import _hash_uid

    uid_hash = _hash_uid(uid)
    aportes = await list_aportes(uid_hash, limit=5)

    if not aportes:
        await message.answer(t("memory_empty", lang))
        return

    identity = get_identity_manager()
    profile = await identity.get_profile(uid)
    count = len(aportes)

    memory_text = t("memory_header", lang, count=count)

    for i, aporte in enumerate(aportes, 1):
        try:
            from datetime import datetime, timezone as _tz
            texto, tags = await read_aporte(aporte["path"])
            quality = tags.get("quality_score", "?")
            category = tags.get("category", "—")

            # Date from filename timestamp (aisynergix/aportes/YYYY-MM/uid_TS.txt)
            try:
                ts_str = aporte["path"].rsplit("/", 1)[-1].split("_", 1)[-1].replace(".txt", "")
                dt = datetime.fromtimestamp(int(ts_str), tz=_tz.utc)
                date_str = dt.strftime("%Y-%m-%d %H:%M")
            except Exception:
                date_str = aporte["path"].rsplit("/", 1)[-1]

            # First sentence of the contribution as summary
            sentences = [s.strip() for s in texto.replace("\n", " ").split(".") if s.strip()]
            summary = sentences[0] if sentences else texto[:100]
            if len(summary) < 20 and len(sentences) > 1:
                summary = summary + ". " + sentences[1]
            if len(summary) > 120:
                summary = summary[:120] + "…"

            memory_text += (
                t("memory_entry", lang, num=i, date=date_str,
                  category=html.escape(str(category)), quality=html.escape(str(quality)),
                  summary=html.escape(summary))
                + "\n"
            )
        except Exception:
            continue

    memory_text += t("memory_footer", lang, points=profile.points, total=count)
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


@dp.message(F.text == "💰 Synergix")
async def handle_synergix_button(message: Message) -> None:
    if not message.from_user:
        return
    uid = message.from_user.id
    lang = await get_user_language(uid)
    ghost = get_ghost_state_manager()
    await ghost.reset_state(uid)
    inline_kb = get_synergix_inline_keyboard(lang)
    await message.answer(t("synergix_menu", lang), reply_markup=inline_kb)


@dp.callback_query(F.data.startswith("lang:"))
async def handle_language_selection(callback: CallbackQuery) -> None:
    if not callback.from_user or not callback.data or not callback.message:
        return

    uid = callback.from_user.id
    lang_code = callback.data.split(":", 1)[1]

    if lang_code not in LANG_NAMES:
        current = await get_user_language(uid)
        await callback.answer(t("lang_unsupported", current))
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
        current = await get_user_language(uid)
        await callback.answer(t("lang_change_error", current))


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
        cb_name = callback.from_user.first_name or ""
        status = await ai.get_user_status(uid, name=cb_name)
        status_text = t(
            "status_msg",
            lang,
            name=html.escape(status["name"]),
            rank=_t_rank(status["rank"], lang),
            points=status["points"],
            contribuciones=status["contribuciones"],
            daily_aportes_count=status["daily_aportes_count"],
            daily_limit=status["daily_limit"],
            total_uses_count=status["total_uses_count"],
            total_aportes=status["total_aportes"],
            tema_actual=status["tema_actual"],
            points_next=status["points_next"],
            beneficio=_t_benefit(status["rank"], lang, status.get("remaining", 0)),
            multiplier=status["multiplier"],
            contribution_count=status["contribution_count"],
            language=get_lang_name(status["language"]),
        )
        status_text += "\n" + t(
            "status_trust_score", lang, trust_score=f"{status.get('trust_score', 5.0):.1f}"
        )
        status_text += "\n" + t("status_synx", lang, synx=f"{status.get('synx_balance', 0):.0f}")
        if status.get("human_verified"):
            status_text += "\n" + t("status_verified", lang)
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


@dp.message(F.text == "🏆 Top Mentes")
@dp.message(F.text == "🏆 Top Minds")
@dp.message(F.text == "🏆 最强大脑")
@dp.message(F.text == "🏆 शीर्ष मन")
@dp.message(F.text == "🏆 أفضل العقول")
@dp.message(F.text == "🏆 Top Esprits")
@dp.message(F.text == "🏆 শীর্ষ মেধা")
@dp.message(F.text == "🏆 Top Pikiran")
@dp.message(F.text == "🏆 سرفہرست ذہن")
async def handle_top_mentes_button(message: Message) -> None:
    if not message.from_user:
        return

    uid = message.from_user.id
    lang = await get_user_language(uid)
    ghost = get_ghost_state_manager()
    await ghost.reset_state(uid)

    # Read from Irys, overlaying the sealed-to-Irys write-ledger so just-updated
    # users rank with current data despite Irys's index lag.
    top10 = await get_ai_manager().get_top10()

    if not top10 or not isinstance(top10, list):
        await message.answer(t("top_mentes_empty", lang))
        return

    medals = ["🥇", "🥈", "🥉"]
    text = t("top_mentes_header", lang) + "\n"
    for i, user in enumerate(top10):
        emoji = medals[i] if i < 3 else f"{i + 1}."
        rank_label = _t_rank(user.get("rank", "🌱 Iniciado"), lang)
        points = user.get("points", 0)
        contribs = user.get("contribution_count", 0)
        uid_hash = user.get("uid", "")
        text += t(
            "top_mentes_entry",
            lang,
            rank_emoji=emoji,
            uid_hash=uid_hash,
            rank_label=rank_label,
            points=points,
            contribs=contribs,
        ) + "\n"

    await message.answer(text)


@dp.callback_query(F.data.startswith("synergix:"))
async def handle_synergix_action(callback: CallbackQuery) -> None:
    if not callback.from_user or not callback.data or not callback.message:
        return

    uid = callback.from_user.id
    action = callback.data.split(":", 1)[1]
    lang = await get_user_language(uid)
    ghost = get_ghost_state_manager()

    from aisynergix.bot.identity import _hash_uid
    uid_hash = _hash_uid(uid)

    if action == "buy":
        await ghost.enter_buy_mode(uid)
        await callback.message.edit_text(
            t("buy_prompt", lang, min_buy=trading_svc.MIN_BUY_BNB)
        )
    elif action == "sell":
        await ghost.enter_sell_mode(uid)
        await callback.message.edit_text(t("sell_prompt", lang))
    elif action == "verify":
        existing = await get_verified_wallet(uid_hash)
        if existing:
            inline_kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text=t("btn_new_wallet", lang), callback_data="verify:start"),
            ]])
            await callback.message.edit_text(
                t("already_verified", lang, address=existing),
                reply_markup=inline_kb,
            )
        else:
            inline_kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text=t("btn_continuar", lang), callback_data="verify:start"),
            ]])
            await callback.message.edit_text(t("verify_intro", lang), reply_markup=inline_kb)
    elif action == "balance":
        # "Mi Saldo" = saldo on-chain de la wallet CUSTODIAL (donde viven los
        # depósitos, recompensas y desde donde se retira). Es la wallet que
        # gestiona el bot; la wallet externa verificada solo se usa como
        # respaldo si la custodial está deshabilitada.
        existing = None
        is_custodial = False
        if wallet_svc.wallet_enabled():
            existing = await wallet_svc.ensure_custodial_wallet(uid)
            is_custodial = bool(existing)
        if not existing:
            existing = await get_verified_wallet(uid_hash)
        if not existing:
            await callback.message.edit_text(t("balance_no_wallet", lang))
        else:
            syn_bal, bnb_bal, market = await asyncio.gather(
                trading_svc.get_token_balance(existing),
                trading_svc.get_bnb_balance(existing),
                dex_svc.get_token_market(trading_svc.SYNERGIX_TOKEN),
            )
            if syn_bal is None or bnb_bal is None:
                await callback.message.edit_text(t("balance_unavailable", lang))
            else:
                price_usd = market["price_usd"] if market else 0.0
                usd_value = syn_bal * price_usd
                balance_text = t(
                    "balance_info",
                    lang,
                    address=existing,
                    syn=syn_bal,
                    usd=usd_value,
                    bnb=bnb_bal,
                )
                # SYNERGIX bloqueado (bonds de nodo + stake de Oráculo): los
                # tokens siguen en la wallet pero no cuentan como disponibles
                # para retiro/venta (libro contable único de bonds.py).
                from aisynergix.services import bonds as bonds_svc
                locked = await bonds_svc.locked_synergix(uid)
                if locked > 0:
                    available = max(0.0, syn_bal - locked)
                    balance_text += "\n" + t(
                        "balance_locked", lang,
                        locked=f"{locked:,.0f}", available=f"{available:,.0f}",
                    )
                # SYNX contable (ledger interno en Irys) — unidad separada del
                # SYNERGIX real on-chain de arriba. Es el saldo que se gana con
                # los aportes y que alimenta bounties, Academy, API y el canje.
                synx_profile = await get_identity_manager().get_profile(uid)
                balance_text += "\n" + t(
                    "status_synx", lang, synx=f"{synx_profile.synx_balance:.0f}"
                )
                if is_custodial:
                    balance_text += "\n\n" + t("balance_custodial_note", lang)
                    # Membresía por tenencia (Capa 1): el tier se calcula sobre
                    # el saldo de la custodial, que es justo `syn_bal` aquí.
                    from aisynergix.services import tiers as tiers_svc
                    tier = tiers_svc.tier_for(syn_bal)
                    nxt = tiers_svc.next_tier(syn_bal)
                    if tier["key"] != "base":
                        balance_text += "\n\n" + t(
                            "membership_current", lang,
                            tier=tier["name"], bonus=tier["daily_bonus"])
                    else:
                        balance_text += "\n\n" + t("membership_none", lang)
                    if nxt:
                        balance_text += "\n" + t(
                            "membership_next", lang, tier=nxt["name"],
                            amount=f"{nxt['min'] - syn_bal:,.0f}", bonus=nxt["daily_bonus"])
                await callback.message.edit_text(balance_text)
    elif action == "progress":
        progress = await fourmeme_svc.get_curve_progress(trading_svc.SYNERGIX_TOKEN)
        if progress is None:
            await callback.message.edit_text(t("progress_unavailable", lang))
        elif progress["graduated"]:
            await callback.message.edit_text(t("progress_graduated", lang))
        else:
            percent = progress["percent"]
            filled = int(percent / 5)  # 20-char bar (5% per cell)
            bar_chars = "█" * filled + "░" * (20 - filled)
            await callback.message.edit_text(
                t(
                    "progress_info",
                    lang,
                    raised=progress["raised_bnb"],
                    target=progress["target_bnb"],
                    percent=percent,
                    bar=f"<code>{bar_chars}</code>",
                )
            )
    elif action == "deposit":
        if not wallet_svc.wallet_enabled():
            await callback.message.edit_text(t("custody_disabled", lang))
        else:
            info = await custody_svc.deposit_info(uid)
            if not info:
                await callback.message.edit_text(t("custody_disabled", lang))
            elif not info.get("healthy", True):
                # La wallet existe pero su keystore no descifra (master key
                # cambiada): NO mostrar la dirección para depositar; ofrecer
                # regenerarla bajo la clave actual.
                kb = InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text=t("btn_wallet_reset", lang), callback_data="wd:reset"),
                ]])
                await callback.message.edit_text(t("wallet_unhealthy", lang), reply_markup=kb)
            else:
                await callback.message.edit_text(
                    t("deposit_info", lang,
                      address=info["address"],
                      bnb=f"{info['bnb']:.6f}" if info["bnb"] is not None else "?",
                      synergix=f"{info['synergix']:.4f}" if info["synergix"] is not None else "?"),
                )

    elif action == "withdraw":
        if not wallet_svc.wallet_enabled():
            await callback.message.edit_text(t("custody_disabled", lang))
        else:
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text=t("btn_withdraw_bnb", lang), callback_data="wd:asset:bnb"),
                InlineKeyboardButton(text=t("btn_withdraw_syn", lang), callback_data="wd:asset:synergix"),
            ]])
            await callback.message.edit_text(t("withdraw_choose_asset", lang), reply_markup=kb)

    elif action == "market":
        market = await dex_svc.get_token_market(trading_svc.SYNERGIX_TOKEN)
        if market:
            dex_label = (market["dex_id"] or "PancakeSwap").replace("pancakeswap", "PancakeSwap").title()
            await callback.message.edit_text(
                t(
                    "market_info",
                    lang,
                    price_usd=market["price_usd"],
                    change_24h=market["price_change_24h"],
                    liquidity=market["liquidity_usd"],
                    volume=market["volume_24h_usd"],
                    mcap=market["market_cap"] or market["fdv"],
                    buys=market["txns_24h_buys"],
                    sells=market["txns_24h_sells"],
                    dex=dex_label,
                )
            )
        else:
            # DexScreener has no data — token is likely still on the bonding curve.
            # Fall back to four.meme curve progress so the user sees something useful.
            progress = await fourmeme_svc.get_curve_progress(trading_svc.SYNERGIX_TOKEN)
            if progress and not progress.get("graduated"):
                percent = progress["percent"]
                filled = int(percent / 5)
                bar_chars = "█" * filled + "░" * (20 - filled)
                await callback.message.edit_text(
                    t(
                        "market_bonding_curve",
                        lang,
                        raised=progress["raised_bnb"],
                        target=progress["target_bnb"],
                        percent=percent,
                        bar=f"<code>{bar_chars}</code>",
                    )
                )
            else:
                await callback.message.edit_text(t("market_unavailable", lang))

    await callback.answer()


@dp.callback_query(F.data == "verify:start")
async def handle_verify_start(callback: CallbackQuery) -> None:
    if not callback.from_user or not callback.message:
        return

    uid = callback.from_user.id
    lang = await get_user_language(uid)
    ghost = get_ghost_state_manager()

    from aisynergix.bot.identity import _hash_uid
    uid_hash = _hash_uid(uid)
    clear_pending(uid_hash)

    challenge = build_challenge_simple(uid_hash)
    await ghost.enter_wallet_signature_mode(uid)
    await callback.message.edit_text(t("verify_etherscan_prompt", lang, challenge=challenge))
    await callback.answer()


@dp.callback_query(F.data.startswith("trade:"))
async def handle_trade_callback(callback: CallbackQuery) -> None:
    if not callback.from_user or not callback.data or not callback.message:
        return

    uid = callback.from_user.id
    action = callback.data.split(":", 1)[1]
    lang = await get_user_language(uid)
    ghost = get_ghost_state_manager()
    cache = get_l1_cache()

    if action == "cancel":
        await ghost.reset_state(uid)
        await cache.set_state_data(uid, {})
        await callback.message.edit_text(t("trade_cancelled", lang))
        await callback.answer()
        return

    trade_data = await cache.get_state_data(uid)
    if not trade_data:
        await callback.message.edit_text(t("trade_cancelled", lang))
        await callback.answer()
        return

    ttype = trade_data.get("type")
    link = trade_data.get("link", "")
    await ghost.reset_state(uid)
    await cache.set_state_data(uid, {})

    # Ejecutar el swap DESDE la wallet custodial cuando la función está activa.
    # Si no (o el token aún no cotiza en PancakeSwap), caer al enlace externo.
    if wallet_svc.wallet_enabled() and ttype in ("buy", "sell"):
        await callback.answer()  # responder ya; el swap puede tardar (approve+swap)
        await callback.message.edit_text(t("trade_executing", lang))
        if ttype == "buy":
            result = await custody_svc.swap_buy(uid, float(trade_data.get("bnb_in", 0)))
        else:
            result = await custody_svc.swap_sell(uid, float(trade_data.get("syn_in", 0)))

        if result.get("ok"):
            # La tenencia cambió → recalcular el tier de membresía en la
            # próxima consulta (Capa 1).
            from aisynergix.services import tiers as tiers_svc
            tiers_svc.invalidate(uid)
            tx = result["tx"]
            tx = tx if tx.startswith("0x") else "0x" + tx
            await callback.message.edit_text(
                t("trade_executed", lang, tx=tx, url=f"https://bscscan.com/tx/{tx}"),
                disable_web_page_preview=True,
            )
        elif result.get("error") == "not_graduated":
            # Aún en bonding curve → enlace externo.
            await callback.message.edit_text(
                t("trade_link", lang, link=link, slippage=trading_svc.DEFAULT_SLIPPAGE)
            )
        else:
            await callback.message.edit_text(t("swap_error_" + result.get("error", "broadcast"), lang))
        return

    await callback.message.edit_text(
        t("trade_link", lang, link=link, slippage=trading_svc.DEFAULT_SLIPPAGE)
    )
    await callback.answer()


# ══════════════════════════════════════════════════════════════════════════
# COMMUNITY NODES
# ══════════════════════════════════════════════════════════════════════════

def _topic_label(key: str, lang: str) -> str:
    icon = nodes_svc.TOPICS.get(key, "🏷️")
    return f"{icon} {t('topic_' + key, lang)}"


def _node_type_label(key: str, lang: str) -> str:
    icon = nodes_svc.NODE_TYPES.get(key, "🌐")
    return f"{icon} {t('node_type_' + key, lang)}"


def get_nodes_menu_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("btn_node_create", lang), callback_data="node:create")],
        [
            InlineKeyboardButton(text=t("btn_node_explore", lang), callback_data="node:explore"),
            InlineKeyboardButton(text=t("btn_node_mine", lang), callback_data="node:mine"),
        ],
        [InlineKeyboardButton(text=t("btn_nodes_by_country", lang), callback_data="geo:countries")],
    ])


def get_country_kb(prefix: str, lang: str = "es") -> InlineKeyboardMarkup:
    """Teclado de países (endónimo + bandera) + 'otro país' para escribir uno
    libremente (libertad total de selección)."""
    from aisynergix.nodes import geo
    rows, row = [], []
    for code, label in geo.COUNTRIES.items():
        row.append(InlineKeyboardButton(text=label, callback_data=f"{prefix}:{code}"))
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(
        text=t("btn_country_other", lang), callback_data=f"{prefix}:{geo.OTHER_COUNTRY}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_scope_kb(lang: str) -> InlineKeyboardMarkup:
    """Teclado de granularidad geográfica (ciudad/barrio/zona rural/región)."""
    from aisynergix.nodes import geo
    rows, row = [], []
    for key, icon in geo.GEO_SCOPES.items():
        row.append(InlineKeyboardButton(
            text=f"{icon} {t('geo_scope_' + key, lang)}", callback_data=f"node:scope:{key}"))
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_node_type_kb(lang: str) -> InlineKeyboardMarkup:
    rows, row = [], []
    for key in nodes_svc.NODE_TYPES:
        row.append(InlineKeyboardButton(text=_node_type_label(key, lang), callback_data=f"node:type:{key}"))
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_node_lang_kb(lang: str) -> InlineKeyboardMarkup:
    """Teclado de idioma principal del nodo (§4.4)."""
    rows, row = [], []
    for code, name in LANG_NAMES.items():
        row.append(InlineKeyboardButton(text=name, callback_data=f"node:lang:{code}"))
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_node_topics_kb(lang: str, selected: list) -> InlineKeyboardMarkup:
    rows, row = [], []
    for key in nodes_svc.TOPICS:
        mark = "✅ " if key in selected else ""
        row.append(InlineKeyboardButton(text=mark + _topic_label(key, lang), callback_data=f"node:topic:{key}"))
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text=t("btn_node_create_confirm", lang), callback_data="node:topics_done")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_node_view_kb(
    lang: str, node_id: str, member: bool,
    is_founder: bool = False, bond: Optional[dict] = None,
) -> InlineKeyboardMarkup:
    rows = []
    if member:
        rows.append([
            InlineKeyboardButton(text=t("btn_node_use", lang), callback_data=f"node:use:{node_id}"),
            InlineKeyboardButton(text=t("btn_node_leave", lang), callback_data=f"node:leave:{node_id}"),
        ])
    else:
        rows.append([InlineKeyboardButton(text=t("btn_node_join", lang), callback_data=f"node:join:{node_id}")])
    # El fundador con bond bloqueado puede iniciar el unbonding para recuperarlo.
    if is_founder and bond and bond.get("bond-status") == "locked":
        rows.append([InlineKeyboardButton(text=t("btn_node_unbond", lang), callback_data=f"node:unbond:{node_id}")])
    rows.append([InlineKeyboardButton(text=t("btn_node_back", lang), callback_data="node:explore")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _bond_status_text(bond: Optional[dict], lang: str) -> str:
    """Línea de estado del bond para la vista del nodo (solo fundador)."""
    if not bond:
        return ""
    status = bond.get("bond-status", "locked")
    try:
        amount = float(bond.get("amount", 0) or 0)
    except (ValueError, TypeError):
        amount = 0.0
    amt = f"{amount:,.0f}"
    if status == "locked":
        return t("node_bond_status_locked", lang, amount=amt)
    if status == "unbonding":
        from datetime import datetime, timezone as _tz
        try:
            until = int(bond.get("unbond-until", 0) or 0)
            date = datetime.fromtimestamp(until, tz=_tz.utc).strftime("%Y-%m-%d")
        except Exception:
            date = "?"
        return t("node_bond_status_unbonding", lang, amount=amt, date=date)
    if status == "slashed":
        return t("node_bond_status_slashed", lang, amount=amt)
    return t("node_bond_status_released", lang, amount=amt)


async def _render_node_view(node, lang: str) -> str:
    from aisynergix.nodes.node_stats import node_stats
    from aisynergix.nodes import geo
    type_label = _node_type_label(node.node_type, lang)
    location = geo.location_label(node.country, node.region, node.geo_scope)
    if location:
        type_label += f" · {location}"
    lang_label = get_lang_name(node.language)
    if node.topics:
        topics_str = ", ".join(_topic_label(k, lang) for k in node.topics)
        cov = await kmap_svc.node_knowledge_map(node.node_id, node.topics)
        map_str = kmap_svc.render_map(cov, lambda k: _topic_label(k, lang))
    else:
        topics_str = t("node_view_no_topics", lang)
        map_str = "—"
    stats = await node_stats(node.node_id)
    return t(
        "node_view", lang,
        icon=node.icon, name=html.escape(node.name), type=type_label,
        language=lang_label, members=node.member_count, aportes=node.aporte_count,
        synx=f"{stats['synx_circulating']:,.0f}", projects=stats["active_projects"],
        impacts=stats["verified_impacts"], topics=topics_str, map=map_str,
    )


async def _begin_node_creation(uid: int) -> None:
    ghost = get_ghost_state_manager()
    cache = get_l1_cache()
    await ghost.enter_node_name_mode(uid)
    await cache.set_state_data(uid, {"step": "name", "topics": []})


async def _bond_insufficient_msg(uid: int, lang: str) -> Optional[str]:
    """Mensaje de SYNERGIX insuficiente para el bond, o None si puede pagarlo."""
    from aisynergix.services import bonds as bonds_svc
    if await bonds_svc.can_afford_bond(uid):
        return None
    avail = 0.0
    try:
        addr = await wallet_svc.ensure_custodial_wallet(uid)
        bal = (await trading_svc.get_token_balance(addr) or 0.0) if addr else 0.0
        avail = await bonds_svc.available_synergix(uid, bal)
    except Exception:
        pass
    return t("node_bond_insufficient", lang,
             bond=f"{bonds_svc.BOND_AMOUNT:,.0f}", available=f"{avail:,.0f}")


async def _start_node_creation_text(uid: int, lang: str) -> str:
    """Comprueba el bond (anti-spam) y arranca la creación. Devuelve el texto."""
    from aisynergix.services import bonds as bonds_svc
    msg = await _bond_insufficient_msg(uid, lang)
    if msg:
        return msg
    await _begin_node_creation(uid)
    return t("node_create_ask_name", lang, bond=f"{bonds_svc.BOND_AMOUNT:,.0f}")


@dp.message(Command("nodos"))
async def cmd_nodes(message: Message) -> None:
    if not message.from_user:
        return
    uid = message.from_user.id
    _known_uids.add(uid)
    lang = await get_user_language(uid)
    await get_ghost_state_manager().reset_state(uid)
    await message.answer(t("nodes_menu_title", lang), reply_markup=get_nodes_menu_kb(lang))


@dp.message(Command("crear_nodo"))
async def cmd_create_node(message: Message) -> None:
    if not message.from_user:
        return
    uid = message.from_user.id
    _known_uids.add(uid)
    lang = await get_user_language(uid)
    await message.answer(await _start_node_creation_text(uid, lang))


async def handle_node_name_message(message: Message, uid: int, text: str, lang: str) -> None:
    cache = get_l1_cache()
    draft = await cache.get_state_data(uid) or {}
    # Past the name step the flow is inline-only; nudge the user to the buttons.
    if draft.get("step") and draft["step"] != "name":
        await message.answer(t("node_use_buttons", lang))
        return
    name = text.strip()
    if not (nodes_svc.MIN_NODE_NAME_LEN <= len(name) <= nodes_svc.MAX_NODE_NAME_LEN):
        await message.answer(t("node_name_too_short", lang))
        return
    draft.update({"name": name, "step": "type", "topics": draft.get("topics", [])})
    await cache.set_state_data(uid, draft)
    await message.answer(
        t("node_create_ask_type", lang, name=html.escape(name)),
        reply_markup=get_node_type_kb(lang),
    )


@dp.callback_query(F.data.startswith("geo:"))
async def handle_geo_callback(callback: CallbackQuery) -> None:
    """Exploración territorial: países con nodos → nodos de un país."""
    if not callback.from_user or not callback.data or not callback.message:
        return
    uid = callback.from_user.id
    lang = await get_user_language(uid)
    from aisynergix.nodes import geo
    from aisynergix.services.irys import list_node_records
    parts = callback.data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    arg = parts[2] if len(parts) > 2 else ""

    if action == "countries":
        records = await list_node_records(limit=200)
        grouped = geo.group_by_country(records)
        if not grouped:
            await callback.message.edit_text(
                t("geo_no_countries", lang), reply_markup=get_nodes_menu_kb(lang))
        else:
            rows = [[InlineKeyboardButton(
                text=f"{geo.country_label(code)} ({count})",
                callback_data=f"geo:country:{code}",
            )] for code, count in grouped[:20]]
            rows.append([InlineKeyboardButton(
                text=t("btn_node_back", lang), callback_data="node:menu")])
            await callback.message.edit_text(
                t("geo_countries_header", lang),
                reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            )

    elif action == "country":
        # arg = "<code>" o "<code>|<scope>" para filtrar por granularidad.
        code, _, scope = arg.partition("|")
        records = geo.filter_by(await list_node_records(limit=200), country=code)
        rows = []
        # Chips de filtro por granularidad (ciudad/barrio/zona rural/región).
        scopes = geo.scopes_present(records)
        if scopes:
            chip = []
            for s in scopes:
                mark = "✓ " if s == scope else ""
                chip.append(InlineKeyboardButton(
                    text=f"{mark}{geo.scope_icon(s)} {t('geo_scope_' + s, lang)}",
                    callback_data=f"geo:country:{code}|{'' if s == scope else s}"))
            for i in range(0, len(chip), 2):
                rows.append(chip[i:i + 2])
        shown = geo.filter_by(records, scope=scope) if scope else records
        for r in shown[:15]:
            icon = nodes_svc.NODE_TYPES.get(r.get("node-type", ""), "🌐")
            name = r.get("node-name", "")
            region = (r.get("region", "") or "").strip()
            sc = (r.get("geo-scope", "") or "").strip()
            place = (f"{geo.scope_icon(sc)} {region}" if region else "")
            label = f"{icon} {name}" + (f" · {place}" if place else "")
            rows.append([InlineKeyboardButton(
                text=label, callback_data=f"node:view:{r.get('node-id', '')}")])
        rows.append([InlineKeyboardButton(
            text=t("btn_node_back", lang), callback_data="geo:countries")])
        await callback.message.edit_text(
            t("geo_country_header", lang, country=geo.country_label(code)),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )

    await callback.answer()


async def _node_after_country(msg: Message, uid: int, lang: str, draft: dict, edit: bool) -> None:
    """Tras elegir país: pide granularidad (ciudad/barrio/zona rural/región)
    si el tipo la usa, o salta a los temas. `edit` reutiliza el mensaje inline."""
    from aisynergix.nodes import geo
    cache = get_l1_cache()
    if geo.needs_scope(draft.get("node_type", "")):
        draft["step"] = "scope"
        await cache.set_state_data(uid, draft)
        send = msg.edit_text if edit else msg.answer
        await send(t("node_create_ask_scope", lang), reply_markup=get_scope_kb(lang))
    else:
        draft.update({"step": "topics", "topics": draft.get("topics", [])})
        await cache.set_state_data(uid, draft)
        sel = draft["topics"]
        selected = ", ".join(_topic_label(k, lang) for k in sel) if sel else t("node_topics_none", lang)
        send = msg.edit_text if edit else msg.answer
        await send(
            t("node_create_ask_topics", lang, selected=selected),
            reply_markup=get_node_topics_kb(lang, sel),
        )


async def handle_node_country_message(message: Message, uid: int, text: str, lang: str) -> None:
    """Paso de país libre: el usuario escribe cualquier país no listado."""
    from aisynergix.nodes import geo
    ghost = get_ghost_state_manager()
    cache = get_l1_cache()
    draft = await cache.get_state_data(uid) or {}
    country = geo.normalize_country(text)
    if not country:
        await message.answer(t("node_country_invalid", lang))
        return
    await ghost.reset_state(uid)
    draft["country"] = country
    await cache.set_state_data(uid, draft)
    await _node_after_country(message, uid, lang, draft, edit=False)


async def handle_node_region_message(message: Message, uid: int, text: str, lang: str) -> None:
    """Paso región/lugar de la creación de un nodo (nombre del lugar)."""
    from aisynergix.nodes import geo
    ghost = get_ghost_state_manager()
    cache = get_l1_cache()
    draft = await cache.get_state_data(uid) or {}
    region = geo.normalize_region(text)
    if not region:
        await message.answer(t("node_region_invalid", lang))
        return
    await ghost.reset_state(uid)
    draft.update({"region": region, "step": "topics", "topics": draft.get("topics", [])})
    await cache.set_state_data(uid, draft)
    sel = draft["topics"]
    selected = ", ".join(_topic_label(k, lang) for k in sel) if sel else t("node_topics_none", lang)
    await message.answer(
        t("node_create_ask_topics", lang, selected=selected),
        reply_markup=get_node_topics_kb(lang, sel),
    )


@dp.callback_query(F.data.startswith("node:"))
async def handle_node_callback(callback: CallbackQuery) -> None:
    if not callback.from_user or not callback.data or not callback.message:
        return
    uid = callback.from_user.id
    lang = await get_user_language(uid)
    cache = get_l1_cache()
    ghost = get_ghost_state_manager()
    parts = callback.data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    arg = parts[2] if len(parts) > 2 else ""

    if action == "menu":
        await callback.message.edit_text(t("nodes_menu_title", lang), reply_markup=get_nodes_menu_kb(lang))

    elif action == "create":
        await callback.message.edit_text(await _start_node_creation_text(uid, lang))

    elif action == "explore":
        node_list = await nodes_svc.list_nodes(limit=10)
        if not node_list:
            await callback.message.edit_text(t("node_explore_empty", lang), reply_markup=get_nodes_menu_kb(lang))
        else:
            rows = [[InlineKeyboardButton(text=f"{n.icon} {n.name}", callback_data=f"node:view:{n.node_id}")]
                    for n in node_list]
            rows.append([InlineKeyboardButton(text=t("btn_node_back", lang), callback_data="node:menu")])
            await callback.message.edit_text(t("node_explore_header", lang), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

    elif action == "mine":
        my_nodes = await nodes_svc.list_user_nodes(uid)
        if not my_nodes:
            await callback.message.edit_text(t("node_mine_empty", lang), reply_markup=get_nodes_menu_kb(lang))
        else:
            rows = [[InlineKeyboardButton(text=f"{n.icon} {n.name}", callback_data=f"node:view:{n.node_id}")]
                    for n in my_nodes]
            rows.append([InlineKeyboardButton(text=t("btn_node_back", lang), callback_data="node:menu")])
            await callback.message.edit_text(t("node_mine_header", lang), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

    elif action == "view":
        node = await nodes_svc.get_node(arg)
        if not node:
            await callback.message.edit_text(t("node_not_found", lang), reply_markup=get_nodes_menu_kb(lang))
        else:
            from aisynergix.bot.identity import _hash_uid
            from aisynergix.services.irys import get_node_bond
            member = await nodes_svc.is_member(arg, uid)
            is_founder = node.creator == _hash_uid(uid)
            text = await _render_node_view(node, lang)
            bond = await get_node_bond(arg) if is_founder else None
            if bond:
                text += "\n" + _bond_status_text(bond, lang)
            await callback.message.edit_text(
                text, reply_markup=get_node_view_kb(lang, arg, member, is_founder, bond),
            )

    elif action == "unbond":
        from aisynergix.services import bonds as bonds_svc
        node = await nodes_svc.get_node(arg)
        if not node:
            await callback.answer(); return
        unbond_until = await bonds_svc.begin_unbond(uid, arg)
        if unbond_until is None:
            await callback.answer(t("node_unbond_denied", lang), show_alert=True)
        else:
            from datetime import datetime, timezone as _tz
            date = datetime.fromtimestamp(unbond_until, tz=_tz.utc).strftime("%Y-%m-%d")
            await callback.message.edit_text(
                t("node_unbond_started", lang, days=bonds_svc.UNBOND_DAYS, date=date)
            )

    elif action == "type":
        draft = await cache.get_state_data(uid) or {}
        if not draft.get("name"):
            await callback.answer(); return
        draft.update({"node_type": arg, "step": "language", "topics": draft.get("topics", [])})
        await cache.set_state_data(uid, draft)
        await callback.message.edit_text(
            t("node_create_ask_language", lang),
            reply_markup=get_node_lang_kb(lang),
        )

    elif action == "lang":
        from aisynergix.nodes import geo
        draft = await cache.get_state_data(uid) or {}
        if not draft.get("node_type") or arg not in LANG_NAMES:
            await callback.answer(); return
        draft["language"] = arg
        if geo.needs_location(draft.get("node_type", "")):
            # Nodo territorial: primero el país (y luego la región si es barrio).
            draft["step"] = "country"
            await cache.set_state_data(uid, draft)
            await callback.message.edit_text(
                t("node_create_ask_country", lang),
                reply_markup=get_country_kb("node:country", lang),
            )
        else:
            draft.update({"step": "topics", "topics": draft.get("topics", [])})
            await cache.set_state_data(uid, draft)
            sel = draft["topics"]
            selected = ", ".join(_topic_label(k, lang) for k in sel) if sel else t("node_topics_none", lang)
            await callback.message.edit_text(
                t("node_create_ask_topics", lang, selected=selected),
                reply_markup=get_node_topics_kb(lang, sel),
            )

    elif action == "country":
        from aisynergix.nodes import geo
        draft = await cache.get_state_data(uid) or {}
        if not draft.get("node_type") or not geo.is_valid_country(arg):
            await callback.answer(); return
        if arg == geo.OTHER_COUNTRY:
            # Libertad total: escribir cualquier país que no esté en la lista.
            draft["step"] = "country_free"
            await cache.set_state_data(uid, draft)
            await ghost.set_state(uid, "awaiting_node_country")
            await callback.message.edit_text(t("node_create_ask_country_free", lang))
        else:
            draft["country"] = arg
            await cache.set_state_data(uid, draft)
            await _node_after_country(callback.message, uid, lang, draft, edit=True)

    elif action == "scope":
        from aisynergix.nodes import geo
        draft = await cache.get_state_data(uid) or {}
        if not draft.get("node_type") or not geo.is_valid_scope(arg):
            await callback.answer(); return
        draft.update({"geo_scope": arg, "step": "region"})
        await cache.set_state_data(uid, draft)
        await ghost.set_state(uid, "awaiting_node_region")
        await callback.message.edit_text(
            t("node_create_ask_place", lang, scope=t("geo_scope_" + arg, lang)))

    elif action == "topic":
        draft = await cache.get_state_data(uid) or {}
        sel = list(draft.get("topics", []))
        if arg in sel:
            sel.remove(arg)
        elif len(sel) < nodes_svc.MAX_TOPICS_PER_NODE:
            sel.append(arg)
        draft["topics"] = sel
        await cache.set_state_data(uid, draft)
        selected = ", ".join(_topic_label(k, lang) for k in sel) if sel else t("node_topics_none", lang)
        try:
            await callback.message.edit_text(
                t("node_create_ask_topics", lang, selected=selected),
                reply_markup=get_node_topics_kb(lang, sel),
            )
        except Exception:
            pass

    elif action == "topics_done":
        from aisynergix.services import bonds as bonds_svc
        draft = await cache.get_state_data(uid) or {}
        name = draft.get("name", "")
        if not name:
            await callback.message.edit_text(
                t("node_create_ask_name", lang, bond=f"{bonds_svc.BOND_AMOUNT:,.0f}"))
            await _begin_node_creation(uid)
            await callback.answer(); return
        node = await nodes_svc.create_node(
            uid, name, draft.get("node_type", "global"),
            draft.get("language", lang), draft.get("topics", []),
            country=draft.get("country", ""), region=draft.get("region", ""),
            geo_scope=draft.get("geo_scope", ""),
        )
        await ghost.reset_state(uid)
        if not node:
            # Casi siempre: SYNERGIX insuficiente para el bond (nombre ya validado).
            msg = await _bond_insufficient_msg(uid, lang) or t("node_name_too_short", lang)
            await callback.message.edit_text(msg)
        else:
            await callback.message.edit_text(
                t("node_created", lang, icon=node.icon, name=html.escape(node.name),
                  node_id=node.node_id, bond=f"{bonds_svc.BOND_AMOUNT:,.0f}"),
            )
            await callback.message.answer(
                await _render_node_view(node, lang),
                reply_markup=get_node_view_kb(lang, node.node_id, True, True,
                                              {"bond-status": "locked"}),
            )

    elif action == "join":
        node = await nodes_svc.get_node(arg)
        if not node:
            await callback.message.edit_text(t("node_not_found", lang), reply_markup=get_nodes_menu_kb(lang))
        elif await nodes_svc.is_member(arg, uid):
            await callback.answer(t("node_already_member", lang, name=node.name))
            return
        else:
            await nodes_svc.join_node(uid, arg)
            await callback.message.edit_text(
                t("node_joined", lang, name=html.escape(node.name)),
                reply_markup=get_node_view_kb(lang, arg, True),
            )

    elif action == "leave":
        node = await nodes_svc.get_node(arg)
        await nodes_svc.leave_node(uid, arg)
        name = html.escape(node.name) if node else arg
        await callback.message.edit_text(t("node_left", lang, name=name), reply_markup=get_nodes_menu_kb(lang))

    elif action == "use":
        node = await nodes_svc.get_node(arg)
        if node:
            await nodes_svc.set_active_node(uid, arg)
            await callback.answer(t("node_set_active", lang, name=node.name))
            return

    await callback.answer()


# ══════════════════════════════════════════════════════════════════════════
# FASE 2: PROVEEDORES · FINANCIAMIENTO COLECTIVO · JUECES ORÁCULOS
# ══════════════════════════════════════════════════════════════════════════

async def _active_node_id(uid: int) -> Optional[str]:
    profile = await get_identity_manager().get_profile(uid)
    return profile.active_node


def _prov_label(key: str, lang: str) -> str:
    icon = providers_svc.PROVIDER_CATEGORIES.get(key, "🧰")
    return f"{icon} {t('prov_' + key, lang)}"


# ── Proveedores ─────────────────────────────────────────────────────────────

@dp.message(Command("proveedores"))
async def cmd_providers(message: Message) -> None:
    if not message.from_user:
        return
    uid = message.from_user.id
    _known_uids.add(uid)
    lang = await get_user_language(uid)
    await get_ghost_state_manager().reset_state(uid)

    node_id = await _active_node_id(uid)
    if not node_id:
        await message.answer(t("need_active_node", lang))
        return

    provs = await providers_svc.get_providers(node_id)
    text = t("providers_header", lang) + "\n\n"
    rows: list = []
    if not provs:
        text += t("providers_empty", lang)
    else:
        from aisynergix.bot.identity import _hash_uid
        me = _hash_uid(uid)
        for p in provs:
            text += t(
                "provider_entry", lang,
                icon=providers_svc.PROVIDER_CATEGORIES.get(p.get("category", ""), "🧰"),
                category=t("prov_" + p.get("category", "otros"), lang),
                desc=html.escape(p.get("description", "")),
            ) + "\n"
            phash = p.get("uid-hash", "")
            if phash and phash != me:  # no ofrecer pagarse a uno mismo
                rows.append([InlineKeyboardButton(
                    text=t("btn_provider_pay", lang,
                           category=t("prov_" + p.get("category", "otros"), lang)),
                    callback_data=f"prov:pay:{phash}")])
    rows.append([InlineKeyboardButton(
        text=t("btn_provider_register", lang), callback_data="prov:register")])
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@dp.callback_query(F.data.startswith("prov:"))
async def handle_provider_callback(callback: CallbackQuery) -> None:
    if not callback.from_user or not callback.data or not callback.message:
        return
    uid = callback.from_user.id
    lang = await get_user_language(uid)
    parts = callback.data.split(":")
    action = parts[1] if len(parts) > 1 else ""

    if action == "register":
        node_id = await _active_node_id(uid)
        if not node_id:
            await callback.answer(t("need_active_node", lang), show_alert=True)
            return
        rows, row = [], []
        for key in providers_svc.PROVIDER_CATEGORIES:
            row.append(InlineKeyboardButton(text=_prov_label(key, lang), callback_data=f"prov:cat:{key}"))
            if len(row) == 2:
                rows.append(row); row = []
        if row:
            rows.append(row)
        await callback.message.edit_text(
            t("provider_ask_category", lang),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )

    elif action == "cat":
        category = parts[2] if len(parts) > 2 else ""
        node_id = await _active_node_id(uid)
        if not node_id or category not in providers_svc.PROVIDER_CATEGORIES:
            await callback.answer(); return
        ghost = get_ghost_state_manager()
        cache = get_l1_cache()
        await ghost.set_state(uid, "awaiting_provider_desc")
        await cache.set_state_data(uid, {"prov_cat": category, "prov_node": node_id})
        await callback.message.edit_text(t("provider_ask_desc", lang))

    elif action == "pay":
        provider_hash = parts[2] if len(parts) > 2 else ""
        node_id = await _active_node_id(uid)
        if not node_id or not provider_hash:
            await callback.answer(t("need_active_node", lang), show_alert=True)
            return
        ghost = get_ghost_state_manager()
        cache = get_l1_cache()
        await ghost.set_state(uid, "awaiting_provider_payment")
        await cache.set_state_data(
            uid, {"pay_hash": provider_hash, "pay_node": node_id})
        await callback.message.answer(t("provider_ask_amount", lang))

    await callback.answer()


async def handle_provider_desc_message(message: Message, uid: int, text: str, lang: str) -> None:
    ghost = get_ghost_state_manager()
    cache = get_l1_cache()
    draft = await cache.get_state_data(uid) or {}
    category = draft.get("prov_cat", "")
    node_id = draft.get("prov_node", "")
    await ghost.reset_state(uid)

    err = await providers_svc.register_provider(uid, node_id, category, text)
    if err == "not_verified":
        await message.answer(t("provider_need_verified", lang))
    elif err == "not_member":
        await message.answer(t("need_active_node", lang))
    elif err == "bad_input":
        await message.answer(t("provider_bad_desc", lang))
    else:
        await message.answer(t("provider_registered", lang, category=_prov_label(category, lang)))


async def handle_provider_payment_message(message: Message, uid: int, text: str, lang: str) -> None:
    ghost = get_ghost_state_manager()
    cache = get_l1_cache()
    draft = await cache.get_state_data(uid) or {}
    provider_hash = draft.get("pay_hash", "")
    node_id = draft.get("pay_node", "")
    await ghost.reset_state(uid)

    from aisynergix.services.rewards import parse_amount, is_valid_amount
    amount = parse_amount(text)
    if amount is None or not is_valid_amount(amount) or amount <= 0:
        await message.answer(t("provider_pay_bad_amount", lang))
        return

    err = await providers_svc.pay_provider(uid, node_id, provider_hash, amount)
    if err == "bad_amount":
        await message.answer(t("provider_pay_bad_amount", lang))
    elif err == "not_provider":
        await message.answer(t("provider_pay_not_provider", lang))
    elif err == "self_payment":
        await message.answer(t("provider_pay_self", lang))
    elif err == "insufficient":
        await message.answer(t("provider_pay_insufficient", lang))
    else:
        await message.answer(t("provider_pay_ok", lang, amount=f"{amount:,.2f}"))


# ── Financiamiento colectivo ────────────────────────────────────────────────

_PROJECT_STATUS_ICON = {
    "active": "🟢", "voting": "🗳️", "completed": "✅", "refunded": "↩️",
}


async def _project_view(callback: CallbackQuery, project_id: str, lang: str, uid: int) -> None:
    # Resolución perezosa de votaciones expiradas antes de mostrar.
    await projects_svc.check_and_resolve(project_id)
    proj = await projects_svc.get_project(project_id)
    if not proj:
        await callback.message.edit_text(t("project_not_found", lang))
        return
    uid_hash = _hash_uid_bot(uid)
    text = t(
        "project_view", lang,
        icon=_PROJECT_STATUS_ICON.get(proj["status"], "🟢"),
        title=html.escape(proj["title"]),
        status=t("project_status_" + proj["status"], lang),
        raised=f"{proj['raised']:.0f}",
        goal=f"{proj['goal']:.0f}",
        funders=len(proj["funders"]),
    )
    # Al liberar, los fondos van PRIMERO al creador/propietario (§ prioridad).
    text += "\n" + t("project_beneficiary_note", lang)
    # Evidencia de cumplimiento visible para que los financiadores verifiquen
    # ANTES de votar la liberación (verificación obligatoria).
    evidence = (proj.get("evidence", "") or "").strip()
    if evidence and proj["status"] in ("voting", "completed"):
        text += "\n\n" + t("project_evidence_shown", lang, evidence=html.escape(evidence))
    rows = []
    if proj["status"] == "active":
        rows.append([InlineKeyboardButton(text=t("btn_project_fund", lang), callback_data=f"proj:fund:{project_id}")])
        if proj["creator"] == uid_hash and proj["raised"] > 0:
            rows.append([InlineKeyboardButton(text=t("btn_project_done", lang), callback_data=f"proj:done:{project_id}")])
    elif proj["status"] == "voting" and uid_hash in proj["funders"]:
        rows.append([
            InlineKeyboardButton(text=t("btn_project_vote_yes", lang), callback_data=f"proj:vote:{project_id}:1"),
            InlineKeyboardButton(text=t("btn_project_vote_no", lang), callback_data=f"proj:vote:{project_id}:0"),
        ])
    await callback.message.edit_text(
        text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows) if rows else None,
    )


def _hash_uid_bot(uid: int) -> str:
    from aisynergix.bot.identity import _hash_uid
    return _hash_uid(uid)


@dp.message(Command("proyectos"))
async def cmd_projects(message: Message) -> None:
    if not message.from_user:
        return
    uid = message.from_user.id
    _known_uids.add(uid)
    lang = await get_user_language(uid)
    await get_ghost_state_manager().reset_state(uid)

    node_id = await _active_node_id(uid)
    if not node_id:
        await message.answer(t("need_active_node", lang))
        return

    records = await projects_svc.list_projects(node_id)
    rows = []
    for r in records[:10]:
        pid = r.get("project-id", "")
        icon = _PROJECT_STATUS_ICON.get(r.get("project-status", "active"), "🟢")
        rows.append([InlineKeyboardButton(
            text=f"{icon} {r.get('title', pid)}", callback_data=f"proj:view:{pid}",
        )])
    rows.append([InlineKeyboardButton(text=t("btn_project_create", lang), callback_data="proj:create")])
    text = t("projects_header", lang) if records else t("projects_empty", lang)
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@dp.callback_query(F.data.startswith("proj:"))
async def handle_project_callback(callback: CallbackQuery) -> None:
    if not callback.from_user or not callback.data or not callback.message:
        return
    uid = callback.from_user.id
    lang = await get_user_language(uid)
    ghost = get_ghost_state_manager()
    cache = get_l1_cache()
    parts = callback.data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    pid = parts[2] if len(parts) > 2 else ""

    if action == "create":
        node_id = await _active_node_id(uid)
        if not node_id:
            await callback.answer(t("need_active_node", lang), show_alert=True)
            return
        await ghost.set_state(uid, "awaiting_project_name")
        await cache.set_state_data(uid, {"proj_node": node_id})
        await callback.message.edit_text(t("project_ask_name", lang))

    elif action == "view":
        await _project_view(callback, pid, lang, uid)

    elif action == "fund":
        await ghost.set_state(uid, "awaiting_project_fund")
        await cache.set_state_data(uid, {"proj_id": pid})
        await callback.message.edit_text(
            t("project_fund_ask", lang, min=int(projects_svc.FUND_MIN_SYNX))
        )

    elif action == "done":
        # Verificación obligatoria: el creador debe adjuntar evidencia de
        # cumplimiento (hitos/documentación) antes de abrir la votación.
        await ghost.set_state(uid, "awaiting_project_evidence")
        await cache.set_state_data(uid, {"proj_id": pid})
        await callback.message.edit_text(t(
            "project_ask_evidence", lang,
            min=projects_svc.MIN_EVIDENCE_LEN, max=projects_svc.MAX_EVIDENCE_LEN,
        ))

    elif action == "vote":
        approve = (parts[3] if len(parts) > 3 else "0") == "1"
        result = await projects_svc.vote_project(uid, pid, approve)
        if result == "not_voting":
            await callback.answer(t("project_not_active", lang), show_alert=True)
            return
        if result == "not_funder":
            await callback.answer(t("project_only_funders", lang), show_alert=True)
            return
        if result == "completed":
            await callback.message.edit_text(t("project_released", lang))
        elif result == "refunded":
            await callback.message.edit_text(t("project_refunded", lang))
        else:
            await callback.answer(t("project_vote_recorded", lang))
            return

    await callback.answer()


async def handle_project_name_message(message: Message, uid: int, text: str, lang: str) -> None:
    cache = get_l1_cache()
    draft = await cache.get_state_data(uid) or {}
    title = text.strip()
    if not (projects_svc.MIN_TITLE_LEN <= len(title) <= projects_svc.MAX_TITLE_LEN):
        await message.answer(t("project_invalid_name", lang))
        return
    draft["proj_title"] = title
    await get_ghost_state_manager().set_state(uid, "awaiting_project_goal")
    await cache.set_state_data(uid, draft)
    await message.answer(t("project_ask_goal", lang, min=int(projects_svc.GOAL_MIN_SYNX)))


async def handle_project_goal_message(message: Message, uid: int, text: str, lang: str) -> None:
    ghost = get_ghost_state_manager()
    cache = get_l1_cache()
    draft = await cache.get_state_data(uid) or {}
    from aisynergix.services.rewards import parse_amount
    goal = parse_amount(text)
    if goal is None:
        await message.answer(t("invalid_amount", lang))
        return
    if goal < projects_svc.GOAL_MIN_SYNX:
        await message.answer(t("project_ask_goal", lang, min=int(projects_svc.GOAL_MIN_SYNX)))
        return
    await ghost.reset_state(uid)
    proj = await projects_svc.create_project(
        uid, draft.get("proj_node", ""), draft.get("proj_title", ""), goal,
    )
    if not proj:
        await message.answer(t("project_create_failed", lang))
        return
    await message.answer(t(
        "project_created", lang,
        title=html.escape(proj["title"]), goal=f"{goal:.0f}",
    ))


async def handle_project_evidence_message(message: Message, uid: int, text: str, lang: str) -> None:
    ghost = get_ghost_state_manager()
    cache = get_l1_cache()
    draft = await cache.get_state_data(uid) or {}
    pid = draft.get("proj_id", "")
    await ghost.reset_state(uid)
    err = await projects_svc.request_completion(uid, pid, text)
    if err == "bad_evidence":
        await message.answer(t(
            "project_evidence_bad", lang,
            min=projects_svc.MIN_EVIDENCE_LEN, max=projects_svc.MAX_EVIDENCE_LEN,
        ))
    elif err == "not_creator":
        await message.answer(t("project_only_creator", lang))
    elif err in ("not_active", "no_funds"):
        await message.answer(t("project_not_active", lang))
    else:
        await message.answer(
            t("project_voting_started", lang, hours=projects_svc.VOTING_WINDOW_H))


async def handle_project_fund_message(message: Message, uid: int, text: str, lang: str) -> None:
    ghost = get_ghost_state_manager()
    cache = get_l1_cache()
    draft = await cache.get_state_data(uid) or {}
    pid = draft.get("proj_id", "")
    from aisynergix.services.rewards import parse_amount
    amount = parse_amount(text)
    if amount is None:
        await message.answer(t("invalid_amount", lang))
        return
    await ghost.reset_state(uid)
    err = await projects_svc.fund_project(uid, pid, amount)
    if err == "insufficient":
        await message.answer(t("project_insufficient", lang))
    elif err == "bad_amount":
        await message.answer(t("project_fund_ask", lang, min=int(projects_svc.FUND_MIN_SYNX)))
    elif err == "not_active":
        await message.answer(t("project_not_active", lang))
    else:
        await message.answer(t("project_funded", lang, amount=f"{amount:.0f}"))


# ── Bounties de Conocimiento (Proof-of-Knowledge) ───────────────────────────

_BOUNTY_STATUS_ICON = {"open": "🎯", "closed": "✅", "expired": "⌛"}


def _bounty_row_label(b: dict, lang: str) -> str:
    icon = _BOUNTY_STATUS_ICON.get(b.get("bounty-status", "open"), "🎯")
    topic = _topic_label(b.get("topic", ""), lang)
    rem = bounties_svc.remaining_pool(b)
    return f"{icon} {topic} · {rem:.0f} SYNX"


async def _bounty_view(callback: CallbackQuery, bounty_id: str, lang: str) -> None:
    from aisynergix.services.irys import read_bounty
    b = await read_bounty(bounty_id)
    if not b:
        await callback.message.edit_text(t("bounty_not_found", lang))
        return
    now = int(datetime.now(timezone.utc).timestamp())
    deadline = int(b.get("deadline", 0) or 0)
    date = datetime.fromtimestamp(deadline, tz=timezone.utc).strftime("%Y-%m-%d") if deadline else "—"
    status_key = b.get("bounty-status", "open")
    if status_key == "open" and not bounties_svc.is_open(b, now):
        status_key = "expired"
    text = t(
        "bounty_view", lang,
        topic=_topic_label(b.get("topic", ""), lang),
        status=t("bounty_status_" + status_key, lang),
        reward=f"{float(b.get('reward', 0)):.0f}",
        remaining=f"{bounties_svc.remaining_pool(b):.0f}",
        pool=f"{float(b.get('pool', 0)):.0f}",
        paid=int(b.get("paid-count", 0) or 0),
        slots=bounties_svc.max_recipients(b),
        date=date,
    )
    await callback.message.edit_text(text)


@dp.message(Command("bounties"))
async def cmd_bounties(message: Message) -> None:
    if not message.from_user:
        return
    uid = message.from_user.id
    _known_uids.add(uid)
    lang = await get_user_language(uid)
    await get_ghost_state_manager().reset_state(uid)

    node_id = await _active_node_id(uid)
    if not node_id:
        await message.answer(t("need_active_node", lang))
        return

    records = await bounties_svc.list_bounties(node_id)
    records.sort(key=lambda b: (not b.get("open"), b.get("topic", "")))
    rows = []
    for b in records[:10]:
        rows.append([InlineKeyboardButton(
            text=_bounty_row_label(b, lang),
            callback_data=f"bounty:view:{b.get('bounty-id', '')}",
        )])
    rows.append([InlineKeyboardButton(
        text=t("btn_bounty_create", lang), callback_data="bounty:create")])
    text = t("bounties_header", lang) if records else t("bounties_empty", lang)
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@dp.callback_query(F.data.startswith("bounty:"))
async def handle_bounty_callback(callback: CallbackQuery) -> None:
    if not callback.from_user or not callback.data or not callback.message:
        return
    uid = callback.from_user.id
    lang = await get_user_language(uid)
    ghost = get_ghost_state_manager()
    cache = get_l1_cache()
    parts = callback.data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    arg = parts[2] if len(parts) > 2 else ""

    if action == "create":
        node_id = await _active_node_id(uid)
        if not node_id:
            await callback.answer(t("need_active_node", lang), show_alert=True)
            return
        node = await nodes_svc.get_node(node_id)
        topics = node.topics if node else []
        if not topics:
            await callback.answer(t("bounty_no_topics", lang), show_alert=True)
            return
        rows, row = [], []
        for key in topics:
            row.append(InlineKeyboardButton(
                text=_topic_label(key, lang), callback_data=f"bounty:topic:{key}"))
            if len(row) == 2:
                rows.append(row); row = []
        if row:
            rows.append(row)
        await callback.message.edit_text(
            t("bounty_ask_topic", lang),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )

    elif action == "topic":
        node_id = await _active_node_id(uid)
        if not node_id or not arg:
            await callback.answer(); return
        await ghost.set_state(uid, "awaiting_bounty_pool")
        await cache.set_state_data(uid, {"bty_node": node_id, "bty_topic": arg})
        await callback.message.edit_text(
            t("bounty_ask_pool", lang,
              min=int(bounties_svc.BOUNTY_MIN_POOL),
              reward=int(bounties_svc.REWARD_MIN),
              days=bounties_svc.DEFAULT_DURATION_DAYS)
        )

    elif action == "view":
        await _bounty_view(callback, arg, lang)

    await callback.answer()


async def handle_bounty_pool_message(message: Message, uid: int, text: str, lang: str) -> None:
    ghost = get_ghost_state_manager()
    cache = get_l1_cache()
    draft = await cache.get_state_data(uid) or {}
    node_id = draft.get("bty_node", "")
    topic = draft.get("bty_topic", "")
    await ghost.reset_state(uid)

    from aisynergix.services.rewards import parse_amount, is_valid_amount
    pool = parse_amount(text)
    if pool is None or not is_valid_amount(pool):
        await message.answer(t("invalid_amount", lang))
        return
    bounty_id, err = await bounties_svc.create_bounty(uid, node_id, topic, pool)
    if err == "not_member":
        await message.answer(t("need_active_node", lang))
    elif err == "pool_too_small":
        await message.answer(t("bounty_pool_too_small", lang,
                               min=int(bounties_svc.BOUNTY_MIN_POOL)))
    elif err in ("bad_reward", "bad_duration"):
        await message.answer(t("bounty_bad_params", lang))
    elif err == "insufficient":
        await message.answer(t("bounty_insufficient", lang))
    else:
        await message.answer(t(
            "bounty_created", lang,
            topic=_topic_label(topic, lang),
            pool=f"{pool:.0f}",
            reward=int(bounties_svc.REWARD_MIN),
            days=bounties_svc.DEFAULT_DURATION_DAYS,
        ))


# ── Atlas de Problemas y Soluciones ─────────────────────────────────────────

from aisynergix.services import problems as problems_svc

_PROBLEM_STATUS_ICON = {"open": "🚩", "solving": "🛠️", "solved": "✅"}


async def _problem_view(callback: CallbackQuery, pid: str, lang: str, uid: int) -> None:
    from aisynergix.services.irys import read_problem, list_problem_confirms
    record = await read_problem(pid)
    if not record:
        await callback.message.edit_text(t("problem_not_found", lang))
        return
    status = problems_svc.status_of(record)
    confirms = problems_svc.distinct_confirmers(
        await list_problem_confirms(pid, kind="problem"))
    text = t(
        "problem_view", lang,
        icon=_PROBLEM_STATUS_ICON.get(status, "🚩"),
        text=html.escape(record.get("problem-text", "")),
        status=t("problem_status_" + status, lang),
        confirms=confirms,
    )
    solution = (record.get("solution", "") or "").strip()
    if solution:
        text += "\n\n" + t("problem_solution_line", lang, solution=html.escape(solution))
    uid_hash = _hash_uid_bot(uid)
    rows = []
    if status != "solved":
        if record.get("reporter", "") != uid_hash:
            rows.append([InlineKeyboardButton(
                text=t("btn_problem_confirm", lang), callback_data=f"prob:confirm:{pid}")])
        rows.append([InlineKeyboardButton(
            text=t("btn_problem_solve", lang), callback_data=f"prob:solve:{pid}")])
    if status == "solving" and record.get("solver", "") != uid_hash:
        rows.append([InlineKeyboardButton(
            text=t("btn_solution_works", lang, needed=problems_svc.SOLVED_THRESHOLD),
            callback_data=f"prob:works:{pid}")])
    await callback.message.edit_text(
        text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows) if rows else None,
    )


@dp.message(Command("problemas"))
async def cmd_problems(message: Message) -> None:
    if not message.from_user:
        return
    uid = message.from_user.id
    _known_uids.add(uid)
    lang = await get_user_language(uid)
    await get_ghost_state_manager().reset_state(uid)

    node_id = await _active_node_id(uid)
    if not node_id:
        await message.answer(t("need_active_node", lang))
        return

    records = await problems_svc.list_problems(node_id)
    order = {"open": 0, "solving": 1, "solved": 2}
    records.sort(key=lambda r: order.get(r.get("problem-status", "open"), 0))
    rows = []
    for r in records[:10]:
        pid = r.get("problem-id", "")
        icon = _PROBLEM_STATUS_ICON.get(r.get("problem-status", "open"), "🚩")
        preview = (r.get("problem-text", "") or "")[:40]
        rows.append([InlineKeyboardButton(
            text=f"{icon} {preview}", callback_data=f"prob:view:{pid}")])
    rows.append([InlineKeyboardButton(
        text=t("btn_problem_report", lang), callback_data="prob:report")])
    text = t("problems_atlas_header", lang) if records else t("problems_atlas_empty", lang)
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@dp.callback_query(F.data.startswith("prob:"))
async def handle_problem_callback(callback: CallbackQuery) -> None:
    if not callback.from_user or not callback.data or not callback.message:
        return
    uid = callback.from_user.id
    lang = await get_user_language(uid)
    ghost = get_ghost_state_manager()
    cache = get_l1_cache()
    parts = callback.data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    pid = parts[2] if len(parts) > 2 else ""

    if action == "report":
        node_id = await _active_node_id(uid)
        if not node_id:
            await callback.answer(t("need_active_node", lang), show_alert=True)
            return
        await ghost.set_state(uid, "awaiting_problem_text")
        await cache.set_state_data(uid, {"prob_node": node_id})
        await callback.message.edit_text(t(
            "problem_ask_text", lang,
            min=problems_svc.MIN_TEXT_LEN, max=problems_svc.MAX_TEXT_LEN,
        ))

    elif action == "view":
        await _problem_view(callback, pid, lang, uid)

    elif action == "confirm":
        count, err = await problems_svc.confirm_problem(uid, pid)
        if err == "own_problem":
            await callback.answer(t("problem_own", lang), show_alert=True)
            return
        if err == "not_member":
            await callback.answer(t("need_active_node", lang), show_alert=True)
            return
        if err in ("not_found", "closed"):
            await callback.answer(t("problem_closed", lang))
            return
        await callback.answer(t("problem_confirmed", lang, count=count))
        return

    elif action == "solve":
        await ghost.set_state(uid, "awaiting_solution_text")
        await cache.set_state_data(uid, {"prob_id": pid})
        await callback.message.edit_text(t(
            "problem_ask_solution", lang,
            min=problems_svc.MIN_TEXT_LEN, max=problems_svc.MAX_TEXT_LEN,
        ))

    elif action == "works":
        status, err = await problems_svc.confirm_solution(uid, pid)
        if err == "own_solution":
            await callback.answer(t("solution_own", lang), show_alert=True)
            return
        if err == "not_member":
            await callback.answer(t("need_active_node", lang), show_alert=True)
            return
        if err in ("not_found", "not_solving"):
            await callback.answer(t("problem_closed", lang))
            return
        if status == "solved":
            await callback.message.edit_text(t(
                "problem_solved", lang,
                reward=int(problems_svc.SOLVER_REWARD_SYNX),
            ))
        else:
            await callback.answer(t(
                "solution_vote_recorded", lang,
                needed=problems_svc.SOLVED_THRESHOLD,
            ))
        return

    await callback.answer()


async def handle_problem_text_message(message: Message, uid: int, text: str, lang: str) -> None:
    ghost = get_ghost_state_manager()
    cache = get_l1_cache()
    draft = await cache.get_state_data(uid) or {}
    node_id = draft.get("prob_node", "")
    await ghost.reset_state(uid)

    pid, err = await problems_svc.report_problem(uid, node_id, text)
    if err == "bad_text":
        await message.answer(t(
            "problem_bad_text", lang,
            min=problems_svc.MIN_TEXT_LEN, max=problems_svc.MAX_TEXT_LEN,
        ))
        return
    if err == "not_member":
        await message.answer(t("need_active_node", lang))
        return
    if err == "daily_cap":
        await message.answer(t("academy_daily_cap", lang, cap=problems_svc.REPORTS_DAILY_CAP))
        return
    await message.answer(t("problem_reported", lang))

    # Atlas translingüe: ¿la red ya conoce este problema (en cualquier idioma)?
    try:
        matches = await problems_svc.find_similar(text, lang)
    except Exception:
        matches = []
    if matches:
        out = t("atlas_related_header", lang) + "\n"
        for m in matches:
            out += "\n" + t("atlas_entry", lang, excerpt=html.escape(m["excerpt"]))
        await message.answer(out)


async def handle_solution_text_message(message: Message, uid: int, text: str, lang: str) -> None:
    ghost = get_ghost_state_manager()
    cache = get_l1_cache()
    draft = await cache.get_state_data(uid) or {}
    pid = draft.get("prob_id", "")
    await ghost.reset_state(uid)

    err = await problems_svc.propose_solution(uid, pid, text)
    if err == "bad_text":
        await message.answer(t(
            "problem_bad_text", lang,
            min=problems_svc.MIN_TEXT_LEN, max=problems_svc.MAX_TEXT_LEN,
        ))
    elif err == "not_member":
        await message.answer(t("need_active_node", lang))
    elif err in ("not_found", "already_solved"):
        await message.answer(t("problem_closed", lang))
    else:
        await message.answer(t(
            "solution_proposed", lang, needed=problems_svc.SOLVED_THRESHOLD,
        ))


# ── Synergix Academy: aprende y gana ────────────────────────────────────────

from aisynergix.services import academy as academy_svc


@dp.message(Command("aprender"))
async def cmd_learn(message: Message) -> None:
    if not message.from_user:
        return
    uid = message.from_user.id
    _known_uids.add(uid)
    lang = await get_user_language(uid)
    await get_ghost_state_manager().reset_state(uid)

    if not academy_svc.can_take_lesson(uid):
        await message.answer(t("academy_daily_cap", lang, cap=academy_svc.DAILY_LESSON_CAP))
        return
    rows, row = [], []
    for key in nodes_svc.TOPICS:
        row.append(InlineKeyboardButton(
            text=_topic_label(key, lang), callback_data=f"learn:topic:{key}"))
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)
    await message.answer(
        t("academy_intro", lang, reward=int(academy_svc.LESSON_REWARD_SYNX)),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@dp.callback_query(F.data.startswith("learn:"))
async def handle_learn_callback(callback: CallbackQuery) -> None:
    if not callback.from_user or not callback.data or not callback.message:
        return
    uid = callback.from_user.id
    lang = await get_user_language(uid)
    parts = callback.data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    topic_key = parts[2] if len(parts) > 2 else ""

    if action == "topic" and topic_key in nodes_svc.TOPICS:
        if not academy_svc.can_take_lesson(uid):
            await callback.answer(
                t("academy_daily_cap", lang, cap=academy_svc.DAILY_LESSON_CAP),
                show_alert=True,
            )
            return
        await callback.answer()  # responder ya; la generación puede tardar
        await callback.message.edit_text(t("academy_generating", lang))
        try:
            lesson = await academy_svc.generate_lesson(
                topic_key, _topic_label(topic_key, lang), lang,
            )
        except Exception as exc:
            logger.warning("academy: generación falló (%s): %s", topic_key, exc)
            lesson = None
        if not lesson:
            await callback.message.edit_text(t("academy_no_knowledge", lang))
            return
        ghost = get_ghost_state_manager()
        cache = get_l1_cache()
        await ghost.set_state(uid, "awaiting_lesson_answer")
        await cache.set_state_data(uid, {
            "lesson_topic": topic_key,
            # tuplas → listas para el estado en cache
            "lesson_citations": [list(c) for c in lesson["citations"]],
        })
        await callback.message.edit_text(t(
            "academy_lesson", lang,
            topic=_topic_label(topic_key, lang),
            lesson=html.escape(lesson["lesson"]),
            reward=int(academy_svc.LESSON_REWARD_SYNX),
        ))
        return

    await callback.answer()


async def handle_lesson_answer_message(message: Message, uid: int, text: str, lang: str) -> None:
    ghost = get_ghost_state_manager()
    cache = get_l1_cache()
    draft = await cache.get_state_data(uid) or {}
    topic_key = draft.get("lesson_topic", "")
    citations = [tuple(c) for c in draft.get("lesson_citations", [])]

    if len(text.strip()) < academy_svc.MIN_ANSWER_LEN:
        # No consumir la lección: dejar que desarrolle la respuesta.
        await message.answer(t("academy_answer_too_short", lang))
        return

    await ghost.reset_state(uid)
    result = await academy_svc.submit_answer(uid, topic_key, text, citations)
    status = result.get("status")
    if status == "too_short":
        await message.answer(t("academy_answer_too_short", lang))
    elif status == "daily_cap":
        await message.answer(t("academy_daily_cap", lang, cap=academy_svc.DAILY_LESSON_CAP))
    elif status == "fail":
        feedback = result.get("feedback") or ""
        await message.answer(t(
            "academy_fail", lang,
            score=result.get("score", 0), feedback=html.escape(feedback),
        ))
    else:  # pass
        out = t(
            "academy_pass", lang,
            score=result.get("score", 0),
            synx=f"{result.get('reward', 0):.0f}",
            topic=_topic_label(topic_key, lang),
            lessons=result.get("lessons", 1),
            level=result.get("level", 0),
        )
        if result.get("authors_credited"):
            out += "\n" + t("academy_authors_note", lang)
        await message.answer(out)
        if result.get("leveled_up"):
            await message.answer(t(
                "academy_levelup", lang,
                level=result.get("level", 1),
                topic=_topic_label(topic_key, lang),
            ))


# ── Jueces Oráculos ─────────────────────────────────────────────────────────

@dp.message(Command("oraculo"))
async def cmd_oracle(message: Message) -> None:
    if not message.from_user:
        return
    uid = message.from_user.id
    _known_uids.add(uid)
    lang = await get_user_language(uid)
    await get_ghost_state_manager().reset_state(uid)

    stake = await oracle_svc.get_stake(_hash_uid_bot(uid))
    if stake:
        text = t(
            "oracle_menu_staked", lang,
            amount=f"{float(stake.get('amount', 0) or 0):.0f}",
            reward=int(oracle_svc.VOTE_REWARD_SYNX),
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=t("btn_oracle_pending", lang), callback_data="orc:pending")],
            [InlineKeyboardButton(text=t("btn_oracle_unstake", lang), callback_data="orc:unstake")],
        ])
    else:
        text = t(
            "oracle_menu_intro", lang,
            stake=int(oracle_svc.STAKE_SYNERGIX),
            points=oracle_svc.ELIGIBLE_MIN_POINTS,
            reward=int(oracle_svc.VOTE_REWARD_SYNX),
            penalty=int(oracle_svc.PENALTY_SYNX),
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text=t("btn_oracle_stake", lang, stake=int(oracle_svc.STAKE_SYNERGIX)),
                callback_data="orc:stake",
            ),
        ]])
    await message.answer(text, reply_markup=kb)


@dp.callback_query(F.data.startswith("orc:"))
async def handle_oracle_callback(callback: CallbackQuery) -> None:
    if not callback.from_user or not callback.data or not callback.message:
        return
    uid = callback.from_user.id
    lang = await get_user_language(uid)
    parts = callback.data.split(":")
    action = parts[1] if len(parts) > 1 else ""

    if action == "stake":
        err = await oracle_svc.stake(uid)
        if err == "not_eligible":
            await callback.answer(
                t("oracle_not_eligible", lang, points=oracle_svc.ELIGIBLE_MIN_POINTS),
                show_alert=True,
            )
            return
        if err == "already_staked":
            await callback.answer(t("oracle_already_staked", lang))
            return
        if err == "insufficient":
            await callback.answer(
                t("oracle_insufficient", lang, stake=int(oracle_svc.STAKE_SYNERGIX)),
                show_alert=True,
            )
            return
        await callback.message.edit_text(t("oracle_stake_ok", lang))

    elif action == "unstake":
        err = await oracle_svc.unstake(uid)
        if err == "not_staked":
            await callback.answer(t("oracle_not_staked", lang))
            return
        await callback.message.edit_text(t("oracle_unstake_ok", lang))

    elif action == "pending":
        reviews = await oracle_svc.pending_reviews(limit=5)
        if not reviews:
            await callback.message.edit_text(t("oracle_no_pending", lang))
        else:
            from aisynergix.services.irys import read_aporte
            await callback.message.edit_text(t("oracle_pending_header", lang, count=len(reviews)))
            for review in reviews:
                tx = review.get("aporte-tx", "")
                preview = ""
                try:
                    texto, _tags = await read_aporte(tx)
                    preview = texto[:250] + ("…" if len(texto) > 250 else "")
                except Exception:
                    preview = tx
                kb = InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="👍", callback_data=f"orc:v:{tx}:1"),
                    InlineKeyboardButton(text="👎", callback_data=f"orc:v:{tx}:0"),
                ]])
                await callback.message.answer(
                    t("oracle_review_entry", lang, preview=html.escape(preview)),
                    reply_markup=kb,
                )

    elif action == "v":
        tx = parts[2] if len(parts) > 2 else ""
        approve = (parts[3] if len(parts) > 3 else "0") == "1"
        result = await oracle_svc.vote(uid, tx, approve)
        if result == "not_oracle":
            await callback.answer(t("oracle_not_staked", lang), show_alert=True)
            return
        if result == "own_aporte":
            await callback.answer(t("oracle_own_aporte", lang), show_alert=True)
            return
        if result == "not_pending":
            await callback.answer(t("oracle_review_closed", lang))
        elif result in ("approved", "rejected"):
            await callback.message.edit_text(t(f"oracle_resolved_{result}", lang))
        else:
            await callback.answer(t("oracle_vote_recorded", lang))
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    await callback.answer()


# ══════════════════════════════════════════════════════════════════════════
# FASE 3: PASSPORT · AGENTE · GOBERNANZA
# ══════════════════════════════════════════════════════════════════════════

@dp.message(Command("pasaporte"))
async def cmd_passport(message: Message) -> None:
    """Synergix Passport (§10): reputación verificable sellada en Arweave."""
    if not message.from_user:
        return
    uid = message.from_user.id
    _known_uids.add(uid)
    lang = await get_user_language(uid)
    await get_ghost_state_manager().reset_state(uid)

    notice = await message.answer(t("passport_building", lang))
    result = await passport_svc.build_passport(uid)
    try:
        await notice.delete()
    except Exception:
        pass
    if not result:
        await message.answer(t("passport_failed", lang))
        return
    data = result["data"]
    impacts = data.get("impacts", {})
    oracle_info = data.get("oracle", {})
    accuracy = oracle_info.get("accuracy")
    text = t(
        "passport_view", lang,
        ghost_id=data.get("ghost_id", ""),
        rank=_t_rank(data.get("rank", "🌱 Iniciado"), lang),
        points=data.get("points", 0),
        contributions=data.get("contributions", 0),
        avg_score=data.get("avg_quality_score") or "—",
        views=impacts.get("views", 0),
        useful=impacts.get("useful", 0),
        references=impacts.get("references", 0),
        synx_earned=f"{data.get('synx_earned', 0):.0f}",
        oracle_accuracy=(f"{accuracy * 100:.0f}%" if accuracy is not None else "—"),
        url=f"{IRYS_GATEWAY_URL}/{result['tx']}",
    )
    if data.get("human_verified"):
        text += "\n" + t("status_verified", lang)
    await message.answer(text)


# ── Gobernanza ──────────────────────────────────────────────────────────────

_PROPOSAL_STATUS_ICON = {"open": "🗳️", "approved": "✅", "rejected": "❌"}


@dp.message(Command("propuestas"))
async def cmd_proposals(message: Message) -> None:
    if not message.from_user:
        return
    uid = message.from_user.id
    _known_uids.add(uid)
    lang = await get_user_language(uid)
    await get_ghost_state_manager().reset_state(uid)

    node_id = await _active_node_id(uid)
    if not node_id:
        await message.answer(t("need_active_node", lang))
        return

    records = await gov_svc.list_proposals(node_id)
    rows = []
    for r in records[:10]:
        pid = r.get("proposal-id", "")
        icon = _PROPOSAL_STATUS_ICON.get(r.get("proposal-status", "open"), "🗳️")
        label = r.get("text", pid)[:40]
        rows.append([InlineKeyboardButton(text=f"{icon} {label}", callback_data=f"gov:view:{pid}")])
    rows.append([InlineKeyboardButton(text=t("btn_proposal_create", lang), callback_data="gov:create")])
    text = t("proposals_header", lang, pct=int(gov_svc.APPROVAL_PCT * 100),
             hours=gov_svc.VOTING_WINDOW_H) if records else t("proposals_empty", lang)
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


async def _proposal_view(callback: CallbackQuery, pid: str, lang: str) -> None:
    await gov_svc.check_and_resolve(pid)
    record = await gov_svc.get_proposal(pid)
    if not record:
        await callback.message.edit_text(t("proposal_not_found", lang))
        return
    status = record.get("proposal-status", "open")
    yes, total = await gov_svc.proposal_tallies(pid)
    text = t(
        "proposal_view", lang,
        icon=_PROPOSAL_STATUS_ICON.get(status, "🗳️"),
        text=html.escape(record.get("text", "")),
        status=t("proposal_status_" + status, lang),
        yes=yes, total=total,
    )
    rows = []
    if status == "open":
        rows.append([
            InlineKeyboardButton(text=t("btn_vote_yes", lang), callback_data=f"gov:v:{pid}:1"),
            InlineKeyboardButton(text=t("btn_vote_no", lang), callback_data=f"gov:v:{pid}:0"),
        ])
    await callback.message.edit_text(
        text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows) if rows else None,
    )


@dp.callback_query(F.data.startswith("gov:"))
async def handle_governance_callback(callback: CallbackQuery) -> None:
    if not callback.from_user or not callback.data or not callback.message:
        return
    uid = callback.from_user.id
    lang = await get_user_language(uid)
    parts = callback.data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    pid = parts[2] if len(parts) > 2 else ""

    if action == "create":
        node_id = await _active_node_id(uid)
        if not node_id:
            await callback.answer(t("need_active_node", lang), show_alert=True)
            return
        ghost = get_ghost_state_manager()
        cache = get_l1_cache()
        await ghost.set_state(uid, "awaiting_proposal_text")
        await cache.set_state_data(uid, {"gov_node": node_id})
        await callback.message.edit_text(t("proposal_ask_text", lang))

    elif action == "view":
        await _proposal_view(callback, pid, lang)

    elif action == "v":
        approve = (parts[3] if len(parts) > 3 else "0") == "1"
        err = await gov_svc.vote_proposal(uid, pid, approve)
        if err == "closed":
            await callback.answer(t("proposal_closed", lang), show_alert=True)
            return
        if err == "not_member":
            await callback.answer(t("need_active_node", lang), show_alert=True)
            return
        if err == "no_weight":
            await callback.answer(t("proposal_no_weight", lang), show_alert=True)
            return
        await callback.answer(t("proposal_vote_recorded", lang))
        return

    await callback.answer()


async def handle_proposal_text_message(message: Message, uid: int, text: str, lang: str) -> None:
    ghost = get_ghost_state_manager()
    cache = get_l1_cache()
    draft = await cache.get_state_data(uid) or {}
    await ghost.reset_state(uid)
    proposal = await gov_svc.create_proposal(uid, draft.get("gov_node", ""), text)
    if not proposal:
        await message.answer(t("proposal_invalid", lang,
                               min=gov_svc.MIN_TEXT_LEN, max=gov_svc.MAX_TEXT_LEN))
        return
    await message.answer(t(
        "proposal_created", lang,
        pct=int(gov_svc.APPROVAL_PCT * 100), hours=gov_svc.VOTING_WINDOW_H,
    ))


# ── Agente: acciones desde la conversación ──────────────────────────────────

async def _try_agent_actions(message: Message, uid: int, text: str, lang: str) -> bool:
    """Nivel 2 (Conecta) y Nivel 3 (Actúa) del Agente (§9.2).

    Retorna True si el Agente atendió el mensaje (no pasa al Thinker).
    Requiere nodo activo; sin nodo, la conversación sigue normal.
    """
    node_id = await _active_node_id(uid)
    if not node_id:
        return False

    # Nivel 2: "necesito un electricista" → proveedores verificados del nodo.
    category = agent_svc.detect_provider_intent(text)
    if category:
        provs = await providers_svc.get_providers(node_id)
        matches = [p for p in provs if p.get("category") == category] or provs
        if not matches:
            await message.answer(t("agent_no_providers", lang,
                                   category=_prov_label(category, lang)))
            return True
        out = t("agent_providers_found", lang, category=_prov_label(category, lang)) + "\n\n"
        for p in matches[:5]:
            out += t(
                "provider_entry", lang,
                icon=providers_svc.PROVIDER_CATEGORIES.get(p.get("category", ""), "🧰"),
                category=t("prov_" + p.get("category", "otros"), lang),
                desc=html.escape(p.get("description", "")),
            ) + "\n"
        await message.answer(out)
        return True

    # Nivel 3: "dona 10 SYNX al proyecto X" → confirmación explícita, nunca
    # se mueve SYNX sin que el usuario pulse el botón.
    amount = agent_svc.detect_donation_intent(text)
    if amount:
        records = await projects_svc.list_projects(node_id)
        project = agent_svc.match_project(records, text)
        if not project:
            await message.answer(t("agent_no_project_match", lang))
            return True
        pid = project.get("project-id", "")
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text=t("btn_confirm_trade", lang),
                callback_data=f"agent:don:{pid}:{amount:g}",
            ),
            InlineKeyboardButton(text=t("btn_cancel_trade", lang), callback_data="agent:cancel"),
        ]])
        await message.answer(
            t("agent_donation_confirm", lang,
              amount=f"{amount:g}", title=html.escape(project.get("title", pid))),
            reply_markup=kb,
        )
        return True

    return False


@dp.callback_query(F.data.startswith("agent:"))
async def handle_agent_callback(callback: CallbackQuery) -> None:
    if not callback.from_user or not callback.data or not callback.message:
        return
    uid = callback.from_user.id
    lang = await get_user_language(uid)
    parts = callback.data.split(":")
    action = parts[1] if len(parts) > 1 else ""

    if action == "cancel":
        await callback.message.edit_text(t("trade_cancelled", lang))

    elif action == "don":
        pid = parts[2] if len(parts) > 2 else ""
        # callback_data es falsificable por el cliente: validar el monto.
        amount = rewards.parse_amount(parts[3]) if len(parts) > 3 else None
        if amount is None:
            await callback.answer(t("invalid_amount", lang), show_alert=True)
            return
        err = await projects_svc.fund_project(uid, pid, amount)
        if err == "insufficient":
            await callback.message.edit_text(t("project_insufficient", lang))
        elif err in ("bad_amount", "not_active"):
            await callback.message.edit_text(t("project_not_active", lang))
        else:
            await callback.message.edit_text(
                t("agent_donation_done", lang, amount=f"{amount:g}")
            )

    await callback.answer()


# ══════════════════════════════════════════════════════════════════════════
# CUSTODIA ON-CHAIN: retiro de BNB / SYNERGIX (mueve fondos reales)
# ══════════════════════════════════════════════════════════════════════════

_ASSET_LABEL = {"bnb": "BNB", "synergix": "SYNERGIX"}


@dp.callback_query(F.data.startswith("wd:"))
async def handle_withdraw_callback(callback: CallbackQuery) -> None:
    if not callback.from_user or not callback.data or not callback.message:
        return
    uid = callback.from_user.id
    lang = await get_user_language(uid)
    ghost = get_ghost_state_manager()
    cache = get_l1_cache()
    parts = callback.data.split(":")
    action = parts[1] if len(parts) > 1 else ""

    if action == "asset":
        asset = parts[2] if len(parts) > 2 else ""
        if asset not in _ASSET_LABEL:
            await callback.answer(); return
        await ghost.set_state(uid, "awaiting_withdraw_address")
        await cache.set_state_data(uid, {"wd_asset": asset})
        await callback.message.edit_text(
            t("withdraw_ask_address", lang, asset=_ASSET_LABEL[asset])
        )

    elif action == "confirm":
        data = await cache.get_state_data(uid) or {}
        asset = data.get("wd_asset", "")
        to_addr = data.get("wd_address", "")
        amount = data.get("wd_amount", 0.0)
        await ghost.reset_state(uid)
        await cache.set_state_data(uid, {})
        if not (asset and to_addr and amount):
            await callback.message.edit_text(t("withdraw_expired", lang))
            await callback.answer(); return
        await callback.message.edit_text(t("withdraw_sending", lang))
        result = await custody_svc.withdraw(uid, to_addr, float(amount), asset)
        if result.get("ok"):
            if asset == "SYNERGIX":
                from aisynergix.services import tiers as tiers_svc
                tiers_svc.invalidate(uid)
            tx = result["tx"]
            tx = tx if tx.startswith("0x") else "0x" + tx
            await callback.message.edit_text(
                t("withdraw_sent", lang, amount=f"{amount:g}", asset=_ASSET_LABEL[asset],
                  tx=tx, url=f"https://bscscan.com/tx/{tx}"),
                disable_web_page_preview=True,
            )
        elif result.get("error") == "undecryptable":
            # Ofrecer regenerar la wallet bajo la master key actual.
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text=t("btn_wallet_reset", lang), callback_data="wd:reset"),
            ]])
            await callback.message.edit_text(t("withdraw_error_undecryptable", lang), reply_markup=kb)
        else:
            await callback.message.edit_text(
                t("withdraw_error_" + result.get("error", "broadcast"), lang,
                  min_bnb=custody_svc.MIN_WITHDRAW_BNB, min_token=custody_svc.MIN_WITHDRAW_TOKEN)
            )

    elif action == "cancel":
        await ghost.reset_state(uid)
        await cache.set_state_data(uid, {})
        await callback.message.edit_text(t("withdraw_cancelled", lang))

    elif action == "reset":
        # Confirmación antes de abandonar la wallet vieja (acción destructiva).
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=t("btn_wallet_reset_confirm", lang), callback_data="wd:reset_go"),
            InlineKeyboardButton(text=t("btn_cancel_trade", lang), callback_data="wd:cancel"),
        ]])
        await callback.message.edit_text(t("wallet_reset_confirm", lang), reply_markup=kb)

    elif action == "reset_go":
        await callback.message.edit_text(t("wallet_reset_working", lang))
        new_addr = await wallet_svc.reset_custodial_wallet(uid)
        if new_addr:
            await callback.message.edit_text(t("wallet_reset_done", lang, address=new_addr))
        else:
            await callback.message.edit_text(t("wallet_reset_failed", lang))

    await callback.answer()


async def handle_withdraw_address_message(message: Message, uid: int, text: str, lang: str) -> None:
    cache = get_l1_cache()
    data = await cache.get_state_data(uid) or {}
    to_addr = text.strip()
    if not custody_svc.is_valid_address(to_addr):
        await message.answer(t("withdraw_bad_address", lang))
        return
    data["wd_address"] = to_addr
    await get_ghost_state_manager().set_state(uid, "awaiting_withdraw_amount")
    await cache.set_state_data(uid, data)
    asset = data.get("wd_asset", "bnb")
    await message.answer(t("withdraw_ask_amount", lang, asset=_ASSET_LABEL.get(asset, asset)))


async def handle_withdraw_amount_message(message: Message, uid: int, text: str, lang: str) -> None:
    cache = get_l1_cache()
    data = await cache.get_state_data(uid) or {}
    amount = rewards.parse_amount(text)
    if amount is None:
        await message.answer(t("invalid_amount", lang))
        return
    data["wd_amount"] = amount
    await cache.set_state_data(uid, data)
    asset = data.get("wd_asset", "bnb")
    # Confirmación explícita ANTES de firmar (el retiro es irreversible).
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=t("btn_withdraw_confirm", lang), callback_data="wd:confirm"),
        InlineKeyboardButton(text=t("btn_cancel_trade", lang), callback_data="wd:cancel"),
    ]])
    await message.answer(
        t("withdraw_confirm", lang, amount=f"{amount:g}",
          asset=_ASSET_LABEL.get(asset, asset), address=html.escape(data.get("wd_address", ""))),
        reply_markup=kb,
    )


@dp.callback_query(F.data == "impact:useful")
async def handle_impact_useful(callback: CallbackQuery) -> None:
    """'✅ útil' (PIR §7.2): +1 impacto verificado a cada aporte usado en la
    respuesta.  El tracker paga la regalía perpetua al cruzar bloques de 100."""
    if not callback.from_user or not callback.message:
        return
    lang = await get_user_language(callback.from_user.id)
    key = (callback.message.chat.id, callback.message.message_id)
    payload = _impact_sources.pop(key, None)

    # El botón desaparece siempre: es de un solo uso.
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    if payload is None:
        await callback.answer(t("useful_expired", lang))
        return

    # Anti-farming: quien pulsa "útil" no puede acreditar impacto/regalías a
    # sus PROPIOS aportes (surgir el propio aporte con una consulta y votarlo
    # sería un bucle de auto-recompensa). Se excluye por Ghost ID.
    from aisynergix.bot.identity import _hash_uid
    presser_hash = _hash_uid(callback.from_user.id)

    # Acreditar en background — cada aporte implica lecturas/escrituras en
    # Irys y el callback debe responder en <10 s.
    async def _credit() -> None:
        try:
            n = await get_ai_manager().record_useful_sources(
                payload, exclude_author=presser_hash
            )
            logger.info("✅ útil: %d aporte(s) acreditados (chat=%s)", n, key[0])
        except Exception as exc:
            logger.warning("impact useful credit falló: %s", exc)

    _spawn_bot_bg(_credit())
    await callback.answer(t("useful_thanks", lang))


# ══════════════════════════════════════════════════════════════════════════
# CANJE: SYNX contable → SYNERGIX real (Fase B — gate; el pago llega en Fase C)
# ══════════════════════════════════════════════════════════════════════════

def _redeem_reason_text(reason: str, lang: str) -> str:
    """Traduce el motivo de inelegibilidad/fallo a un mensaje accionable."""
    if reason.startswith("pay_"):
        # El pago on-chain falló; el SYNX ya fue reembolsado.
        return t("redeem_pay_failed", lang)
    keys = {
        "not_verified": ("redeem_need_verify", {}),
        "low_reputation": ("redeem_need_reputation",
                           {"min": redeem_svc.REDEEM_MIN_CONTRIBUTIONS}),
        "below_min": ("redeem_below_min", {"min": f"{redeem_svc.REDEEM_MIN_AMOUNT:,.0f}"}),
        "insufficient_balance": ("redeem_insufficient", {}),
        "user_cap": ("redeem_user_cap", {}),
        "address_cap": ("redeem_address_cap", {}),
        "address_shared": ("redeem_address_shared", {}),
        "frozen": ("redeem_frozen", {}),
        "budget": ("redeem_budget", {}),
        "disabled": ("redeem_soon_generic", {}),
    }
    key, kw = keys.get(reason, ("redeem_ineligible", {}))
    return t(key, lang, **kw)


@dp.message(Command("canjear"))
async def cmd_redeem(message: Message) -> None:
    if not message.from_user:
        return
    uid = message.from_user.id
    _known_uids.add(uid)
    lang = await get_user_language(uid)
    await get_ghost_state_manager().reset_state(uid)

    status = await redeem_svc.redeemable_now(uid)
    balance = status.get("synx_balance", 0.0)
    header = t("redeem_header", lang, balance=f"{balance:,.0f}")

    if not status.get("eligible"):
        # No elegible: explicar cómo llegar a serlo (verificar wallet, aportar).
        await message.answer(header + "\n\n" + _redeem_reason_text(status.get("reason", ""), lang))
        return

    if not status.get("enabled"):
        # Elegible pero el canje aún no está activo (Fase B).
        await message.answer(
            header + "\n\n" + t("redeem_soon", lang,
                                max=f"{status['max']:,.0f}", address=status["address"])
        )
        return

    # Elegible y activo (Fase C): botón para reclamar el máximo.
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text=t("btn_redeem_claim", lang, max=f"{status['max']:,.0f}"),
            callback_data="redeem:claim",
        ),
    ]])
    await message.answer(
        header + "\n\n" + t("redeem_ready", lang,
                            max=f"{status['max']:,.0f}", address=status["address"]),
        reply_markup=kb,
    )


@dp.callback_query(F.data == "redeem:claim")
async def handle_redeem_claim(callback: CallbackQuery) -> None:
    if not callback.from_user or not callback.message:
        return
    uid = callback.from_user.id
    lang = await get_user_language(uid)
    status = await redeem_svc.redeemable_now(uid)
    if not (status.get("enabled") and status.get("eligible")):
        await callback.answer(t("redeem_ineligible", lang))
        return
    await callback.answer()
    await callback.message.edit_text(t("redeem_processing", lang))
    result = await redeem_svc.request_redemption(uid, status["max"])
    if result.get("ok"):
        tx = result["tx"]
        tx = tx if tx.startswith("0x") else "0x" + tx
        await callback.message.edit_text(
            t("redeem_claimed", lang, amount=f"{result['amount']:,.0f}",
              synergix=f"{result['synergix']:,.0f}", address=result["address"],
              tx=tx, url=f"https://bscscan.com/tx/{tx}"),
            disable_web_page_preview=True,
        )
    else:
        await callback.message.edit_text(_redeem_reason_text(result.get("reason", ""), lang))


@dp.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    if not message.from_user:
        return
    uid = message.from_user.id
    if uid not in _ADMIN_IDS:
        return  # Silently ignore non-admins
    lang = await get_user_language(uid)
    args = (message.text or "").split()
    if len(args) < 2:
        await message.answer("Uso: /admin lock | /admin unlock")
        return
    action = args[1].lower()
    from aisynergix.services.irys import create_emergency_lock, delete_emergency_lock
    if action == "lock":
        await create_emergency_lock()
        await message.answer(t("admin_lock_enabled", lang))
    elif action == "unlock":
        await delete_emergency_lock()
        await message.answer(t("admin_lock_disabled", lang))
    else:
        await message.answer("Uso: /admin lock | /admin unlock")


@dp.message(F.sticker)
async def handle_sticker_message(message: Message) -> None:
    if not message.from_user or not message.sticker:
        return

    uid = message.from_user.id
    _known_uids.add(uid)
    sticker = message.sticker
    set_name = sticker.set_name

    # A sticker is always answered with a sticker — never with text.
    # Pick a different sticker from the same pack; fall back to echoing the
    # original one when the pack has no alternatives or cannot be fetched.
    reply_file_id = sticker.file_id
    if set_name:
        try:
            sticker_set = await bot.get_sticker_set(set_name)
            others = [s for s in sticker_set.stickers if s.file_id != sticker.file_id]
            if others:
                reply_file_id = random.choice(others).file_id
        except Exception:
            pass
    try:
        await message.answer_sticker(reply_file_id)
    except Exception as e:
        logger.warning("Error en respuesta a sticker de %s: %s", uid, e)


@dp.message()
async def handle_free_conversation(message: Message) -> None:
    if not message.from_user:
        return

    if message.text is None:
        return

    uid = message.from_user.id
    _known_uids.add(uid)
    text = message.text.strip()
    lang = await get_user_language(uid)

    ghost = get_ghost_state_manager()
    current_state = await ghost.get_state(uid)

    if current_state == "awaiting_contribution":
        await handle_contribution_message(message, uid, text, lang)
        return

    if current_state == "awaiting_buy_amount":
        await handle_buy_amount_message(message, uid, text, lang)
        return

    if current_state == "awaiting_sell_amount":
        await handle_sell_amount_message(message, uid, text, lang)
        return

    if current_state == "awaiting_wallet_address":
        await handle_wallet_address_message(message, uid, text, lang)
        return

    if current_state == "awaiting_wallet_signature":
        await handle_wallet_signature_message(message, uid, text, lang)
        return

    if current_state == "awaiting_node_name":
        await handle_node_name_message(message, uid, text, lang)
        return

    if current_state == "awaiting_provider_desc":
        await handle_provider_desc_message(message, uid, text, lang)
        return

    if current_state == "awaiting_provider_payment":
        await handle_provider_payment_message(message, uid, text, lang)
        return

    if current_state == "awaiting_project_name":
        await handle_project_name_message(message, uid, text, lang)
        return

    if current_state == "awaiting_project_goal":
        await handle_project_goal_message(message, uid, text, lang)
        return

    if current_state == "awaiting_project_evidence":
        await handle_project_evidence_message(message, uid, text, lang)
        return

    if current_state == "awaiting_project_fund":
        await handle_project_fund_message(message, uid, text, lang)
        return

    if current_state == "awaiting_bounty_pool":
        await handle_bounty_pool_message(message, uid, text, lang)
        return

    if current_state == "awaiting_lesson_answer":
        await handle_lesson_answer_message(message, uid, text, lang)
        return

    if current_state == "awaiting_node_country":
        await handle_node_country_message(message, uid, text, lang)
        return

    if current_state == "awaiting_node_region":
        await handle_node_region_message(message, uid, text, lang)
        return

    if current_state == "awaiting_problem_text":
        await handle_problem_text_message(message, uid, text, lang)
        return

    if current_state == "awaiting_solution_text":
        await handle_solution_text_message(message, uid, text, lang)
        return

    if current_state == "awaiting_proposal_text":
        await handle_proposal_text_message(message, uid, text, lang)
        return

    if current_state == "awaiting_withdraw_address":
        await handle_withdraw_address_message(message, uid, text, lang)
        return

    if current_state == "awaiting_withdraw_amount":
        await handle_withdraw_amount_message(message, uid, text, lang)
        return

    # Anti-flood: el único camino restante es la conversación con el Thinker
    # (LLM local, concurrencia 1). Limita la frecuencia por usuario para que un
    # flood no monopolice la cola. Avisa como máximo una vez por ventana para
    # no generar spam propio de respuestas.
    if _DM_COOLDOWN_S:
        now = asyncio.get_event_loop().time()
        if now - _dm_last_ts.get(uid, 0.0) < _DM_COOLDOWN_S:
            if now - _dm_notified.get(uid, 0.0) >= _DM_COOLDOWN_S:
                _dm_notified[uid] = now
                await message.answer(t("dm_cooldown", lang))
            return
        # Poda defensiva para acotar la memoria de los diccionarios de rate.
        if len(_dm_last_ts) > 10000:
            _dm_last_ts.clear()
            _dm_notified.clear()
        _dm_last_ts[uid] = now

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

    from aisynergix.services.irys import is_emergency_locked
    if is_emergency_locked():
        await message.answer(t("emergency_lock_active", lang))
        return

    await message.answer(t("contribution_received", lang))

    try:
        result = await ai.process_contribution(uid, text)

        message_key = result.get("message_key", "contribution_success")

        if result["status"] == "success":
            if result.get("tier") == "legendary":
                message_key = "contribution_success_legendary"
            elif result.get("tier") == "elite":
                message_key = "contribution_success_elite"

            cid_raw = result.get("cid", "")
            if cid_raw.startswith("local:"):
                cid_display = cid_raw[6:22] + "… (pendiente)"
            else:
                full_url = f"{IRYS_GATEWAY_URL}/{cid_raw}"
                cid_display = f'<a href="{full_url}">{full_url}</a>'

            response_text = t(
                message_key,
                lang,
                cid=cid_display,
                quality_score=result.get("quality_score", 0),
                points_gained=result.get("points_gained", 0),
                new_total_points=result.get("new_total_points", 0),
                rank=_t_rank(result.get("rank", "🌱 Iniciado"), lang),
            )

            synx_gained = result.get("synx_gained", 0)
            if synx_gained:
                mult = result.get("synx_multiplier", 1.0)
                if mult and mult > 1.0:
                    response_text += "\n" + t(
                        "contribution_synx", lang,
                        synx=f"{synx_gained:.0f}", multiplier=f"{mult:.0f}",
                    )
                else:
                    response_text += "\n" + t(
                        "contribution_synx_plain", lang, synx=f"{synx_gained:.0f}"
                    )
                # Desglose §5.3 (el vacío ya se muestra arriba con su ×).
                extra = [b for b in result.get("synx_bonuses", []) if b != "gap"]
                if extra:
                    details = ", ".join(t(f"bonus_{b}", lang) for b in extra)
                    response_text += "\n" + t("synx_bonus_detail", lang, details=details)
                streak = result.get("streak_days", 0)
                if streak >= 2:
                    response_text += "\n" + t("streak_line", lang, days=streak)
                if result.get("oracle_review"):
                    response_text += "\n" + t("oracle_review_pending", lang)

            keyboard = get_main_keyboard(lang)
            await message.answer(response_text, reply_markup=keyboard)

            if result.get("new_rank"):
                new_rank_text = t(
                    "rank_up",
                    lang,
                    old_rank=_t_rank(result.get("rank", ""), lang),
                    new_rank=_t_rank(result["new_rank"], lang),
                )
                await message.answer(new_rank_text)

        elif result["status"] == "too_short":
            await message.answer(t("contribution_too_short", lang))
        elif result["status"] == "too_long":
            await message.answer(
                t("contribution_too_long", lang, max_length=result.get("max_length", 8000))
            )
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
        logger.exception("Error procesando aporte de %s: %s", uid, e)
        await message.answer(t("error_generic", lang))


async def _handle_image_request(
    message: Message,
    uid: int,
    prompt: str,
    lang: str,
) -> None:
    """Run the quota gate and, if allowed, generate and send one image."""
    ai = get_ai_manager()

    quota = ai.check_image_quota(uid)
    if not quota.get("ok"):
        reason = quota.get("reason")
        if reason == "cooldown":
            mins = max(1, round(quota.get("seconds", 0) / 60))
            await message.answer(t("image_cooldown", lang, minutes=mins))
        elif reason == "daily_limit":
            await message.answer(t("image_daily_limit", lang, limit=quota.get("limit", 0)))
        else:  # disabled / unknown
            await message.answer(t("image_disabled", lang))
        return

    # Temporary "generating…" notice: shown while we work, then deleted once the
    # photo (or a failure message) is ready, so it doesn't linger in the chat.
    notice = await message.answer(t("image_generating", lang))
    stop_action = asyncio.Event()
    action_task = asyncio.create_task(
        _keep_typing(message.chat.id, stop_action, action="upload_photo")
    )
    try:
        image = await ai.generate_image(uid, prompt)
    finally:
        stop_action.set()
        action_task.cancel()

    # Remove the temporary notice before delivering the result.
    try:
        await notice.delete()
    except Exception:
        pass

    if not image:
        await message.answer(t("image_failed", lang))
        return

    photo = BufferedInputFile(image, filename="synergix.png")
    try:
        await message.answer_photo(photo)
    except Exception as exc:
        logger.warning("answer_photo failed uid=%s: %s", uid, exc)
        await message.answer(t("image_failed", lang))


def _md_to_html(text: str) -> str:
    """Convierte el Markdown que emite el Thinker a HTML de Telegram.

    El bot envía con parse_mode=HTML, así que `**negrita**`, `## títulos` y
    demás se verían con los asteriscos/almohadillas literales. Se protegen
    primero los bloques/spans de código, se escapan las entidades HTML del
    texto del modelo (para no romper el parseo con un `<` suelto) y luego se
    traducen las marcas Markdown más comunes a las etiquetas que Telegram
    soporta (b, i, code, pre, a). Si algo queda mal, el envío hace fallback a
    texto plano en el llamador.
    """
    if not text:
        return text

    placeholders: list = []

    def _stash(snippet: str) -> str:
        placeholders.append(snippet)
        return f"\x00{len(placeholders) - 1}\x00"

    # Código en bloque y en línea: su contenido NO se formatea.
    text = re.sub(
        r"```[a-zA-Z0-9_+-]*\n?(.*?)```",
        lambda m: _stash(f"<pre>{html.escape(m.group(1).strip(chr(10)))}</pre>"),
        text, flags=re.DOTALL,
    )
    text = re.sub(
        r"`([^`\n]+)`",
        lambda m: _stash(f"<code>{html.escape(m.group(1))}</code>"),
        text,
    )

    # Escapar el resto (el modelo puede emitir <, >, &).
    text = html.escape(text)

    # Enlaces, encabezados, negrita, cursiva y viñetas.
    text = re.sub(
        r"\[([^\]\n]+)\]\((https?://[^)\s]+|tg://[^)\s]+)\)",
        r'<a href="\2">\1</a>', text,
    )
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s*(.+?)\s*#*\s*$", r"<b>\1</b>", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text, flags=re.DOTALL)
    text = re.sub(r"__(.+?)__", r"<b>\1</b>", text, flags=re.DOTALL)
    text = re.sub(r"(?<![\*\w])\*(?!\s)([^\*\n]+?)(?<!\s)\*(?![\*\w])", r"<i>\1</i>", text)
    text = re.sub(r"(?<![_\w])_(?!\s)([^_\n]+?)(?<!\s)_(?![_\w])", r"<i>\1</i>", text)
    text = re.sub(r"(?m)^(\s*)[-*]\s+", r"\1• ", text)

    # Restaurar los códigos protegidos.
    text = re.sub(r"\x00(\d+)\x00", lambda m: placeholders[int(m.group(1))], text)
    return text


async def handle_conversation_message(
    message: Message,
    uid: int,
    text: str,
    lang: str,
) -> None:
    from aisynergix.services.irys import check_ai_guard
    if check_ai_guard(text):
        await message.answer(t("ai_guard_blocked", lang))
        return

    ai = get_ai_manager()

    # Emoji-only message → ask the model for a single emoji reaction.
    # The full verbose response is suppressed; only emojis are extracted and shown.
    is_emoji_msg = _is_emoji_only(text)

    # Agente Synergix (§9.2): intenciones de acción — buscar proveedores
    # (Nivel 2) o donar a un proyecto (Nivel 3, con confirmación) — se
    # atienden aquí y no llegan al Thinker.  Detectores deterministas,
    # sin coste de LLM.
    if not is_emoji_msg:
        try:
            if await _try_agent_actions(message, uid, text, lang):
                return
        except Exception as exc:
            logger.warning("agent actions uid=%s falló: %s", uid, exc)

    # Image generation: the Judge classifies whether this is an explicit request
    # to create an image.  If so, handle it here and return — never fall through
    # to the chat Thinker.  Emoji-only messages are never image requests.
    if not is_emoji_msg:
        image_prompt = await ai.classify_image_request(text)
        if image_prompt:
            await _handle_image_request(message, uid, image_prompt, lang)
            return

    ai_text = text
    if is_emoji_msg:
        ai_text = (
            f"[El usuario envió el emoji {text}. "
            "Responde ÚNICAMENTE con 1 o 2 emojis que expresen una reacción empática "
            "natural. Nada de texto — solo el [[STICKER:X]] apropiado.]"
        )

    # Stream tokens: Qwen2.5-Coder-3B produces no <think> block so all chunks
    # arrive as "answer" immediately.  If the model is ever swapped for a
    # reasoning model (Qwen3, DeepSeek-R1, etc.), the think-trace UI activates
    # automatically: it renders the trace live in italic with a 💭 header and
    # replaces it with the clean answer once </think> closes.
    sent_msg = None
    answer_buf = ""
    think_buf = ""
    saw_answer = False
    memory_count = 0
    web_used = False
    sources_payload = ""
    last_edit = 0.0
    THINK_PREVIEW_CHARS = 800  # Telegram caps messages at 4096; show tail only
    THINK_EDIT_INTERVAL = 1.5  # safe distance from Telegram's edit rate limit
    ANSWER_EDIT_INTERVAL = 0.9
    stop_typing = asyncio.Event()
    typing_task = asyncio.create_task(_keep_typing(message.chat.id, stop_typing))

    def _render_think(text: str) -> str:
        snippet = text[-THINK_PREVIEW_CHARS:]
        if len(text) > THINK_PREVIEW_CHARS:
            snippet = "…" + snippet
        escaped = html.escape(snippet)
        header = html.escape(t("thinking_label", lang))
        return f"<b>{header}</b>\n<i>{escaped}</i>"

    try:
        async for kind, chunk in ai.stream_conversation(uid, ai_text):
            if kind == "memory_count":
                memory_count = int(chunk)
                continue
            if kind == "web_used":
                web_used = True
                continue
            if kind == "sources":
                sources_payload = chunk
                continue

            now = asyncio.get_event_loop().time()

            if kind == "think" and not saw_answer:
                think_buf += chunk
                if sent_msg is None:
                    if len(think_buf.strip()) >= 12:
                        stop_typing.set()
                        try:
                            sent_msg = await message.answer(
                                _render_think(think_buf), parse_mode="HTML"
                            )
                        except Exception:
                            sent_msg = await message.answer(t("thinking_label", lang))
                        last_edit = now
                    continue
                if now - last_edit >= THINK_EDIT_INTERVAL:
                    try:
                        await sent_msg.edit_text(_render_think(think_buf), parse_mode="HTML")
                        last_edit = now
                    except Exception:
                        pass
                continue

            if kind == "answer":
                answer_buf += chunk
                if not saw_answer:
                    saw_answer = True
                    stop_typing.set()
                    if is_emoji_msg:
                        continue  # accumulate silently; final emoji shown after stream
                    if sent_msg is None:
                        if len(answer_buf.strip()) >= 1:
                            sent_msg = await message.answer(answer_buf)
                            last_edit = now
                        continue
                    # Replace the live "thinking" preview with the clean answer.
                    try:
                        await sent_msg.edit_text(answer_buf or " ")
                    except Exception:
                        pass
                    last_edit = now
                    continue
                if is_emoji_msg:
                    continue  # keep accumulating, don't stream intermediate text
                if now - last_edit >= ANSWER_EDIT_INTERVAL:
                    try:
                        await sent_msg.edit_text(answer_buf)
                        last_edit = now
                    except Exception:
                        pass

        # Stream finished.
        if not saw_answer:
            # Model exhausted max_tokens inside <think> and never produced a
            # visible answer.  Replace the live trace (if shown) with a
            # generic error so the user knows the request failed.
            if sent_msg is not None:
                try:
                    await sent_msg.edit_text(t("error_generic", lang))
                except Exception:
                    pass
            else:
                await message.answer(t("error_generic", lang))
            return

        # Emoji-only path: show only the extracted emoji reaction, no footer.
        if is_emoji_msg:
            from aisynergix.ai.manager import _extract_sticker
            _, reply_emoji = _extract_sticker(answer_buf)
            emoji_final = reply_emoji if reply_emoji else _extract_emoji_reply(answer_buf)
            await message.answer(emoji_final)
            return

        if sent_msg is None:
            sent_msg = await message.answer(answer_buf)

        # Final edit: always flush the last tokens that may not have been
        # shown during the throttled streaming loop, then strip [[STICKER:…]].
        # Previously this edit was skipped when final_text == answer_buf
        # (i.e. no sticker), causing the last ≤0.9 s of generated text to
        # be silently dropped.
        from aisynergix.ai.manager import _extract_sticker, _strip_filler, _strip_name_prefix
        clean, sticker_emoji = _extract_sticker(answer_buf)
        clean = _strip_name_prefix(clean)
        clean = _strip_filler(clean)
        # El Thinker emite Markdown (**negrita**, ## títulos): convertir a HTML
        # de Telegram para que no se vean los asteriscos/almohadillas literales.
        clean = _md_to_html(clean)
        final_text = f"{clean}\n{sticker_emoji}" if sticker_emoji else clean
        if memory_count > 0:
            final_text += f"\n\n<i>{t('rag_memory_used', lang, count=memory_count)}</i>"
        elif web_used:
            final_text += f"\n\n<i>{t('web_search_used', lang)}</i>"
        # PIR: si la respuesta usó memoria inmortal, ofrecer confirmar "✅ útil".
        useful_kb = _useful_button_kb(lang) if sources_payload else None
        try:
            await sent_msg.edit_text(final_text, parse_mode="HTML", reply_markup=useful_kb)
        except Exception:
            try:
                await sent_msg.edit_text(final_text, reply_markup=useful_kb)
            except Exception:
                useful_kb = None
        if useful_kb is not None and sources_payload:
            _register_impact_sources(message.chat.id, sent_msg.message_id, sources_payload)

    except Exception as e:
        logger.exception("Error en conversación streaming de %s: %s", uid, e)
        if sent_msg is not None:
            try:
                await sent_msg.edit_text(t("error_generic", lang))
            except Exception:
                pass
        else:
            try:
                await message.answer(t("error_generic", lang))
            except Exception:
                pass
    finally:
        stop_typing.set()
        typing_task.cancel()
        try:
            await typing_task
        except asyncio.CancelledError:
            pass


async def handle_buy_amount_message(
    message: Message,
    uid: int,
    text: str,
    lang: str,
) -> None:
    ghost = get_ghost_state_manager()
    cache = get_l1_cache()

    from aisynergix.services.rewards import parse_amount
    bnb_in = parse_amount(text)
    if bnb_in is None:
        await message.answer(t("invalid_amount", lang))
        return

    if bnb_in < trading_svc.MIN_BUY_BNB:
        await message.answer(t("amount_too_low_buy", lang, min_buy=trading_svc.MIN_BUY_BNB))
        return

    syn_out = await trading_svc.calculate_buy_amount(bnb_in)
    if syn_out is None:
        # No PancakeSwap pair yet — token is still on the four.meme bonding curve.
        # Give the user the four.meme link so they can trade against the curve.
        await ghost.reset_state(uid)
        link = await trading_svc.get_trade_link(bnb_in, "buy")
        await message.answer(t("bonding_curve_trade", lang, link=link))
        return

    bnb_usd = await trading_svc.get_bnb_usd_price()
    usd_in = f"{bnb_in * (bnb_usd or 0):.2f}" if bnb_usd else "N/A"
    link = await trading_svc.get_trade_link(bnb_in, "buy")

    await cache.set_state_data(uid, {"type": "buy", "bnb_in": bnb_in, "syn_out": syn_out, "link": link})

    inline_kb = get_trade_confirm_keyboard(lang, "buy")
    await message.answer(
        t("buy_summary", lang, bnb_in=bnb_in, usd_in=usd_in, syn_out=syn_out, slippage=trading_svc.DEFAULT_SLIPPAGE),
        reply_markup=inline_kb,
    )


async def handle_sell_amount_message(
    message: Message,
    uid: int,
    text: str,
    lang: str,
) -> None:
    ghost = get_ghost_state_manager()
    cache = get_l1_cache()

    from aisynergix.services.rewards import parse_amount
    syn_in = parse_amount(text)
    if syn_in is None:
        await message.answer(t("invalid_amount", lang))
        return

    bnb_out = await trading_svc.calculate_sell_amount(syn_in)
    if bnb_out is None:
        # No PancakeSwap pair yet — token is still on the four.meme bonding curve.
        # Give the user the four.meme link so they can trade against the curve.
        await ghost.reset_state(uid)
        link = await trading_svc.get_trade_link(syn_in, "sell")
        await message.answer(t("bonding_curve_trade", lang, link=link))
        return

    if bnb_out < trading_svc.MIN_SELL_BNB_EQUIV:
        await message.answer(t("amount_too_low_sell", lang, min_bnb_equiv=trading_svc.MIN_SELL_BNB_EQUIV))
        return

    bnb_usd = await trading_svc.get_bnb_usd_price()
    usd_out = f"{bnb_out * (bnb_usd or 0):.2f}" if bnb_usd else "N/A"
    link = await trading_svc.get_trade_link(syn_in, "sell")

    await cache.set_state_data(uid, {"type": "sell", "syn_in": syn_in, "bnb_out": bnb_out, "link": link})

    inline_kb = get_trade_confirm_keyboard(lang, "sell")
    await message.answer(
        t("sell_summary", lang, syn_in=syn_in, bnb_out=bnb_out, usd_out=usd_out, slippage=trading_svc.DEFAULT_SLIPPAGE),
        reply_markup=inline_kb,
    )


async def handle_wallet_address_message(
    message: Message,
    uid: int,
    text: str,
    lang: str,
) -> None:
    ghost = get_ghost_state_manager()

    from aisynergix.bot.identity import _hash_uid
    uid_hash = _hash_uid(uid)

    if not _is_hex_address(text):
        await message.answer(t("verify_invalid_address", lang))
        return

    challenge = build_challenge(uid_hash, text)
    if not challenge:
        await message.answer(t("verify_invalid_address", lang))
        return

    await ghost.enter_wallet_signature_mode(uid)
    await message.answer(t("verify_challenge", lang, challenge=challenge))


async def handle_wallet_signature_message(
    message: Message,
    uid: int,
    text: str,
    lang: str,
) -> None:
    ghost = get_ghost_state_manager()

    from aisynergix.bot.identity import _hash_uid
    uid_hash = _hash_uid(uid)

    recovered = verify_signature(uid_hash, text)
    await ghost.reset_state(uid)

    if not recovered:
        await message.answer(t("verify_failed", lang))
        return

    await save_verified_wallet(uid_hash, recovered)
    await message.answer(t("verify_success", lang, address=recovered))


# Comandos del menú de Telegram: (comando, clave de locale de su descripción).
_MENU_COMMANDS = [
    ("start",       "cmd_start"),
    ("nodos",       "cmd_nodos"),
    ("crear_nodo",  "cmd_crear_nodo"),
    ("proveedores", "cmd_proveedores"),
    ("proyectos",   "cmd_proyectos"),
    ("problemas",   "cmd_problemas"),
    ("bounties",    "cmd_bounties"),
    ("aprender",    "cmd_aprender"),
    ("oraculo",     "cmd_oraculo"),
    ("propuestas",  "cmd_propuestas"),
    ("pasaporte",   "cmd_pasaporte"),
    ("canjear",     "cmd_canjear"),
]


def _commands_for(lang: str) -> list:
    # Telegram limita la descripción a 256 caracteres; las nuestras son cortas.
    return [BotCommand(command=cmd, description=t(key, lang)[:256])
            for cmd, key in _MENU_COMMANDS]


async def set_bot_commands():
    """Publica el menú de comandos en los 10 idiomas (§ i18n total).

    Telegram muestra a cada usuario el menú de su idioma de interfaz
    (language_code); el menú por defecto (sin código) queda en español para
    cualquier idioma fuera de los 10 soportados.

    Estos metadatos rara vez cambian y Telegram los limita con flood control
    agresivo (un ban puede durar >20 min). Por eso NINGUNA llamada puede ser
    fatal: se capturan una a una para que un fallo/flood no impida arrancar.
    """
    for lang_code in (None, *LANG_NAMES):
        try:
            if lang_code is None:
                await bot.set_my_commands(_commands_for("es"))  # default global
            else:
                await bot.set_my_commands(
                    _commands_for(lang_code), language_code=lang_code,
                )
        except Exception as exc:
            logger.warning("set_my_commands(%s) falló: %s", lang_code or "default", exc)


async def set_bot_descriptions():
    """Publica la descripción del bot en los 10 idiomas (§ i18n total).

    ``set_my_description`` es el texto de "¿Qué puede hacer este bot?" que
    Telegram muestra en el chat vacío (máx. 512); ``set_my_short_description``
    es la del perfil y los reenvíos (máx. 120). Igual que el menú de comandos,
    cada usuario la ve en su idioma de interfaz y el default queda en español.
    """
    try:
        await bot.set_my_description(t("bot_description", "es")[:512])
        await bot.set_my_short_description(t("bot_short_description", "es")[:120])
    except Exception as exc:
        logger.warning("set_my_description(default) falló: %s", exc)
    for lang_code in LANG_NAMES:
        try:
            await bot.set_my_description(
                t("bot_description", lang_code)[:512], language_code=lang_code,
            )
            await bot.set_my_short_description(
                t("bot_short_description", lang_code)[:120], language_code=lang_code,
            )
        except Exception as exc:
            logger.warning("set_my_description(%s) falló: %s", lang_code, exc)


async def on_startup():
    logger.info("🚀 Iniciando Nodo Fantasma Synergix...")

    # Bot identity for @mention / reply detection in groups.
    global BOT_ID, BOT_USERNAME
    try:
        me = await bot.get_me()
        BOT_ID = me.id
        BOT_USERNAME = me.username or ""
        logger.info("🤖 Identidad del bot: @%s (id=%s)", BOT_USERNAME, BOT_ID)
        if _ADMIN_IDS:
            logger.info("👑 /admin habilitado para %d admin(s)", len(_ADMIN_IDS))
        else:
            logger.warning(
                "👑 /admin DESHABILITADO: SYNERGIX_ADMIN_IDS vacía o no llegó al proceso"
            )
        if _GROUP_WHITELIST:
            logger.info("👥 Grupos autorizados: %s", sorted(_GROUP_WHITELIST))
        else:
            logger.info("👥 Sin whitelist de grupos (responde en cualquier grupo)")
    except Exception as exc:
        logger.error("No pude obtener la identidad del bot (get_me): %s", exc)

    await load_all_locales()
    logger.info(f"🌐 Locales cargados: {list(LOCALES.keys())}")

    # Sincronización de comandos/descripción del bot: metadatos que rara vez
    # cambian y que Telegram limita con flood control agresivo. Se ejecuta en
    # SEGUNDO PLANO y con guardas totales para que un flood/ban (que puede
    # durar >20 min) NUNCA impida que el bot arranque y haga polling. Se puede
    # desactivar del todo con SYNERGIX_SYNC_BOT_META=false cuando ya están
    # publicados y se reinicia con frecuencia.
    if os.getenv("SYNERGIX_SYNC_BOT_META", "true").strip().lower() not in (
        "0", "false", "no", "off",
    ):
        async def _sync_bot_metadata() -> None:
            try:
                await set_bot_commands()
                await set_bot_descriptions()
            except Exception as exc:
                logger.warning("sincronización de metadatos del bot falló: %s", exc)
        _spawn_bot_bg(_sync_bot_metadata())
    else:
        logger.info("⏭️ Sincronización de metadatos del bot desactivada (SYNERGIX_SYNC_BOT_META)")

    from aisynergix.services.irys import (
        load_ai_guard, load_system_config, check_emergency_lock,
        load_current_challenge_from_greenfield,
    )
    await load_system_config()
    logger.info("⚙️ System config cargado")
    await load_ai_guard()
    logger.info("🛡️ AI Guard inicializado")
    locked = await check_emergency_lock()
    if locked:
        logger.warning("🔒 Emergency lock ACTIVO al inicio")
    challenge = await load_current_challenge_from_greenfield()
    if challenge:
        global _last_broadcast_challenge_id
        _last_broadcast_challenge_id = challenge.get("id")
    logger.info("🎯 Challenge restaurado (id=%s)", _last_broadcast_challenge_id)

    # Start background loop that detects new challenges and notifies users
    asyncio.create_task(_challenge_broadcast_loop())

    # Notificación push a Oráculos cuando se abre una revisión (§5.4).
    oracle_svc.set_review_notifier(_notify_oracles_new_review)

    # Liquidación de royalties de la API de Conocimiento a los autores (③).
    asyncio.create_task(_api_settlement_loop())

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
    thinker = get_ai_manager()._thinker
    judge = get_ai_manager()._judge
    await thinker.close()
    await judge.close()


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
