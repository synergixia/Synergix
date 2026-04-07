"""
aisynergix/bot/handlers/commands.py
══════════════════════════════════════════════════════════════════════════════
Handlers de comandos y botones del menú de Synergix.

Registra:
  /start      — bienvenida + sync GF
  /top        — ranking de contribuidores
  /challenge  — challenge semanal activo
  /stats      — estadísticas globales
  /validar    — validación por Mente Colmena+
  BTN_STATUS  — estado del usuario
  BTN_MEMORY  — legado on-chain
  BTN_LANG    — selector de idioma
  BTN_CONTRIBUTE → activa FSM de aporte
══════════════════════════════════════════════════════════════════════════════
"""

import asyncio
import logging
import time
from datetime import datetime, timezone

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
    Message, ReplyKeyboardMarkup, KeyboardButton,
)

from aisynergix.config.constants import (
    TRANSLATIONS as T,
    BTN_CONTRIBUTE, BTN_STATUS, BTN_MEMORY, BTN_LANG,
    RANK_TABLE,
)

logger = logging.getLogger("synergix.handlers")


class Form(StatesGroup):
    waiting_contribution = State()


# ── Helpers ───────────────────────────────────────────────────────────────────
def _tr(uid: int, key: str, user_lang: dict, **kw) -> str:
    lang = user_lang.get(uid, "es")
    text = T.get(lang, T["es"]).get(key, T["es"].get(key, key))
    return text.format(**kw) if kw else text

def _menu_kb(uid: int, user_lang: dict) -> ReplyKeyboardMarkup:
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

def _get_rank_info(pts: int, uid: int, master_uids: set) -> dict:
    if uid in master_uids or pts >= 15000:
        return {"level": 5, "key": "rank_6", "multiplier": 5.0,
                "daily_limit": 999, "min_pts": 15000, "next_pts": None}
    for i in range(len(RANK_TABLE) - 1, -1, -1):
        mn, mult, fw, dlim, key = RANK_TABLE[i]
        if pts >= mn:
            nxt = RANK_TABLE[i+1][0] if i+1 < len(RANK_TABLE) else None
            return {"level": i, "key": key, "multiplier": mult,
                    "daily_limit": dlim, "min_pts": mn, "next_pts": nxt,
                    "fusion_weight": fw}
    return {"level": 0, "key": "rank_1", "multiplier": 1.0,
            "daily_limit": 5, "min_pts": 0, "next_pts": 100}

