"""
Hidratador de identidades fantasma para Synergix.
Resucita usuarios desde Greenfield, aplica ascensos automáticos y maneja límites diarios.
"""

import asyncio
import logging
import time
from typing import Dict, List, Optional, Tuple

from aisynergix.services.greenfield import (
    get_user_metadata,
    hash_uid,
    update_user_metadata,
)

logger = logging.getLogger("synergix.identity")

# ──────────────────────────────────────────────────────────────────────────────
# JERARQUÍA INMORTAL (spec oficial)
# ──────────────────────────────────────────────────────────────────────────────

RANK_TABLE = [
    # (puntos_mínimos, rango_tag, límite_diario, nombre_visual)
    (0, "🌱 Iniciado", 5, "rank_1"),
    (100, "📈 Activo", 12, "rank_2"),
    (500, "🧬 Sincronizado", 25, "rank_3"),
    (1500, "🏗️ Arquitecto", 40, "rank_4"),
    (5000, "🧠 Mente Colmena", 60, "rank_5"),
    (15000, "🔮 Oráculo", 99999, "rank_6"),
]

# Mapeo de idiomas soportados (tags → código interno)
SUPPORTED_LANGUAGES = {"es", "en", "zh-hans", "zh-hant"}
DEFAULT_LANGUAGE = "es"


def get_rank_info(points: int) -> Dict[str, any]:
    """
    Determina el rango actual basado en los puntos inmortales.
    Retorna un dict con toda la información del rango.
    """
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
    # Fallback (nunca debería llegar aquí)
    return {
        "level": 0,
        "key": "rank_1",
        "tag": "🌱 Iniciado",
        "daily_limit": 5,
        "min_points": 0,
        "next_points": 100,
    }


def should_promote(old_points: int, new_points: int) -> bool:
    """
    Verifica si el usuario ha cruzado un umbral de puntos que merece ascenso.
    """
    old_rank_idx = -1
    new_rank_idx = -1
    for i, (min_pts, _, _, _) in enumerate(RANK_TABLE):
        if old_points >= min_pts:
            old_rank_idx = i
        if new_points >= min_pts:
            new_rank_idx = i
    return new_rank_idx > old_rank_idx


# ──────────────────────────────────────────────────────────────────────────────
# HIDRATACIÓN DE USUARIOS
# ──────────────────────────────────────────────────────────────────────────────

async def hydrate_user(telegram_uid: int, language_hint: str = "") -> Dict[str, any]:
    """
    Hidrata (o crea) un perfil de usuario desde Greenfield.
    Retorna un dict con todos los campos del usuario listos para usar.
    """
    uid_ofuscado = await hash_uid(telegram_uid)
    tags = await get_user_metadata(uid_ofuscado)

    now_ts = int(time.time())
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_ts))

    # Si no existe, crear perfil nuevo
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
        logger.info(
            "👤 Nuevo usuario creado: %s (telegram_uid=%d)",
            uid_ofuscado,
            telegram_uid,
        )
        return _tags_to_user_dict(uid_ofuscado, tags)

    # Actualizar last_seen_ts siempre
    tags["last_seen_ts"] = str(now_ts)

    # Aplicar ascensos si corresponde
    points = int(tags.get("points", "0"))
    rank_info = get_rank_info(points)
    current_rank_tag = tags.get("rank", "🌱 Iniciado")
    if rank_info["tag"] != current_rank_tag:
        tags["rank"] = rank_info["tag"]
        logger.info(
            "⬆️  Ascenso automático: %s %s → %s (pts=%d)",
            uid_ofuscado,
            current_rank_tag,
            rank_info["tag"],
            points,
        )

    # Actualizar idioma si se proporciona hint y es diferente
    if language_hint:
        new_lang = _normalize_language(language_hint)
        current_lang = tags.get("language", DEFAULT_LANGUAGE)
        if new_lang != current_lang and new_lang in SUPPORTED_LANGUAGES:
            tags["language"] = new_lang
            logger.debug(
                "🌐 Idioma actualizado: %s %s → %s",
                uid_ofuscado,
                current_lang,
                new_lang,
            )

    # Guardar cambios (si hubo alguno)
    await update_user_metadata(uid_ofuscado, tags)

    return _tags_to_user_dict(uid_ofuscado, tags)


async def update_user_field(
    telegram_uid: int, field: str, value: str, sync_now: bool = False
) -> None:
    """
    Actualiza un campo específico del usuario en Greenfield.
    Si sync_now=False, el cambio se encola en el Write‑Behind Cache (ver fsm.py).
    """
    uid_ofuscado = await hash_uid(telegram_uid)
    tags = await get_user_metadata(uid_ofuscado)
    if not tags:
        logger.warning("Usuario %s no encontrado al actualizar campo %s", uid_ofuscado, field)
        return
    tags[field] = str(value)
    if sync_now:
        await update_user_metadata(uid_ofuscado, tags)
    else:
        # Encolar en caché L1 (la sincronización la hará el cron cada 2 min)
        from aisynergix.bot.fsm import enqueue_cache_update
        enqueue_cache_update(uid_ofuscado, tags)
        logger.debug("📝 Campo encolado en caché L1: %s.%s = %s", uid_ofuscado, field, value)


