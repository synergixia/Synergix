import asyncio
import json
import logging
import os
import sys
from collections import defaultdict

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart
from aiogram.types import (
    InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, ReplyKeyboardMarkup,
)
from dotenv import load_dotenv

from aisynergix.ai.local_ia import ask_judge, ask_thinker, escape_markdown_v2
from aisynergix.ai.manager import sem
from aisynergix.bot.fsm import get_state, set_state
from aisynergix.bot.identity import UserContext, dehydrate_user, hydrate_user
from aisynergix.config.constants import MASTER_UIDS, RANK_TABLE
from aisynergix.services.greenfield import upload_aporte
from aisynergix.services.rag_engine import get_related_context

# ── Logging ────────────────────────────────────────────────────────────────────
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("logs/synergix.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("SynergixNode")

load_dotenv()
bot = Bot(token=os.getenv("TELEGRAM_TOKEN"))
dp  = Dispatcher()

# Cargar traducciones
with open("aisynergix/config/locales.json", "r", encoding="utf-8") as f:
    T = json.load(f)

# Historial de conversación en RAM (por uid, máx 10 turnos)
# Volátil por diseño — se pierde al reiniciar (stateless)
_chat_history: dict[str, list[dict]] = defaultdict(list)
_MAX_HISTORY = 10


def get_menu_kb(lang: str) -> ReplyKeyboardMarkup:
    kb = [
        [KeyboardButton(text=T[lang]["btn_contribute"]),
         KeyboardButton(text=T[lang]["btn_status"])],
        [KeyboardButton(text=T[lang]["btn_memory"]),
         KeyboardButton(text=T[lang]["btn_language"])],
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True, is_persistent=True)


# ── Middleware de identidad ────────────────────────────────────────────────────
@dp.message.outer_middleware()
async def ghost_identity_middleware(handler, event, data):
    if not event.from_user:
        return await handler(event, data)
    uid = str(event.from_user.id)
    ctx = await hydrate_user(uid)
    data["user"] = ctx
    try:
        return await handler(event, data)
    finally:
        await dehydrate_user(ctx)


@dp.callback_query.outer_middleware()
async def ghost_identity_cb_middleware(handler, event, data):
    if not event.from_user:
        return await handler(event, data)
    uid = str(event.from_user.id)
    ctx = await hydrate_user(uid)
    data["user"] = ctx
    try:
        return await handler(event, data)
    finally:
        await dehydrate_user(ctx)


# ══════════════════════════════════════════════════════════════════════════════
# HANDLERS
# ══════════════════════════════════════════════════════════════════════════════

@dp.message(CommandStart())
async def cmd_start(message: types.Message, user: UserContext):
    lang = user.language
    txt  = T[lang]["welcome"].format(
        name=message.from_user.first_name,
        challenge="Soberanía Web3 🔗"
    )
    await message.answer(
        escape_markdown_v2(txt),
        reply_markup=get_menu_kb(lang),
        parse_mode="MarkdownV2",
    )


@dp.message(F.text.func(lambda t: any(
    t == T[l]["btn_status"] for l in T
)))
async def view_status(message: types.Message, user: UserContext):
    lang = user.language
    info = user.get_rank_info()
    denom = info["next_pts"] if info["next_pts"] > 0 else 1
    prog  = min(int((user.points / denom) * 10), 10)
    bar   = "█" * prog + "░" * (10 - prog)

    txt = T[lang]["status_msg"].format(
        total     = "∞ DCellar",
        challenge = "Inmortalidad RAG 🧬",
        name      = message.from_user.first_name,
        pts       = user.points,
        contribs  = user.impact_index,
        impact    = user.impact_index * 2,
        rank      = user.rank,
        benefit   = info["benefit"],
        progress_bar = bar,
        next_rank = f"{max(0, info['next_pts'] - user.points)} pts"
        if info["next_pts"] else "Rango máximo 🔮",
    )
    await message.answer(
        escape_markdown_v2(txt), parse_mode="MarkdownV2"
    )


@dp.message(F.text.func(lambda t: any(
    t == T[l]["btn_language"] for l in T
)))
async def lang_menu(message: types.Message, user: UserContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Español 🇪🇸",    callback_data="sl_es"),
         InlineKeyboardButton(text="English 🇺🇸",    callback_data="sl_en")],
        [InlineKeyboardButton(text="简体中文 🇨🇳",   callback_data="sl_zh_cn"),
         InlineKeyboardButton(text="繁體中文 🇭🇰",   callback_data="sl_zh")],
    ])
    await message.answer(
        escape_markdown_v2(T[user.language]["choose_lang"]),
        reply_markup=kb, parse_mode="MarkdownV2",
    )


@dp.callback_query(F.data.startswith("sl_"))
async def set_lang(cb: types.CallbackQuery, user: UserContext):
    # Extraer lang de "sl_es" / "sl_en" / "sl_zh_cn" / "sl_zh"
    raw  = cb.data[3:]          # "es" / "en" / "zh_cn" / "zh"
    lang = raw if raw in T else "es"
    user.language = lang
    await cb.message.edit_text(
        escape_markdown_v2(T[lang]["lang_updated"]),
        parse_mode="MarkdownV2",
    )
    await cb.message.answer(
        escape_markdown_v2("✅"),
        reply_markup=get_menu_kb(lang),
        parse_mode="MarkdownV2",
    )
    await cb.answer()