def _next_rank_str(lang: str, pts: int, uid: int, master_uids: set) -> str:
    r = _get_rank_info(pts, uid, master_uids)
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
# REGISTRO DE HANDLERS
# ══════════════════════════════════════════════════════════════════════════════
def register_command_handlers(
    dp:          Dispatcher,
    bot:         Bot,
    db:          dict,
    user_lang:   dict,
    welcomed_users: set,
    master_uids: set,
    contrib_queue,
    gf_update_user_fn,
    gf_head_user_fn,
    save_db_fn,
    set_user_fn,
    check_daily_limit_fn,
    uid_hash_fn,
) -> None:
    """
    Registra todos los handlers de comandos y botones en el Dispatcher.

    Args:
        dp:                  Dispatcher de aiogram.
        bot:                 Instancia del Bot.
        db:                  DB local (dict mutable).
        user_lang:           Dict {uid: lang} en memoria.
        welcomed_users:      Set de UIDs que ya recibieron welcome.
        master_uids:         Set de UIDs con rango Oráculo permanente.
        contrib_queue:       asyncio.Queue para aportes.
        gf_update_user_fn:   Función para actualizar perfil en GF.
        gf_head_user_fn:     Función HEAD para leer perfil de GF.
        save_db_fn:          Función save_db().
        set_user_fn:         Función _set_user(uid, key, val).
        check_daily_limit_fn:Función check_daily_limit(uid).
        uid_hash_fn:         Función uid_hash(uid) → str.
    """

    # ── /start ────────────────────────────────────────────────────────────────
    @dp.message(CommandStart())
    async def cmd_start(msg: Message) -> None:
        uid  = msg.from_user.id
        name = msg.from_user.first_name or "Anon"

        # Detectar idioma
        if uid not in user_lang:
            tg = (msg.from_user.language_code or "").lower()
            if "zh-hant" in tg or tg == "zh-tw":
                user_lang[uid] = "zh"
            elif tg.startswith("zh"):
                user_lang[uid] = "zh_cn"
            elif tg.startswith("en"):
                user_lang[uid] = "en"
            else:
                user_lang[uid] = "es"

        lang = user_lang[uid]

        # HEAD a GF para sincronizar perfil existente
        if uid not in welcomed_users:
            loop = asyncio.get_running_loop()
            profile = await loop.run_in_executor(None, lambda: gf_head_user_fn(uid))
            if profile.get("exists"):
                meta      = profile.get("meta", {})
                role_lang = meta.get("role", "")
                if "|lang:" in role_lang:
                    sl = role_lang.split("|lang:")[-1]
                    if sl in T:
                        user_lang[uid] = sl
                        lang = sl
                pts_raw = meta.get("points", "0").split("|")
                gf_pts  = int(pts_raw[0]) if pts_raw[0].isdigit() else 0
                uid_s   = str(uid)
                db["reputation"].setdefault(uid_s, {"points":0,"contributions":0,"impact":0})
                db["reputation"][uid_s]["points"] = max(
                    db["reputation"][uid_s].get("points", 0), gf_pts
                )
                save_db_fn()

        challenge = db["global_stats"].get("challenge", "BNB Greenfield DeFi Challenge")
        is_first  = uid not in welcomed_users
        key       = "welcome" if is_first else "welcome_back"

        welcomed_users.add(uid)
        set_user_fn(uid, "welcomed", True)
        set_user_fn(uid, "lang", lang)

        await msg.answer(
            _tr(uid, key, user_lang, name=name, challenge=challenge),
            reply_markup=_menu_kb(uid, user_lang)
        )

        # Actualizar perfil en GF (background)
        loop = asyncio.get_running_loop()
        asyncio.create_task(
            loop.run_in_executor(None, lambda: gf_update_user_fn(uid, name, lang))
        )

        logger.info("👤 /start uid=%d lang=%s new=%s", uid, lang, is_first)

    # ── Botón: Mi Estado ──────────────────────────────────────────────────────
    @dp.message(F.text.in_(BTN_STATUS))
    async def btn_status(msg: Message) -> None:
        uid   = msg.from_user.id
        lang  = user_lang.get(uid, "es")
        uid_s = str(uid)
        name  = msg.from_user.first_name or "Anon"
        rep   = db["reputation"].get(uid_s, {"points":0,"contributions":0,"impact":0})
        pts   = rep.get("points", 0)
        rank  = _get_rank_info(pts, uid, master_uids)
        rk    = T.get(lang, T["es"]).get(rank["key"], rank["key"])
        bn    = T.get(lang, T["es"]).get(f"benefit_{rank['level']+1}", "")
        nri   = _next_rank_str(lang, pts, uid, master_uids)
        await msg.answer(
            T.get(lang, T["es"])["status_msg"].format(
                name=name, pts=pts,
                contribs=rep.get("contributions", 0),
                impact=rep.get("impact", 0),
                rank=rk, benefit=bn,
                challenge=db["global_stats"].get("challenge", ""),
                total=db["global_stats"].get("total_contributions", 0),
                next_rank=nri,
            )
        )

    # ── Botón: Mi Legado ──────────────────────────────────────────────────────
    @dp.message(F.text.in_(BTN_MEMORY))
    async def btn_memory(msg: Message) -> None:
        uid   = msg.from_user.id
        lang  = user_lang.get(uid, "es")
        uid_s = str(uid)
        items = db["memory"].get(uid_s, [])
        rep   = db["reputation"].get(uid_s, {"points":0,"contributions":0})
        tx    = T.get(lang, T["es"])
        if not items:
            await msg.answer(tx["no_memory"]); return
        lines = []
        for i, e in enumerate(items[:5], 1):
            sc_raw = str(e.get("score", "5"))
            sc     = int(sc_raw.split("|")[0]) if sc_raw.split("|")[0].isdigit() else 5
            lines.append(
                f"{i}. [{sc}/10] {e.get('summary','')[:80]}\n"
                f"   CID: {e.get('cid','N/A')[:14]}"
            )
        body  = tx["memory_title"] + "\n".join(lines)
        body += tx["memory_footer"].format(
            pts=rep.get("points",0), contribs=rep.get("contributions",0)
        )
        await msg.answer(body)

    # ── Botón: Idioma ─────────────────────────────────────────────────────────
    @dp.message(F.text.in_(BTN_LANG))
    async def btn_lang(msg: Message) -> None:
        uid  = msg.from_user.id
        lang = user_lang.get(uid, "es")
        kb   = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🇪🇸 Español",  callback_data="lang_es"),
             InlineKeyboardButton(text="🇬🇧 English",  callback_data="lang_en")],
            [InlineKeyboardButton(text="🇨🇳 简体中文", callback_data="lang_zh_cn"),
             InlineKeyboardButton(text="🇹🇼 繁體中文", callback_data="lang_zh")],
        ])
        await msg.answer(T.get(lang, T["es"])["select_lang"], reply_markup=kb)

    @dp.callback_query(F.data.startswith("lang_"))
    async def cb_lang(cb: CallbackQuery) -> None:
        uid  = cb.from_user.id
        lang = cb.data.split("lang_", 1)[1]
        user_lang[uid] = lang
        set_user_fn(uid, "lang", lang)
        await cb.message.answer(
            T.get(lang, T["es"])["lang_set"],
            reply_markup=_menu_kb(uid, user_lang)
        )
        await cb.answer()

    # ── Botón: Contribuir ─────────────────────────────────────────────────────
    @dp.message(F.text.in_(BTN_CONTRIBUTE))
    async def btn_contribute(msg: Message, state: FSMContext) -> None:
        uid  = msg.from_user.id
        lang = user_lang.get(uid, "es")
        can, count, limit = check_daily_limit_fn(uid)
        if not can:
            await msg.answer(_tr(uid, "daily_limit", user_lang,
                                 count=count, limit=limit))
            return
        await msg.answer(T.get(lang, T["es"])["await_contrib"])
        await state.set_state(Form.waiting_contribution)

    # ── /top — ranking ────────────────────────────────────────────────────────
    @dp.message(Command("top"))
    async def cmd_top(msg: Message) -> None:
        uid  = msg.from_user.id
        lang = user_lang.get(uid, "es")
        top  = sorted(db["reputation"].items(),
                      key=lambda x: -x[1].get("points", 0))[:10]
        medals = ["🥇","🥈","🥉"] + ["🏅"] * 7
        lines  = []
        for i, (u_s, rep) in enumerate(top):
            pts   = rep.get("points", 0)
            rank  = _get_rank_info(pts, int(u_s) if u_s.isdigit() else 0, master_uids)
            rk    = T.get(lang, T["es"]).get(rank["key"], "")
            lines.append(f"{medals[i]} #{i+1} — {pts:,} pts | {rk}")
        await msg.answer(T.get(lang, T["es"])["top_title"] + "\n".join(lines))

    # ── /challenge ────────────────────────────────────────────────────────────
    @dp.message(Command("challenge"))
    async def cmd_challenge(msg: Message) -> None:
        uid  = msg.from_user.id
        lang = user_lang.get(uid, "es")
        ch   = db["global_stats"].get("challenge", "")
        await msg.answer(
            T.get(lang, T["es"])["challenge_title"].format(challenge=ch)
        )

    # ── /stats — estadísticas globales ────────────────────────────────────────
    @dp.message(Command("stats"))
    async def cmd_stats(msg: Message) -> None:
        uid   = msg.from_user.id
        lang  = user_lang.get(uid, "es")
        gs    = db.get("global_stats", {})
        total = gs.get("total_contributions", 0)
        users = len(db.get("reputation", {}))
        last  = gs.get("last_fusion", "—")
        wisdom = gs.get("collective_wisdom", "")[:100]
        stats_msgs = {
            "es": (
                f"📊 Synergix — Estadísticas Globales\n\n"
                f"🌐 Usuarios activos: {users}\n"
                f"📦 Aportes inmortales: {total}\n"
                f"🕐 Última fusión: {last[:16] if last else '—'}\n\n"
                f"🧠 Sabiduría colectiva:\n{wisdom}..."
            ),
            "en": (
                f"📊 Synergix — Global Stats\n\n"
                f"🌐 Active users: {users}\n"
                f"📦 Immortal contributions: {total}\n"
                f"🕐 Last fusion: {last[:16] if last else '—'}\n\n"
                f"🧠 Collective wisdom:\n{wisdom}..."
            ),
            "zh_cn": (
                f"📊 Synergix — 全局统计\n\n"
                f"🌐 活跃用户：{users}\n"
                f"📦 不朽贡献：{total}\n"
                f"🕐 最后融合：{last[:16] if last else '—'}\n\n"
                f"🧠 集体智慧：\n{wisdom}..."
            ),
            "zh": (
                f"📊 Synergix — 全局統計\n\n"
                f"🌐 活躍用戶：{users}\n"
                f"📦 不朽貢獻：{total}\n"
                f"🕐 最後融合：{last[:16] if last else '—'}\n\n"
                f"🧠 集體智慧：\n{wisdom}..."
            ),
        }
        await msg.answer(stats_msgs.get(lang, stats_msgs["en"]))

    # ── /validar — Mente Colmena+ puede validar aportes ───────────────────────
    @dp.message(F.text.startswith("/validar"))
    async def cmd_validar(msg: Message) -> None:
        uid  = msg.from_user.id
        lang = user_lang.get(uid, "es")
        uid_s = str(uid)
        pts   = db["reputation"].get(uid_s, {}).get("points", 0)
        rank  = _get_rank_info(pts, uid, master_uids)

        if rank["level"] < 4 and uid not in master_uids:
            no_perm = {
                "es": "🔒 Solo Mente Colmena (5000+ pts) o Oráculo pueden validar aportes.",
                "en": "🔒 Only Hive Mind (5000+ pts) or Oracle can validate contributions.",
                "zh_cn": "🔒 只有蜂巢思维（5000+分）或神谕才能验证贡献。",
                "zh":    "🔒 只有蜂巢思維（5000+分）或神諭才能驗證貢獻。",
            }
            await msg.answer(no_perm.get(lang, no_perm["en"]))
            return

        parts = msg.text.split(maxsplit=2)
        if len(parts) < 3:
            usage = {
                "es": "Uso: /validar {uid_hash} {aprobado|rechazado}",
                "en": "Usage: /validar {uid_hash} {approved|rejected}",
                "zh_cn": "用法：/validar {uid_hash} {approved|rejected}",
                "zh":    "用法：/validar {uid_hash} {approved|rejected}",
            }
            await msg.answer(usage.get(lang, usage["en"]))
            return

        target_hash = parts[1]
        decision    = parts[2].lower()
        ok_msgs = {
            "es": f"✅ Validación registrada: {target_hash[:8]}... → {decision}",
            "en": f"✅ Validation recorded: {target_hash[:8]}... → {decision}",
            "zh_cn": f"✅ 验证已记录：{target_hash[:8]}... → {decision}",
            "zh":    f"✅ 驗證已記錄：{target_hash[:8]}... → {decision}",
        }
        # Incrementar contador de validaciones
        cur = int(db.get("user_settings",{}).get(uid_s,{}).get("validated_count","0"))
        set_user_fn(uid, "validated_count", str(cur + 1))
        await msg.answer(ok_msgs.get(lang, ok_msgs["en"]))
        logger.info("🗳️ /validar uid=%d target=%s decision=%s",
                    uid, target_hash[:8], decision)
