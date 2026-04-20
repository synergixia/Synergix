"""
Módulo 2: Inteligencia Multilingüe y Lectura JSON (locales.py)
---------------------------------------------------------
Carga los archivos JSON de traducciones directo a la memoria RAM.
Hace fallbacks automáticos y gestiona la detección de Telegram + NLP.
"""
import json
import logging
from pathlib import Path
from typing import Dict, Tuple

logger = logging.getLogger("synergix.locales")

LOCALES_DIR = Path(__file__).parent / "locales"

# Diccionario de IDIOMAS SOPORTADOS: "Código": ("Nombre", "Bandera")
LANGUAGES = {
    "es": ("Español", "🇪🇸"), "en": ("English", "🇬🇧"), "zh": ("Chino", "🇨🇳"),
    "hi": ("Hindi", "🇮🇳"), "ar": ("Árabe", "🇸🇦"), "fr": ("Francés", "🇫🇷"),
    "bn": ("Bengalí", "🇧🇩"), "pt": ("Portugués", "🇵🇹"), "id": ("Indonesio", "🇮🇩"),
    "ur": ("Urdu", "🇵🇰")
}

# Aquí guardamos los JSON en memoria RAM para latencia 0ms
_translations = {}

def load_all_locales():
    """Inyecta los JSON en la RAM al vuelo durante el arranque del Bot."""
    if not LOCALES_DIR.exists():
        logger.warning(f"[Locales] El directorio de idiomas no existe en {LOCALES_DIR}")
        return
        
    for file_path in LOCALES_DIR.glob("*.json"):
        lang_code = file_path.stem
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                _translations[lang_code] = json.load(f)
            logger.debug(f"[Locales] Memoria inyectada para Idioma: {lang_code.upper()}")
        except Exception as e:
            logger.error(f"[Locales] Error cargando JSON maestro de {lang_code}: {e}")

# Ejecutamos la inyección RAM justo al importar el módulo
load_all_locales()


def auto_detect_lang(telegram_lang_code: str, user_text: str = "") -> str:
    """
    Detecta el idioma: 
    1. Usa el locale nativo de Telegram.
    2. Si no es reconocido, intenta usar langdetect en el texto (opcional).
    3. Si falla, hace fallback brutal a Inglés ("en").
    """
    base_code = (telegram_lang_code or "en").split("-")[0].lower()
    
    if base_code in LANGUAGES:
        return base_code
        
    if user_text and len(user_text) > 10:
        try:
            from langdetect import detect
            detected = detect(user_text)
            if detected in LANGUAGES: 
                return detected
        except Exception: 
            pass
            
    return "en"


def get_text(lang: str, key: str, **kwargs) -> str:
    """
    Busca la cadena visual en la RAM. 
    Protege el bot interceptando llaves inexistentes o errores de .format()
    Haciendo fallback estructural a Inglés.
    """
    # 1. Recuperamos diccionario del idioma o caemos al inglés
    lang_dict = _translations.get(lang, _translations.get("en", {}))
    
    # 2. Obtenemos el texto en ese idioma, si no está la llave, intentamos en Inglés
    text = lang_dict.get(key)
    if text is None:
        text = _translations.get("en", {}).get(key, f"[FIXME: {key}]")
        
    # 3. Formateamos las variables {puntos}, {name}, protegiendo de KeyError
    try:
        if kwargs:
            return text.format(**kwargs)
        return text
    except KeyError as e:
        logger.error(f"[Locales] Falla Variable de Traducción: Falta la data '{e.args[0]}' para la clave '{key}' en idioma '{lang}'.")
        return text
