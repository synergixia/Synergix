import asyncio
import logging
import time
from dataclasses import dataclass, field

from aisynergix.services.greenfield import (
    get_user_metadata, update_user_metadata, create_user
)
from aisynergix.config.constants import RANK_TABLE

logger = logging.getLogger("synergix.identity")

# Cache RAM — evita HEAD a GF en cada mensaje
_cache:    dict[str, "UserContext"] = {}
_CACHE_TTL = 45  # segundos


@dataclass
class UserContext:
    uid:            str
    points:         int  = 0
    rank:           str  = "🌱 Iniciado"
    fsm_state:      str  = "IDLE"
    daily_quota:    int  = 5
    language:       str  = "es"
    impact_index:   int  = 0
    _dirty:         bool = field(default=False, repr=False)
    _cached_at:     float = field(default_factory=time.time, repr=False)
    _original_state: dict = field(default_factory=dict, repr=False)

    def get_rank_info(self) -> dict:
        current = RANK_TABLE[0]
        nxt     = None
        for i, r in enumerate(RANK_TABLE):
            if self.points >= r["min_pts"]:
                current = r
                nxt     = RANK_TABLE[i + 1] if i + 1 < len(RANK_TABLE) else None
        return {
            "name":      current["name"],
            "benefit":   current["benefit"],
            "limit":     current["limit"],
            "next_pts":  nxt["min_pts"] if nxt else 0,
            "next_rank": nxt["name"]    if nxt else None,
        }

    def compute_rank(self) -> str:
        current = RANK_TABLE[0]["name"]
        for r in RANK_TABLE:
            if self.points >= r["min_pts"]:
                current = r["name"]
        return current

    def to_dict(self) -> dict:
        return {
            "points":       self.points,
            "rank":         self.rank,
            "fsm_state":    self.fsm_state,
            "daily_quota":  self.daily_quota,
            "language":     self.language,
            "impact_index": self.impact_index,
        }


async def hydrate_user(uid: str) -> UserContext:
    """
    Hidrata el usuario desde GF o desde cache RAM.

    Flujo:
      1. Cache hit (45s) → retorno instantáneo.
      2. HEAD a users/{ghost_id} → si existe, leer meta-headers.
      3. Si 404 → create_user() → crea objeto 0 bytes con tags iniciales.
      4. Retornar UserContext con los datos de GF.
    """
    # 1. Cache hit
    cached = _cache.get(uid)
    if cached and (time.time() - cached._cached_at) < _CACHE_TTL:
        return cached

    # 2. Fetch desde GF
    meta = await get_user_metadata(uid)

    if meta is None:
        # 3. Usuario nuevo — crear en GF
        logger.info("🆕 Registrando nuevo usuario en GF: uid=%s", uid[:6] + "***")
        ok = await create_user(uid)
        if not ok:
            logger.warning("⚠️  create_user falló — usando defaults en RAM")
        meta = {
            "points": 0, "rank": "🌱 Iniciado", "fsm_state": "IDLE",
            "daily_quota": 5, "language": "es", "impact_index": 0,
        }

    ctx = UserContext(
        uid          = uid,
        points       = int(meta.get("points",       0)),
        rank         = meta.get("rank",             "🌱 Iniciado"),
        fsm_state    = meta.get("fsm_state",         "IDLE"),
        daily_quota  = int(meta.get("daily_quota",  5)),
        language     = meta.get("language",          "es"),
        impact_index = int(meta.get("impact_index", 0)),
        _original_state = meta.copy(),
    )

    _cache[uid] = ctx
    logger.debug("💧 Hydrate uid=%s pts=%d rank=%s",
                 uid[:6] + "***", ctx.points, ctx.rank)
    return ctx


async def dehydrate_user(ctx: UserContext) -> None:
    """
    Sincroniza cambios de vuelta a Greenfield.
    Solo escribe si hay diferencias respecto al estado original.
    """
    current = ctx.to_dict()
    updates = {
        k: v for k, v in current.items()
        if str(ctx._original_state.get(k, "")) != str(v)
    }

    if not updates:
        return

    logger.debug("💧 Dehydrate uid=%s → %s", ctx.uid[:6] + "***", updates)
    ok = await update_user_metadata(ctx.uid, current)
    if ok:
        ctx._original_state = current.copy()
        ctx._cached_at      = time.time()
        _cache[ctx.uid]     = ctx


def evict_cache(uid: str) -> None:
    """Elimina usuario de la cache (fuerza re-fetch de GF)."""
    _cache.pop(uid, None)
