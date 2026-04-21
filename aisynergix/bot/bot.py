import os
import sys
import logging
import asyncio
import time
from typing import Optional

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

# Imports internos
from aisynergix.bot.locales import sync_locales_to_ram, get_i18n
from aisynergix.bot.fsm import fsm_cache
from aisynergix.bot.identity import get_or_create_user, get_rank_info, hash_uid, update_user_state
from aisynergix.ai.manager import ai_manager
from aisynergix.services.greenfield import greenfield
from aisynergix.services.rag_engine import rag

logger = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    logger.critical("No se encontró TELEGRAM_BOT_TOKEN en variables de entorno. Saliendo.")
    sys.exit(1)

# Deshabilitar localmente protección anti-bots strictos que crashean parseo html/json en ciertos mensajes Telegram
bot = Bot(token=TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher()

# --- 🎯 CONSTRUCTORES DE TECLADO ---

def get_main_keyboard(lang_code: str) -> ReplyKeyboardMarkup:
    """Constructor dinámico del menú inamovible (Idéntico a Botones Permanentes)."""
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=get_i18n(lang_code, "btn_contribute"))],
            [KeyboardButton(text=get_i18n(lang_code, "btn_status")), KeyboardButton(text=get_i18n(lang_code, "btn_memory"))],
            [KeyboardButton(text=get_i18n(lang_code, "btn_language"))]
        ],
        resize_keyboard=True,
        is_persistent=True # Siempre visible
    )
    return kb

def get_language_inline_kb() -> InlineKeyboardMarkup:
    """Despliega los 10 idiomas oficiales en grid."""
    flags = [
        ("es", "Español 🇪🇸"), ("en", "English 🇬🇧"), ("zh", "Chino 🇨🇳"), 
        ("hi", "Hindi 🇮🇳"), ("ar", "Árabe 🇸🇦"), ("fr", "Francés 🇫🇷"),
        ("bn", "Bengalí 🇧🇩"), ("pt", "Portugués 🇵🇹"), ("id", "Indonesio 🇮🇩"), ("ur", "Urdu 🇵🇰")
    ]
    
    # Grid 2 Columnas
    buttons = []
    for i in range(0, len(flags), 2):
        row = [InlineKeyboardButton(text=flags[i][1], callback_data=f"lang_{flags[i][0]}")]
        if i+1 < len(flags):
            row.append(InlineKeyboardButton(text=flags[i+1][1], callback_data=f"lang_{flags[i+1][0]}"))
        buttons.append(row)
        
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# --- 🛡️ MIDDLEWARES & TRIGGERS COMUNES ---

async def handle_first_interaction(message: types.Message) -> str:
    """
    Función helper para resolver identidad y fallback language on-the-fly.
    Devuelve lenguaje del usuario y el UID ofuscado.
    """
    uid = message.from_user.id
    tg_lang = message.from_user.language_code
    detect = tg_lang[:2] if tg_lang else "es"
    
    # El corazón de Identity (Hidratación Greenfield)
    profile = await get_or_create_user(uid, detected_lang=detect)
    return profile.get("language", "es"), hash_uid(uid), profile

# --- 🚀 COMMAND /START ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    lang_code, _, profile = await handle_first_interaction(message)
    
    # Determinar si es genesis o recurrente mirando si es rank 1 points 0 y sin total_uses_count
    # Simplified approach for presentation logic:
    if profile.get("points", 0) == 0 and profile.get("daily_aportes_count", 0) == 0:
        msg_key = "welcome"
    else:
        msg_key = "welcome_back"
        
    welcome_text = get_i18n(lang_code, msg_key)
    
    await message.answer(welcome_text, reply_markup=get_main_keyboard(lang_code))

# --- 🌐 EVENTOS INLINE (CAMBIO IDIOMA) ---