async def increment_daily_contributions(telegram_uid: int) -> bool:
    """
    Incrementa el contador diario de aportes.
    Retorna True si el usuario aún no ha excedido su límite diario.
    """
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
    """
    Retorna cuántos aportes diarios le quedan al usuario hoy.
    """
    uid_ofuscado = await hash_uid(telegram_uid)
    tags = await get_user_metadata(uid_ofuscado)
    if not tags:
        return 0
    daily = int(tags.get("daily_aportes_count", "0"))
    points = int(tags.get("points", "0"))
    rank_info = get_rank_info(points)
    return max(0, rank_info["daily_limit"] - daily)


async def add_points(telegram_uid: int, additional_points: int) -> Dict[str, any]:
    """
    Añade puntos inmortales a un usuario y aplica ascenso automático.
    Retorna el nuevo estado del usuario.
    """
    uid_ofuscado = await hash_uid(telegram_uid)
    tags = await get_user_metadata(uid_ofuscado)
    if not tags:
        logger.error("Usuario %s no encontrado para añadir puntos", uid_ofuscado)
        return {}
    old_points = int(tags.get("points", "0"))
    new_points = old_points + additional_points
    tags["points"] = str(new_points)
    # Verificar ascenso
    old_rank = tags.get("rank", "🌱 Iniciado")
    new_rank_info = get_rank_info(new_points)
    if new_rank_info["tag"] != old_rank:
        tags["rank"] = new_rank_info["tag"]
        logger.info(
            "🏆 Ascenso por puntos: %s %s → %s (%d → %d pts)",
            uid_ofuscado,
            old_rank,
            new_rank_info["tag"],
            old_points,
            new_points,
        )
    await update_user_metadata(uid_ofuscado, tags)
    return _tags_to_user_dict(uid_ofuscado, tags)


# ──────────────────────────────────────────────────────────────────────────────
# HELPERS INTERNOS
# ──────────────────────────────────────────────────────────────────────────────

def _normalize_language(lang_hint: str) -> str:
    """
    Normaliza un hint de idioma a uno de los códigos soportados.
    """
    if not lang_hint:
        return DEFAULT_LANGUAGE
    lang = lang_hint.lower().strip()
    # Mapeos comunes
    if lang.startswith("es"):
        return "es"
    if lang.startswith("en"):
        return "en"
    if lang in ("zh", "zh-cn", "zh_hans", "zh-hans", "zh_cn"):
        return "zh-hans"
    if lang in ("zh-tw", "zh_hant", "zh-hant", "zh_tw"):
        return "zh-hant"
    return DEFAULT_LANGUAGE


def _tags_to_user_dict(uid_ofuscado: str, tags: Dict[str, str]) -> Dict[str, any]:
    """
    Convierte los tags de Greenfield en un dict amigable para el código.
    """
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
        "last_seen_iso": time.strftime(
            "%Y‑%m‑%d %H:%M UTC",
            time.gmtime(int(tags.get("last_seen_ts", "0"))),
        ),
        "needs_promotion": should_promote(0, points),  # solo para nuevos ascensos
    }


async def batch_get_users(uids: List[int]) -> List[Dict[str, any]]:
    """
    Hidrata múltiples usuarios en paralelo.
    """
    tasks = [hydrate_user(uid) for uid in uids]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    users = []
    for res in results:
        if isinstance(res, Exception):
            logger.error("Error hidratando usuario: %s", res)
            continue
        users.append(res)
    return users


async def get_top_users(limit: int = 10) -> List[Dict[str, any]]:
    """
    Retorna los `limit` usuarios con más puntos inmortales.
    Útil para el leaderboard. Se usa en fusion_brain.py.
    """
    from aisynergix.services.greenfield import list_users
    all_users = await list_users()
    ranked = []
    for uid_ofuscado, tags in all_users:
        try:
            points = int(tags.get("points", "0"))
            ranked.append((points, uid_ofuscado, tags))
        except (ValueError, KeyError):
            continue
    ranked.sort(key=lambda x: x[0], reverse=True)
    top = ranked[:limit]
    result = []
    for points, uid_ofuscado, tags in top:
        result.append(_tags_to_user_dict(uid_ofuscado, tags))
    return result


async def get_total_users_count() -> int:
    """
    Retorna el número total de usuarios registrados en Synergix.
    """
    from aisynergix.services.greenfield import list_users
    users = await list_users()
    return len(users)
