"""
Módulo Core: Córtex Telegram / Interacción y Flujo (bot.py)
---------------------------------------------------------
Integra los idiomas JSON (locales.py), la máquina de estado FSM (fsm.py) 
y el conector de Web3/Identidad. 
No posee comandos explícitos excepto /start y el easter egg 'S'. Flujo conversacional orgánico.
"""

import json
import logging
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext

# Importaciones modulares hacia tu arquitectura original Synergix:
from aisynergix.bot.locales import get_text, auto_detect_lang, LANGUAGES
from aisynergix.bot.fsm import UserState      # Tu FSM y Caché L1
from aisynergix.bot.identity import hydrate_user  # Resucitador o creador Web3

# Control Neuronal y Web3 Parcheado:
from aisynergix.ai.local_ia import get_juez_evaluation, get_pensador_chat 
from aisynergix.services.greenfield import upload_aporte

logger = logging.getLogger("synergix.bot")
router = Router()

TOP10_JSON_PATH = Path("/app/aisynergix/data/top10.json")

def get_main_keyboard(lang: str) -> InlineKeyboardMarkup:
    """Retorna los 4 botones maestros traducidos en tiempo real."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=get_text(lang, "btn_contribute"), callback_data="btn_contribute")],
        [InlineKeyboardButton(text=get_text(lang, "btn_status"), callback_data="btn_status")],
        [InlineKeyboardButton(text=get_text(lang, "btn_memory"), callback_data="btn_memory")],
        [InlineKeyboardButton(text=get_text(lang, "btn_lang"), callback_data="btn_lang")]
    ])

# =========================================================================
# FASE 1: IGNICIÓN Y PRESENTACIÓN ÚNICA
# =========================================================================

@router.message(CommandStart())
async def cmd_start(msg: Message, state: FSMContext):
    await state.clear()
    data = await state.get_data()
    
    # Auto-identifica el lenguaje de Telegram si el usuario no tiene uno fijado en RAM
    lang = data.get("lang")
    if not lang:
        lang = auto_detect_lang(msg.from_user.language_code)
        await state.update_data(lang=lang)

    name = msg.from_user.first_name or msg.from_user.username or "Viajero"
    
    # 1. Hidratación en Segundo Plano: Revisa o crea su perfil fantasma de 0 bytes en Web3.
    # Usamos try/except para que un fallo global de blockchain no impida enviar el mensaje en Telegram
    try:
        await hydrate_user(str(msg.from_user.id), language_hint=lang)
    except Exception as e:
        logger.warning(f"[Bot] Fallo menor intentando hidratar la cuenta {msg.from_user.id}: {e}")

    # 2. Mostramos el mensaje (parse_mode HTML vital para la estética)
    txt_bienvenida = get_text(lang, "welcome", name=name)
    await msg.answer(txt_bienvenida, reply_markup=get_main_keyboard(lang), parse_mode="HTML")


# =========================================================================
# FASE 2: GESTIÓN DE BOTONES
# =========================================================================

@router.callback_query(F.data == "btn_contribute")
async def btn_contribute(call: CallbackQuery, state: FSMContext):
    lang = (await state.get_data()).get("lang", "es")
    
    # Atrapamos al usuario en la Máquina de Estados -> Siguiente mensaje es un Aporte.
    await state.set_state(UserState.waiting_for_aporte) 
    
    await call.message.answer(get_text(lang, "contribute_activated"), parse_mode="HTML")
    await call.answer()

@router.callback_query(F.data == "btn_status")
async def btn_status(call: CallbackQuery, state: FSMContext):
    lang = (await state.get_data()).get("lang", "es")
    name = call.from_user.first_name or "Viajero"
    
    # En producción conectarás estos valores leyendo `get_user_metadata()` desde web3
    # Por ahora se manda la plantilla visual con datos dummy para visualizar
    txt = get_text(lang, "status_msg", 
                   total_aportes="Calculando...", tema_challenge="Descentralización", 
                   name=name, points=0, contribuciones=0, total_uses_count=0, 
                   rank="Scout", points_next=10, beneficio="Ninguno", 
                   multiplier=1.0, daily_limit=5)
                   
    await call.message.answer(txt, reply_markup=get_main_keyboard(lang), parse_mode="HTML")
    await call.answer()

@router.callback_query(F.data == "btn_memory")
async def btn_memory(call: CallbackQuery, state: FSMContext):
    lang = (await state.get_data()).get("lang", "es")
    # Lógica base. Luego se sustituye por lectura al SmartContract
    await call.message.answer(get_text(lang, "memory_empty"), reply_markup=get_main_keyboard(lang), parse_mode="HTML")
    await call.answer()

@router.callback_query(F.data == "btn_lang")
async def btn_lang(call: CallbackQuery, state: FSMContext):
    lang = (await state.get_data()).get("lang", "es")
    
    # Renderizamos dinamicamente los 10 idiomas
    kb_list = [[InlineKeyboardButton(text=f"{l_name} {flag}", callback_data=f"setlang_{l_code}")] 
               for l_code, (l_name, flag) in LANGUAGES.items()]
               
    kb = InlineKeyboardMarkup(inline_keyboard=kb_list)
    await call.message.answer(get_text(lang, "language_menu"), reply_markup=kb, parse_mode="HTML")
    await call.answer()

@router.callback_query(F.data.startswith("setlang_"))
async def clb_set_lang(call: CallbackQuery, state: FSMContext):
    new_lang = call.data.split("_")[1]
    
    # Fijamos el nuevo idioma en la RAM (Caché L1 del State)
    await state.update_data(lang=new_lang)
    lang_name, flag = LANGUAGES.get(new_lang, ("English", "🇬🇧"))
    
    # Responde en el nuevo idioma e inyecta los teclados mutados
    txt = get_text(new_lang, "language_set", lang_name=lang_name, flag=flag)
    await call.message.answer(txt, reply_markup=get_main_keyboard(new_lang), parse_mode="HTML")
    await call.answer()


# =========================================================================
# FASE 3: EASTER EGG (COMANDO 'S') Y LECTURA DEL TOP 10 JSON
# =========================================================================

async def process_secret_command_s(msg: Message, lang: str):
    """Intercepta la letra 'S' y devuelve el Top 10 formateado leyendo de RAM/JSON."""
    try:
        if not TOP10_JSON_PATH.exists():
            await msg.answer(get_text(lang, "top10_empty"), parse_mode="HTML")
            return
            
        with open(TOP10_JSON_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        total_users = data.get("total_users", 0)
        top10_list = data.get("top10", [])
        
        # Construimos el texto del Header
        response_text = get_text(lang, "top10_header", total_users=total_users)
        
        medals = ["🥇", "🥈", "🥉"]
        
        # Construimos la lista top 10
        for i, user in enumerate(top10_list):
            medal = medals[i] if i < 3 else "🎖️"
            entry = get_text(lang, "top10_entry", 
                             medal=medal, 
                             n=i+1, 
                             name=user.get("name", "Unknown"), 
                             points=user.get("points", 0), 
                             rank=user.get("rank", ""))
            response_text += entry
            
        await msg.answer(response_text, parse_mode="HTML")
        
    except Exception as e:
        logger.error(f"[Comando S] Fallo al leer top10.json: {e}")
        await msg.answer(get_text(lang, "top10_empty"), parse_mode="HTML")

# =========================================================================
# FASE 4: EL FLUJO LIBRE MAGNÉTICO (Aporte Inmortal vs Conversación Pensador)
# =========================================================================

@router.message(F.text)
async def process_text(msg: Message, state: FSMContext):
    state_actual = await state.get_state()
    lang = (await state.get_data()).get("lang", "es")
    name = msg.from_user.first_name or "Viajero"

    # ========= A. MODO JUDICIAL: EVALUAR APORTE ==================
    if state_actual == getattr(UserState, "waiting_for_aporte", None) or state_actual == getattr(UserState, "waiting_for_aporte", "").state if hasattr(UserState, "waiting_for_aporte") else getattr(getattr(UserState, "waiting_for_aporte", None), "state", None): # Manejo robusto FSM
        
        # Filtro estricto rápido en Telegram
        if len(msg.text) < 20:
            await msg.answer(get_text(lang, "contribution_too_short"), parse_mode="HTML")
            return
            
        wait_msg = await msg.answer(get_text(lang, "contribution_received"), parse_mode="HTML")
        
        # Le enviamos el texto y su idioma al LLM (Qwen2.5) local para JSON Response
        try:
             eval_json = await get_juez_evaluation(msg.text, "Synergix Origin", lang)
        except Exception:
             eval_json = {"approved": False, "quality_score": 0, "reason": "Error en Juez AI. Nodos GGUF colapsados."}

        # Juez Falló o Rechaza el aporte
        if not eval_json.get("approved"):
            rej_txt = get_text(lang, "contribution_rejected", 
                               quality_score=eval_json.get("quality_score", 0), 
                               reason=eval_json.get("reason", ""))
            await wait_msg.edit_text(rej_txt, parse_mode="HTML")
            await state.clear()
            return

        # ===== Aporte Aprobado: Cálculo de Modificadores =====
        q_score = int(eval_json.get("quality_score", 5))
        is_rel = eval_json.get("related_to_challenge", False)
        
        # Score Base (x2) + Reto (+5) + Legendario (+10)
        pts = (q_score * 2) + (5 if is_rel else 0) + (10 if q_score >= 9 else 0)
        
        # Subida Definitiva a Greenfield (DCellar S3 Layer)
        try:
             # Retorna el Hash o la URL real de Web3
             cid = await upload_aporte(str(msg.from_user.id), msg.text, q_score, eval_json.get("category", "General"), eval_json.get("impact_index", 0.5), lang)
        except Exception:
             cid = f"SP_Error_{hash(msg.text)}"

        # Pinta la Victoria al usuario
        template = "contribution_success_challenge" if is_rel else "contribution_success"
        succ_txt = get_text(lang, template, name=name, cid=cid, quality_score=q_score, points_gained=pts)
        
        await wait_msg.edit_text(succ_txt, reply_markup=get_main_keyboard(lang), parse_mode="HTML")
        await state.clear()

    # ========= B. MODO CONVERSACIÓN: EL PENSADOR O COMANDO SECRETO=================
    else:
        text_clean = msg.text.strip().lower()
        
        # Interceptamos "S" o "s" solas para el Huevo de Pascua (Top 10)
        if text_clean == "s":
            await process_secret_command_s(msg, lang)
            return
            
        # Conversación Libre Normal: Pasa el texto a LLaMA.cpp
        try:
            bot_reply = await get_pensador_chat(msg.text, lang)
            await msg.answer(bot_reply, parse_mode="HTML")
        except Exception:
            await msg.answer("🧠 <i>El Pensador está asimilando la red... [Error de LLM Local]</i>", parse_mode="HTML")