@dp.callback_query(lambda c: c.data and c.data.startswith('lang_'))
async def process_language_change(callback_query: types.CallbackQuery):
    new_lang = callback_query.data.split('_')[1]
    uid_ofuscado = hash_uid(callback_query.from_user.id)
    
    # Mutación on-chain Web3
    await update_user_state(callback_query.from_user.id, {"language": new_lang})
    
    # Actualizar estado local FSM para que reaccione al idioma
    fsm_cache.set_state(uid_ofuscado, "idle")

    # Obtener nombre con flag
    flags = {"es":"🇪🇸", "en":"🇬🇧", "zh":"🇨🇳", "hi":"🇮🇳", "ar":"🇸🇦", "fr":"🇫🇷", "bn":"🇧🇩", "pt":"🇵🇹", "id":"🇮🇩", "ur":"🇵🇰"}
    flag = flags.get(new_lang, "🌐")
    
    text = f"✅ Idioma configurado a {new_lang.upper()} {flag}"
    
    # Manda update regenerando el reply keyboard inferior al nuevo idioma
    await bot.send_message(
        callback_query.from_user.id, 
        text, 
        reply_markup=get_main_keyboard(new_lang)
    )
    await callback_query.answer()

# --- 🎛️ CAPTURA DE BOTONES EXACTOS DEL MENÚ ---

@dp.message(F.text)
async def handle_text_interactions(message: types.Message):
    lang_code, uid_ofuscado, profile = await handle_first_interaction(message)
    raw_text = message.text.strip()
    
    # Recuperamos keys de los botones
    btn_contribute = get_i18n(lang_code, "btn_contribute")
    btn_status = get_i18n(lang_code, "btn_status")
    btn_memory = get_i18n(lang_code, "btn_memory")
    btn_language = get_i18n(lang_code, "btn_language")

    current_state = fsm_cache.get_state(uid_ofuscado)

    # 1. 🌐 Botón Idioma
    if raw_text == btn_language:
        fsm_cache.set_state(uid_ofuscado, "idle")
        await message.answer("🌐 Elige tu idioma:", reply_markup=get_language_inline_kb())
        return

    # 2. 📊 Botón Status
    if raw_text == btn_status:
        fsm_cache.set_state(uid_ofuscado, "idle")
        
        status_text = get_i18n(
            lang_code, "status_msg",
            rank=profile.get("rank", "🌱 Iniciado"),
            points=profile.get("points", 0),
            daily_current=profile.get("daily_aportes_count", 0),
            daily_limit=get_rank_info(profile.get("points", 0))[1],
            multipliers="1x" # Logic can be extended for challenge multiplier logic later
        )
        await message.answer(status_text)
        return

    # 3. 🧠 Botón Memoria
    if raw_text == btn_memory:
        fsm_cache.set_state(uid_ofuscado, "idle")
        
        # Recuperar de BNF Greenfield (Subcarpeta mensual y filtrar) -> Simulado a read_aporte real limit list.
        # Por razones de rendimiento en producción real se leería de un index paralelo en data/ o query optimizada
        date_folder = time.strftime("%Y-%m")
        aportes_brutos = await greenfield.list_recent_aportes(date_folder)
        
        mis_aportes = []
        for obj in aportes_brutos:
            # nombre formato: {uid_of}_{ts}.txt
            if obj.get("object_name", "").startswith(f"{uid_ofuscado}_"):
                mis_aportes.append(obj)
                
        if not mis_aportes:
            await message.answer(get_i18n(lang_code, "memory_empty"))
            return
            
        resp = get_i18n(lang_code, "memory_header") + "\\n\\n"
        for idx, m in enumerate(mis_aportes[:5]): # Muestra top 5 recientes de este mes
            cid_mock = m.get("object_name", "")[-10:] # Ofuscacion estética de CID
            resp += get_i18n(lang_code, "memory_entry", score="??", cid=cid_mock) + "\\n"
        resp += "\\n" + get_i18n(lang_code, "memory_footer", ts=time.strftime("%Y-%m-%d"))
        
        await message.answer(resp)
        return

    # 4. 🔥 Botón Contribuir (Activa Modo FSM)
    if raw_text == btn_contribute:
        # Chequeo anti-spam cuota diaria
        _, limit = get_rank_info(profile.get("points", 0))
        if profile.get("daily_aportes_count", 0) >= limit:
            await message.answer(get_i18n(lang_code, "quota_exceeded"))
            return
            
        fsm_cache.set_state(uid_ofuscado, "awaiting_contribution")
        # El JSON pide este mensaje exacto: 
        resp = "🎯 Modo aporte activado! Escribe tu idea. Quedará grabado en la red para siempre de Synergix. 💡 Mínimo 20 caracteres."
        # Como hardcodeó texto exacto en regla, omitimos loc en caso the JSON no match exacto
        await message.answer(resp)
        return

    # -------------------------------------------------------------
    # 📝 FLUJO: CONVERSACIÓN LIBRE VS MODO APORTE
    # -------------------------------------------------------------
    if current_state == "awaiting_contribution":
        # Bloquear modo tras trigger para evitar subida doble indeseada (UX de rebote)
        fsm_cache.set_state(uid_ofuscado, "idle") 
        
        # Flujo de Aporte Web3
        await message.answer("¡Recibido! Tu sabiduría está siendo procesada e inmortalizada. 🔗")
        await process_contribution(message, raw_text, lang_code, uid_ofuscado, profile)
        
    else:
        # Flujo Conversacional / RAG Pensador (Ghost Syncing)
        # 1. Enviar evento al buffer para simular 'Bot Escribiendo...'
        await bot.send_chat_action(chat_id=message.chat.id, action="typing")
        
        # 2. Append history
        fsm_cache.append_history(uid_ofuscado, "user", raw_text)
        current_history = fsm_cache.get_history(uid_ofuscado)
        
        # 3. Disparo al Pensador 
        ia_reply = await ai_manager.chat_with_brain(message.from_user.id, lang_code, current_history, raw_text)
        
        # 4. Append history response
        fsm_cache.append_history(uid_ofuscado, "assistant", ia_reply)
        
        # 5. Respuesta
        await message.answer(ia_reply)

