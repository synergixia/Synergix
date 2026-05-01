import json
import asyncio
from pathlib import Path
from typing import Dict, Any, Optional

LOCALES_DIR = Path(__file__).parent / "locales"
LOCALES: Dict[str, Dict[str, Any]] = {}
LANG_NAMES = {
    "es": ("Español", "🇪🇸"),
    "en": ("English", "🇬🇧"),
    "zh": ("Chino mandarín", "🇨🇳"),
    "hi": ("Hindi", "🇮🇳"),
    "ar": ("Árabe", "🇸🇦"),
    "fr": ("Francés", "🇫🇷"),
    "bn": ("Bengalí", "🇧🇩"),
    "pt": ("Portugués", "🇵🇹"),
    "id": ("Indonesio", "🇮🇩"),
    "ur": ("Urdu", "🇵🇰"),
}
LANG_LIST = list(LANG_NAMES.keys())
DEFAULT_LANG = "es"


async def load_all_locales() -> Dict[str, Dict[str, Any]]:
    loop = asyncio.get_event_loop()
    for lang in LANG_LIST:
        path = LOCALES_DIR / f"{lang}.json"
        if path.exists():
            data = await loop.run_in_executor(None, lambda p=path: json.loads(p.read_text(encoding="utf-8")))
            LOCALES[lang] = data
    if not LOCALES:
        fallback = LOCALES_DIR / "es.json"
        if fallback.exists():
            LOCALES["es"] = json.loads(fallback.read_text(encoding="utf-8"))
    return LOCALES


def get_locale(lang: str, key: str, default: str = "") -> str:
    data = LOCALES.get(lang, LOCALES.get(DEFAULT_LANG, {}))
    return data.get(key, default or key)


def get_locale_dict(lang: str) -> Dict[str, Any]:
    return LOCALES.get(lang, LOCALES.get(DEFAULT_LANG, {}))


def get_language_name(lang: str) -> str:
    return LANG_NAMES.get(lang, (lang, ""))[0]


def get_language_flag(lang: str) -> str:
    return LANG_NAMES.get(lang, ("", ""))[1]


def get_all_languages() -> Dict[str, tuple]:
    return dict(LANG_NAMES)


def format_locale(lang: str, key: str, **kwargs) -> str:
    template = get_locale(lang, key)
    if template and kwargs:
        try:
            return template.format(**kwargs)
        except KeyError:
            return template
    return template
