"""
Módulo 2 (1/2): Identidad y Jerarquía Inmortal (identity.py)
---------------------------------------------------------
El Hidratador de Identidades Fantasma para Synergix. 
Resucita usuarios desde DCellar, gestiona su rango dictado por los Metadatos y el hash determinista.
"""

import asyncio
import logging
import time
from typing import Dict, List

from aisynergix.services.greenfield import (
    get_user_metadata,
    hash_uid,
    update_user_metadata,
)

logger = logging.getLogger("synergix.identity")

# ──────────────────────────────────────────────────────────────────────────────
# JERARQUÍA INMORTAL Y CONSTANTES (Spec Oficial)
# ──────────────────────────────────────────────────────────────────────────────

# Mapeo universal de rangos como está en la regla 4, utilizado también por scripts externos (fusion_brain)
RANGOS = {
    0: ("🌱 Iniciado", 0, 5),
    1: ("📈 Activo", 100, 12),
    2: ("🧬 Sincronizado", 500, 25),
    3: ("🏗️ Arquitecto", 1500, 40),
    4: ("🧠 Mente Colmena", 5000, 60),
    5: ("🔮 Oráculo", 15000, 99999),
}

# (puntos_mínimos, rango_tag, límite_diario, nombre_visual)
RANK_TABLE = [
    (0, "🌱 Iniciado", 5, "rank_1"),
    (100, "📈 Activo", 12, "rank_2"),
    (500, "🧬 Sincronizado", 25, "rank_3"),
    (1500, "🏗️ Arquitecto", 40, "rank_4"),
    (5000, "🧠 Mente Colmena", 60, "rank_5"),
    (15000, "🔮 Oráculo", 99999, "rank_6"),
]

SUPPORTED_LANGUAGES = {"es", "en", "zh-hans", "zh-hant"}
DEFAULT_LANGUAGE = "es"

def get_rank_info(points: int) -> Dict[str, any]:
    """Evalúa los puntos actuales y retorna la estructura del rango correspondiente."""
    for i in range(len(RANK_TABLE) - 1, -1, -1):
        min_pts, rank_tag, daily_limit, rank_key = RANK_TABLE[i]
        if points >= min_pts:
            next_min = RANK_TABLE[i + 1][0] if i + 1 < len(RANK_TABLE) else None
            return {
                "level": i,
                "key": rank_key,
                "tag": rank_tag,
                "daily_limit": daily_limit,
                "min_points": min_pts,
                "next_points": next_min,
            }
    # Fallback ultra-seguro por defecto
    return {
        "level": 0, "key": "rank_1", "tag": "🌱 Iniciado", "daily_limit": 5,
        "min_points": 0, "next_points": 100,
    }

def _normalize_language(lang_hint: str) -> str:
    """Detecta locales de OS de telegram para el multi-idioma (Regla 3)."""
    if not lang_hint:
        return DEFAULT_LANGUAGE
    lang = lang_hint.lower().strip()
    if lang.startswith("es"): return "es"
    if lang.startswith("en"): return "en"
    if lang in ("zh", "zh-cn", "zh_hans", "zh-hans", "zh_cn"): return "zh-hans"
    if lang in ("zh-tw", "zh_hant", "zh-hant", "zh_tw"): return "zh-hant"
    return DEFAULT_LANGUAGE

def _tags_to_user_dict(uid_ofuscado: str, tags: Dict[str, str]) -> Dict[str, any]:
    """Transforma los metadatos Web3 en el cerebro en caché L1 de atributos vitales."""
    points = int(tags.get("points", "0"))
    rank_info = get_rank_info(points)
    return {
        "uid_ofuscado": uid_ofuscado,
        "fsm_state": tags.get("fsm_state", "menu_principal"),
        "points": points,
        "rank_tag": tags.get("rank", "🌱 Iniciado"),
        "rank_key": rank_info["key"],
        "rank_level": rank_info["level"],
        "daily_aportes_count": int(tags.get("daily_aportes_count", "0")),
        "daily_limit": rank_info["daily_limit"],
        "daily_remaining": max(0, rank_info["daily_limit"] - int(tags.get("daily_aportes_count", "0"))),
        "total_uses_count": int(tags.get("total_uses_count", "0")),
        "language": tags.get("language", DEFAULT_LANGUAGE),
        "last_seen_ts": int(tags.get("last_seen_ts", "0")),
    }

# ──────────────────────────────────────────────────────────────────────────────
# LÓGICA DE SUPERVIVENCIA Y EXTRACCIÓN ("HIDRATACIÓN FANTASMA")
# ──────────────────────────────────────────────────────────────────────────────

