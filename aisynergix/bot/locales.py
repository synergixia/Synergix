import json
import asyncio
from pathlib import Path
from typing import Dict, Any, Optional


LOCALES_DIR = Path(__file__).parent / "locales"
LOCALES: Dict[str, Dict[str, Any]] = {}

LANG_NAMES = {
    "es": "Español 🇪🇸",
    "en": "English 🇬🇧",
    "zh": "中文 🇨🇳",
    "hi": "हिन्दी 🇮🇳",
    "ar": "العربية 🇸🇦",
    "fr": "Français 🇫🇷",
    "bn": "বাংলা 🇧🇩",
    "pt": "Português 🇵🇹",
    "id": "Bahasa Indonesia 🇮🇩",
    "ur": "اردو 🇵🇰",
}

LANG_FLAGS = {
    "es": "🇪🇸",
    "en": "🇬🇧",
    "zh": "🇨🇳",
    "hi": "🇮🇳",
    "ar": "🇸🇦",
    "fr": "🇫🇷",
    "bn": "🇧🇩",
    "pt": "🇵🇹",
    "id": "🇮🇩",
    "ur": "🇵🇰",
}

TELEGRAM_LANG_MAP = {
    "es": "es",
    "en": "en",
    "zh": "zh",
    "zh-hans": "zh",
    "zh-hant": "zh",
    "hi": "hi",
    "ar": "ar",
    "fr": "fr",
    "bn": "bn",
    "pt": "pt",
    "pt-br": "pt",
    "id": "id",
    "ur": "ur",
}


async def load_all_locales() -> None:
    global LOCALES
    
    if LOCALES:
        return
    
    tasks = []
    for lang_code in LANG_NAMES.keys():
        tasks.append(_load_single_locale(lang_code))
    
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    for lang_code, result in zip(LANG_NAMES.keys(), results):
        if isinstance(result, Exception):
            print(f"[LOCALES] Error cargando {lang_code}: {result}")
            LOCALES[lang_code] = _get_fallback_locale()
            continue
        LOCALES[lang_code] = result
    
    if "es" not in LOCALES:
        LOCALES["es"] = _get_fallback_locale()


async def _load_single_locale(lang_code: str) -> Dict[str, Any]:
    file_path = LOCALES_DIR / f"{lang_code}.json"
    
    if not file_path.exists():
        raise FileNotFoundError(f"Archivo de locale no encontrado: {file_path}")
    
    content = await asyncio.to_thread(file_path.read_text, encoding="utf-8")
    data = json.loads(content)
    
    return data


def _get_fallback_locale() -> Dict[str, Any]:
    return {
        "welcome": "🔥 ¡Bienvenido a Synergix! Tu mente ahora es parte de la red. 🧠",
        "welcome_back": "🌟 ¡Has vuelto! Synergix te esperaba. La red crece contigo.",
        "btn_contribute": "🔥 Contribuir",
        "btn_status": "📊 Ver estado",
        "btn_memory": "🧠 Mi memoria",
        "btn_language": "🌐 Idioma",
        "status_msg": "📊 *Tu estado en Synergix*\n\n🏅 Rango: {rank}\n⭐ Puntos: {points}\n📤 Aportes hoy: {daily_aportes_count}/{daily_limit}\n📚 Total aportes: {contribution_count}\n🔄 Veces usado: {total_uses_count}\n🌐 Idioma: {language}",
        "memory_header": "🧠 <b>Tu memoria inmortal</b>\n✨ <b>{count}</b> sabidurías inmortalizadas en la red\n\n",
        "memory_entry": "🏆 {num}. CID: <code>{cid}</code> ⭐{quality}/10\n📝 {summary}",
        "memory_footer": "\n📈 Score: <b>{points}</b> pts | Contribuciones: <b>{total}</b>",
        "memory_empty": "🧠 Aún no has inmortalizado ninguna sabiduría. ¡Empieza ahora!",
        "contribution_mode": "🎯 ¡Modo aporte activado! Escribe tu idea. Quedará grabada en la red para siempre de Synergix. 💡 Mínimo 20 caracteres.",
        "contribution_received": "¡Recibido! Tu sabiduría está siendo procesada e inmortalizada. 🔗",
        "contribution_success": "✅ *¡Aporte inmortalizado!*\n🔗 CID: `{cid}`\n⭐ Calidad: {quality_score}/10\n💎 Puntos ganados: +{points_gained}\n🏅 Total puntos: {new_total_points}\n📊 Rango: {rank}",
        "contribution_success_challenge": "✅ *¡Aporte inmortalizado!* 🏆\n🔗 CID: `{cid}`\n⭐ Calidad: {quality_score}/10\n💎 Puntos: +{points_gained} (incluye +5 bonus reto)\n🏅 Total: {new_total_points}\n📊 Rango: {rank}\n🎯 ¡Pertenece al reto semanal!",
        "contribution_success_elite": "✅ *¡Aporte inmortalizado!* ⭐ *¡Aporte de Élite!* ⭐\n🔗 CID: `{cid}`\n⭐ Calidad: {quality_score}/10\n💎 Puntos ganados: +{points_gained}\n🏅 Total: {new_total_points}\n📊 Rango: {rank}",
        "contribution_success_legendary": "✅ *¡Aporte inmortalizado!* 🌟 *¡Aporte Legendario!* 🌟\n🔗 CID: `{cid}`\n⭐ Calidad: {quality_score}/10\n💎 Puntos: +{points_gained}\n🏅 Total: {new_total_points}\n📊 Rango: {rank}\n💫 Tu sabiduría resonará por toda la red.",
        "contribution_rejected": "🤔 Tu aporte necesita más profundidad. {feedback}\n\nVuelve a intentarlo con más claridad y originalidad. 💪",
        "contribution_too_short": "✂️ Tu aporte es muy corto. Necesita al menos 20 caracteres. ¡Desarrolla más tu idea!",
        "contribution_duplicate": "🔄 Esta sabiduría ya existe en la red. ¡Intenta con una idea original!",
        "quota_exceeded": "⏳ Has alcanzado tu límite diario de {daily_limit} aportes. Vuelve mañana para seguir inmortalizando sabiduría. 🌅",
        "rank_up": "🎉 *¡Has ascendido de rango!*\n\n🏅 {old_rank} → {new_rank}\n\nTu contribución a la inteligencia colectiva es invaluable. La red te reconoce. 🌟",
        "language_select": "🌐 Elige tu idioma:",
        "language_set": "✅ Idioma configurado a *{lang_name}* {flag}",
        "challenge_broadcast": "🎯 *¡Nuevo reto semanal!*\n\n{challenge_description}\n\n💎 +5 puntos extra por aportes relacionados.\n🔥 ¡Participa ahora!",
        "residual_notify": "💫 ¡Tu sabiduría acaba de ser usada para iluminar a otra mente! +1 punto de regalías.",
        "error_generic": "⚡ Synergix está procesando demasiada sabiduría. Intenta de nuevo en unos segundos.",
    }


def t(key: str, lang: str, **kwargs) -> str:
    locale = LOCALES.get(lang, LOCALES.get("es", {}))
    text = locale.get(key, key)
    
    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, ValueError):
            pass
    
    return text


def get_lang_name(lang_code: str) -> str:
    return LANG_NAMES.get(lang_code, lang_code)


def get_lang_flag(lang_code: str) -> str:
    return LANG_FLAGS.get(lang_code, "")


__all__ = [
    "load_all_locales",
    "LOCALES",
    "LANG_NAMES",
    "LANG_FLAGS",
    "TELEGRAM_LANG_MAP",
    "t",
    "get_lang_name",
    "get_lang_flag",
]