@dp.message(F.text.func(lambda t: any(
    t == T[l]["btn_contribute"] for l in T
)))
async def start_contribution_mode(message: types.Message, user: UserContext):
    await set_state(user, "AWAITING_CONTRIB")
    await message.answer(
        escape_markdown_v2(T[user.language]["await_contrib"]),
        parse_mode="MarkdownV2",
    )


@dp.message(F.text.func(lambda t: any(
    t == T[l]["btn_memory"] for l in T
)))
async def view_memory(message: types.Message, user: UserContext):
    lang = user.language
    mem_msgs = {
        "es": f"🧠 Tus aportes inmortalizados: *{user.impact_index}*\n🔮 Impacto en la red: *{user.impact_index * 2}*\n\n_Tu legado vive en BNB Greenfield para siempre\\._",
        "en": f"🧠 Your immortalized contributions: *{user.impact_index}*\n🔮 Network impact: *{user.impact_index * 2}*\n\n_Your legacy lives on BNB Greenfield forever\\._",
        "zh_cn": f"🧠 你的永久贡献: *{user.impact_index}*\n🔮 网络影响力: *{user.impact_index * 2}*\n\n_你的遗产永远活在BNB Greenfield上\\._",
        "zh":    f"🧠 你的永久貢獻: *{user.impact_index}*\n🔮 網路影響力: *{user.impact_index * 2}*\n\n_你的遺產永遠活在BNB Greenfield上\\._",
    }
    await message.answer(
        mem_msgs.get(lang, mem_msgs["es"]),
        parse_mode="MarkdownV2",
    )


# ── Handler principal NLP ──────────────────────────────────────────────────────
@dp.message(F.text)
async def synergix_nlp(message: types.Message, user: UserContext):
    lang = user.language
    uid  = user.uid

    # Cuota diaria
    if int(uid) not in MASTER_UIDS and user.daily_quota <= 0:
        await message.answer(
            escape_markdown_v2(T[lang]["error_quota"].format(rank=user.rank)),
            parse_mode="MarkdownV2",
        )
        return

    # ── INDICADOR VISUAL 🔮🔗🧬 ────────────────────────────────────────────
    # Se envía ANTES de llamar a la IA.
    # Se EDITA con la respuesta final → el usuario ve el indicador mientras
    # la IA procesa y luego aparece la respuesta en el mismo mensaje (limpio).
    indicator = await message.answer("🔮🔗🧬")

    async def _reply(text: str):
        """Edita el indicador con la respuesta. Fallback a nuevo mensaje."""
        try:
            await bot.edit_message_text(
                chat_id    = message.chat.id,
                message_id = indicator.message_id,
                text       = text,
                parse_mode = "MarkdownV2",
            )
        except Exception:
            try:
                await indicator.delete()
            except Exception:
                pass
            await message.answer(text, parse_mode="MarkdownV2")

    try:
        # ── MODO APORTE ────────────────────────────────────────────────────
        if user.fsm_state == "AWAITING_CONTRIB":
            if len(message.text) < 20:
                await _reply(escape_markdown_v2(T[lang]["contrib_short"]))
                return

            async with sem:
                res = await ask_judge(message.text)

            score = float(res.get("score", 0.0))

            if score >= 5.0:
                obj_path = await upload_aporte(
                    uid,
                    message.text,
                    {"score": score, "category": res.get("razon", "general")},
                )
                pts           = int(score * 10)
                user.points  += pts
                user.impact_index += 1
                user.rank     = user.compute_rank()
                await set_state(user, "IDLE")

                cid = obj_path.split("/")[-1] if obj_path else "pending"
                await _reply(
                    escape_markdown_v2(
                        T[lang]["contrib_ok"].format(pts=pts) +
                        f"\n🔗 CID: `{cid}`"
                    )
                )
            else:
                await set_state(user, "IDLE")
                motivo = res.get("razon", "Calidad insuficiente")
                await _reply(
                    escape_markdown_v2(
                        T[lang]["contrib_rejected"] +
                        f"\n💡 {motivo}"
                    )
                )
            return

        # ── MODO CHAT LIBRE — El Pensador + RAG ────────────────────────────
        history = _chat_history[uid]

        async with sem:
            contexto   = await get_related_context(message.text)
            respuesta  = await ask_thinker(
                query   = message.text,
                context = contexto,
                lang    = lang,
                history = history,
            )

        if int(uid) not in MASTER_UIDS:
            user.daily_quota -= 1

        # Actualizar historial
        history.append({"role": "user",      "content": message.text})
        history.append({"role": "assistant", "content": respuesta})
        _chat_history[uid] = history[-_MAX_HISTORY * 2:]

        await _reply(respuesta)

    except Exception as e:
        logger.error("❌ NLP error: %s", e)
        try:
            await _reply(escape_markdown_v2("⚠️ Error interno del Nodo Fantasma\\."))
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════════════════
# ARRANQUE
# ══════════════════════════════════════════════════════════════════════════════
async def main():
    logger.info("🚀 Synergix Nodo Fantasma — Ignición...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