async def hydrate_user(telegram_uid: int, language_hint: str = "") -> Dict[str, any]:
    """
    CRÍTICO: Extrae al usuario desde Greenfield o crea un nuevo archivo de "0 bytes"
    si nunca existió. Verifica y aplica ascensos inmortales sin piedad.
    """
    uid_ofuscado = await hash_uid(telegram_uid)
    tags = await get_user_metadata(uid_ofuscado)
    now_ts = int(time.time())

    # Nuevo usuario detectado en la matriz:
    if not tags:
        tags = {
            "fsm_state": "menu_principal",
            "points": "0",
            "rank": "🌱 Iniciado",
            "daily_aportes_count": "0",
            "total_uses_count": "0",
            "last_seen_ts": str(now_ts),
            "language": _normalize_language(language_hint),
        }
        await update_user_metadata(uid_ofuscado, tags)
        logger.info(f"👤 Identidad Fantasma Forjada en DCellar: {uid_ofuscado}")
        return _tags_to_user_dict(uid_ofuscado, tags)

    # El usuario sobrevive. Tocar last_seen
    tags["last_seen_ts"] = str(now_ts)

    # Auditar su energía vital para Ascensos Automáticos
    points = int(tags.get("points", "0"))
    rank_info = get_rank_info(points)
    current_rank_tag = tags.get("rank", "🌱 Iniciado")
    
    if rank_info["tag"] != current_rank_tag:
        tags["rank"] = rank_info["tag"]
        logger.info(f"⬆️ Ascenso Cristalizado en cadena: {uid_ofuscado} alcanzó {rank_info['tag']} (pts={points})")

    # Adaptabilidad idiomática
    if language_hint:
        new_lang = _normalize_language(language_hint)
        current_lang = tags.get("language", DEFAULT_LANGUAGE)
        if new_lang != current_lang and new_lang in SUPPORTED_LANGUAGES:
            tags["language"] = new_lang

    await update_user_metadata(uid_ofuscado, tags)
    return _tags_to_user_dict(uid_ofuscado, tags)

async def update_user_field(telegram_uid: int, field: str, value: str, sync_now: bool = False) -> None:
    """Altera estados rápidos en Web3. Evita rate limits delegando a fsm (Caché L1)."""
    uid_ofuscado = await hash_uid(telegram_uid)
    tags = await get_user_metadata(uid_ofuscado)
    if not tags:
        return
        
    tags[field] = str(value)
    
    if sync_now:
        await update_user_metadata(uid_ofuscado, tags)
    else:
        # Integración inquebrantable con el Write-behind cache
        from aisynergix.bot.fsm import enqueue_cache_update
        enqueue_cache_update(uid_ofuscado, tags)

async def increment_daily_contributions(telegram_uid: int) -> bool:
    """Regla 4: Mide y previene el límite de asfixia diaria de aportes."""
    uid_ofuscado = await hash_uid(telegram_uid)
    tags = await get_user_metadata(uid_ofuscado)
    if not tags:
        return False
        
    daily = int(tags.get("daily_aportes_count", "0"))
    points = int(tags.get("points", "0"))
    rank_info = get_rank_info(points)
    
    if daily >= rank_info["daily_limit"]:
        return False
        
    tags["daily_aportes_count"] = str(daily + 1)
    await update_user_metadata(uid_ofuscado, tags)
    return True

async def get_daily_remaining(telegram_uid: int) -> int:
    uid_ofuscado = await hash_uid(telegram_uid)
    tags = await get_user_metadata(uid_ofuscado)
    if not tags: return 0
    daily = int(tags.get("daily_aportes_count", "0"))
    points = int(tags.get("points", "0"))
    rank_info = get_rank_info(points)
    return max(0, rank_info["daily_limit"] - daily)

async def add_points(telegram_uid: int, additional_points: int) -> Dict[str, any]:
    """Modificación bruta y pesada de puntos inmortales y validación del rango."""
    uid_ofuscado = await hash_uid(telegram_uid)
    tags = await get_user_metadata(uid_ofuscado)
    if not tags: return {}
    
    old_points = int(tags.get("points", "0"))
    new_points = old_points + additional_points
    tags["points"] = str(new_points)
    
    # Check manual de promoción en el vuelo
    old_rank = tags.get("rank", "🌱 Iniciado")
    new_rank_info = get_rank_info(new_points)
    if new_rank_info["tag"] != old_rank:
        tags["rank"] = new_rank_info["tag"]
        logger.info(f"🏆 Ascenso súbito en caliente: {uid_ofuscado} {old_rank} → {new_rank_info['tag']}")
        
    await update_user_metadata(uid_ofuscado, tags)
    return _tags_to_user_dict(uid_ofuscado, tags)
