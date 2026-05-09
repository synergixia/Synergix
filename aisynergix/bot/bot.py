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
    verify_signature,
    clear_pending,
    get_verified_wallet,
    save_verified_wallet,
    _is_hex_address,
)
from aisynergix.services import dexscreener as dex_svc
from aisynergix.services import four_meme as fourmeme_svc


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


@dp.message(Command("start"))
async def cmd_start(message: Message) -> None:
    if not message.from_user:
        return

    uid = message.from_user.id

    identity = get_identity_manager()
    ghost = get_ghost_state_manager()
    ai = get_ai_manager()

    detected_lang = await detect_user_language(message)

    profile = await identity.get_profile(uid)
    is_new = profile.points == 0 and profile.total_uses_count == 0
    name = message.from_user.first_name or "Mente"

    if is_new:
        profile.set_language(detected_lang)
        identity._cache.set(uid, profile)
        lang = detected_lang
        welcome_text = t("welcome", lang, name=name)
    else:
        lang = profile.language
        welcome_text = t("welcome_back", lang, name=name)

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
    name = message.from_user.first_name or ""
    lang = await get_user_language(uid)

    ghost = get_ghost_state_manager()
    await ghost.reset_state(uid)

    ai = get_ai_manager()
    status = await ai.get_user_status(uid, name=name)

    status_text = t(
        "status_msg",
        lang,
        name=status["name"],
        rank=status["rank"],
        points=status["points"],
        contribuciones=status["contribuciones"],
        daily_aportes_count=status["daily_aportes_count"],
        daily_limit=status["daily_limit"],
        total_uses_count=status["total_uses_count"],
        total_aportes=status["total_aportes"],
        tema_actual=status["tema_actual"],
        points_next=status["points_next"],
        beneficio=status["beneficio"],
        multiplier=status["multiplier"],
        contribution_count=status["contribution_count"],
        language=get_lang_name(status["language"]),
    )

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

    from aisynergix.services.greenfield import list_aportes, read_aporte
    from aisynergix.bot.identity import _hash_uid

    uid_hash = _hash_uid(uid)
    aportes = await list_aportes(uid_hash, limit=10)

    if not aportes:
        await message.answer(t("memory_empty", lang))
        return

    memory_text = t("memory_header", lang)

    for aporte in aportes:
        try:
            texto, tags = await read_aporte(aporte["path"])
            quality = tags.get("quality_score", "N/A")

            preview = texto[:120] + "..." if len(texto) > 120 else texto
            memory_text += t("memory_entry", lang, text=preview, quality=quality) + "\n"
        except Exception:
            continue

    memory_text += t("memory_footer", lang, count=len(aportes))
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
        cb_name = callback.from_user.first_name or ""
        status = await ai.get_user_status(uid, name=cb_name)
        status_text = t(
            "status_msg",
            lang,
            name=status["name"],
            rank=status["rank"],
            points=status["points"],
            contribuciones=status["contribuciones"],
            daily_aportes_count=status["daily_aportes_count"],
            daily_limit=status["daily_limit"],
            total_uses_count=status["total_uses_count"],
            total_aportes=status["total_aportes"],
            tema_actual=status["tema_actual"],
            points_next=status["points_next"],
            beneficio=status["beneficio"],
            multiplier=status["multiplier"],
            contribution_count=status["contribution_count"],
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


@dp.message(F.text == "🏆 Top Mentes")
async def handle_top_mentes_button(message: Message) -> None:
    if not message.from_user:
        return

    uid = message.from_user.id
    lang = await get_user_language(uid)
    ghost = get_ghost_state_manager()
    await ghost.reset_state(uid)

    from aisynergix.services.greenfield import read_json_data
    top10 = await read_json_data("aisynergix/data/top10.json")

    if not top10 or not isinstance(top10, list):
        await message.answer(t("top_mentes_empty", lang))
        return

    medals = ["🥇", "🥈", "🥉"]
    text = t("top_mentes_header", lang) + "\n"
    for i, user in enumerate(top10):
        emoji = medals[i] if i < 3 else f"{i + 1}."
        rank_label = user.get("rank", "🌱 Iniciado")
        points = user.get("points", 0)
        contribs = user.get("total_uses_count", 0)
        text += t(
            "top_mentes_entry",
            lang,
            rank_emoji=emoji,
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
                InlineKeyboardButton(text="✅ Continuar", callback_data="verify:start"),
            ]])
            await callback.message.edit_text(t("verify_intro", lang), reply_markup=inline_kb)
    elif action == "balance":
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
                await callback.message.edit_text(
                    t(
                        "balance_info",
                        lang,
                        address=existing,
                        syn=syn_bal,
                        usd=usd_value,
                        bnb=bnb_bal,
                    )
                )
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
    elif action == "market":
        market = await dex_svc.get_token_market(trading_svc.SYNERGIX_TOKEN)
        if not market:
            await callback.message.edit_text(t("market_unavailable", lang))
        else:
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

    await ghost.enter_wallet_verify_mode(uid)
    await callback.message.edit_text(t("verify_address_prompt", lang))
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

    link = trade_data.get("link", "")
    await ghost.reset_state(uid)
    await cache.set_state_data(uid, {})
    await callback.message.edit_text(
        t("trade_link", lang, link=link, slippage=trading_svc.DEFAULT_SLIPPAGE)
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
        logger.exception("Error procesando aporte de %s: %s", uid, e)
        await message.answer(t("error_generic", lang))


async def handle_conversation_message(
    message: Message,
    uid: int,
    text: str,
    lang: str,
) -> None:
    ai = get_ai_manager()

    try:
        response, sticker_emoji, search_results = await ai.process_conversation(uid, text)
        await message.answer(response)
        if sticker_emoji:
            await message.answer(sticker_emoji)

    except Exception as e:
        logger.exception("Error en conversación libre de %s: %s", uid, e)
        await message.answer(t("error_generic", lang))


async def handle_buy_amount_message(
    message: Message,
    uid: int,
    text: str,
    lang: str,
) -> None:
    ghost = get_ghost_state_manager()
    cache = get_l1_cache()

    try:
        bnb_in = float(text.replace(",", "."))
    except ValueError:
        await message.answer(t("invalid_amount", lang))
        return

    if bnb_in < trading_svc.MIN_BUY_BNB:
        await message.answer(t("amount_too_low_buy", lang, min_buy=trading_svc.MIN_BUY_BNB))
        return

    syn_out = await trading_svc.calculate_buy_amount(bnb_in)
    if syn_out is None:
        await ghost.reset_state(uid)
        await message.answer(t("price_unavailable", lang))
        return

    bnb_usd = await trading_svc.get_bnb_usd_price()
    usd_in = f"{bnb_in * (bnb_usd or 0):.2f}" if bnb_usd else "N/A"
    link = trading_svc.generate_buy_link(bnb_in)

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

    try:
        syn_in = float(text.replace(",", "."))
    except ValueError:
        await message.answer(t("invalid_amount", lang))
        return

    bnb_out = await trading_svc.calculate_sell_amount(syn_in)
    if bnb_out is None:
        await ghost.reset_state(uid)
        await message.answer(t("price_unavailable", lang))
        return

    if bnb_out < trading_svc.MIN_SELL_BNB_EQUIV:
        await message.answer(t("amount_too_low_sell", lang, min_bnb_equiv=trading_svc.MIN_SELL_BNB_EQUIV))
        return

    bnb_usd = await trading_svc.get_bnb_usd_price()
    usd_out = f"{bnb_out * (bnb_usd or 0):.2f}" if bnb_usd else "N/A"
    link = trading_svc.generate_sell_link(syn_in)

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