# --- 🔗 WORKER ASINCRONO: APORTES ---

async def process_contribution(message: types.Message, content: str, lang_code: str, uid_ofuscado: str, profile: dict):
    """
    Subrutina delegada para evaluación por IA (Juez) y escritura on-chain en Greenfield.
    """
    verdict = await ai_manager.judge_contribution(content)
    
    # Condicional de rechazos
    if verdict.get("reason") == "contribution_too_short":
        await message.answer(get_i18n(lang_code, "contribution_too_short"))
        return
        
    if verdict.get("is_duplicate", False):
         await message.answer(get_i18n(lang_code, "contribution_duplicate"))
         return
         
    score = float(verdict.get("quality_score", 0.0))
    if score < 5.0:
        texto_rechazo = get_i18n(lang_code, "contribution_rejected") + f" (Motivo del Juez: {verdict.get('reason')})"
        await message.answer(texto_rechazo)
        return

    # EXITO. Calculando Puntos
    points_gained = int(score) 
    suffix = ""
    if score >= 9.5:
        suffix = " 🌟 ¡Aporte legendario!"
        points_gained += 3
    elif score >= 9.0:
        suffix = " ⭐ ¡Aporte de élite!"
        points_gained += 1
        
    is_challenge = verdict.get("related_to_challenge", False)
    if is_challenge:
        points_gained += 5

    # INMORTALIZACIÓN WEB3
    ts = int(time.time())
    tags = {
        "quality_score": score,
        "author_uid": uid_ofuscado,
        "lang": get_i18n(lang_code, "btn_language"), # Referencial general
        "category": verdict.get("category", "general"),
        "impact_index": verdict.get("impact_index", 50)
    }
    
    cid_mocked_or_real = await greenfield.upload_aporte(uid_ofuscado, ts, content, tags)
    generated_cid = cid_mocked_or_real[-15:] # Simplificacion visual
    
    # MUTACIÓN DE IDENTIDAD WEB3
    mutations = {
        "points": profile.get("points", 0) + points_gained,
        "daily_aportes_count": profile.get("daily_aportes_count", 0) + 1
    }
    await update_user_state(message.from_user.id, mutations)
    
    # RESPUESTA AL CLIENTE
    success_key = "contribution_success_challenge" if is_challenge else "contribution_success"
    try:
        succ_txt = get_i18n(
            lang_code, success_key,
            quality_score=f"{score}{suffix}",
            cid=f"G3F-{generated_cid}",
            points_gained=points_gained
        )
    except Exception:
        # Fallback string en caso faltan placeholders
        succ_txt = f"✅ Inmortalizado [Score: {score}]. Ganas +{points_gained} PTS."

    await message.answer(succ_txt)

# --- 🚀 BOOTSTRAP DE EJECUCION (Debe llamarlo sync_brain) ---
async def start_bot():
    """Inyector de loop principal a Aiogram v3."""
    await sync_locales_to_ram()
    logger.info("Bot Synergix Aiogram Booting. (Stateless Mode ON)")
    try:
        await dp.start_polling(bot)
    except Exception as e:
         logger.critical(f"Bot crash en polling: {str(e)}")
