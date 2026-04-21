import asyncio
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field

from aisynergix.config.constants import MASTER_UIDS, get_rank
from aisynergix.services.greenfield import (
    _hash_uid,
    create_user,
    get_user_metadata,
    update_user_metadata,
)

log = logging.getLogger("synergix.identity")

_CACHE_TTL = 600
_CACHE_MAX = 1000


@dataclass
class UserContext:
    uid:                 int
    uid_hash:            str   = ""
    points:              int   = 0
    rank:                str   = "🌱 Iniciado"
    daily_limit:         int   = 5
    daily_aportes_count: int   = 0
    total_uses_count:    int   = 0
    fsm_state:           str   = "IDLE"
    language:            str   = "es"
    last_seen_ts:        int   = 0
    first_name:          str   = ""
    welcomed:            bool  = False
    _dirty:              bool  = field(default=False, repr=False)
    _snap:               dict  = field(default_factory=dict, repr=False)
    _cached_at:          float = field(default_factory=time.monotonic, repr=False)

    def __post_init__(self) -> None:
        if not self.uid_hash:
            self.uid_hash = _hash_uid(self.uid)

    @property
    def quota_remaining(self) -> int:
        return max(0, self.daily_limit - self.daily_aportes_count)

    @property
    def can_contribute(self) -> bool:
        return self.uid in MASTER_UIDS or self.quota_remaining > 0

    def add_points(self, amount: int) -> bool:
        """Suma puntos y actualiza rango. Retorna True si hubo rank-up."""
        old_rank    = self.rank
        self.points += amount
        new_rank, new_limit, _ = get_rank(self.points)
        self.rank        = new_rank
        self.daily_limit = new_limit
        self._dirty      = True
        return self.rank != old_rank

    def consume_quota(self) -> None:
        self.daily_aportes_count += 1
        self._dirty = True

    def set_fsm(self, state: str) -> None:
        self.fsm_state = state
        self._dirty    = True

    def to_gf(self) -> dict:
        """Serializa el contexto a los tags que se escriben en Greenfield."""
        return {
            "points":              self.points,
            "rank":                self.rank,
            "daily_quota":         self.daily_limit,
            "daily_aportes_count": self.daily_aportes_count,
            "total_uses_count":    self.total_uses_count,
            "fsm_state":           self.fsm_state,
            "language":            self.language,
            "last_seen_ts":        int(time.time()),
            "welcomed":            str(self.welcomed).lower(),
        }


@dataclass
class _Entry:
    ctx:     UserContext
    expires: float


class _LRUCache:
    """Caché LRU en RAM con TTL. O(1) en get/set. Thread-safe vía asyncio.Lock."""

    def __init__(self) -> None:
        self._d:  OrderedDict[int, _Entry] = OrderedDict()
        self._mu: asyncio.Lock             = asyncio.Lock()

    async def get(self, uid: int) -> UserContext | None:
        async with self._mu:
            e = self._d.get(uid)
            if e is None:
                return None
            if time.monotonic() > e.expires:
                del self._d[uid]
                return None
            self._d.move_to_end(uid)
            return e.ctx

    async def set(self, ctx: UserContext) -> None:
        async with self._mu:
            uid = ctx.uid
            if uid in self._d:
                self._d.move_to_end(uid)
            self._d[uid] = _Entry(
                ctx     = ctx,
                expires = time.monotonic() + _CACHE_TTL,
            )
            if len(self._d) > _CACHE_MAX:
                self._d.popitem(last=False)

    async def invalidate(self, uid: int) -> None:
        async with self._mu:
            self._d.pop(uid, None)


_cache     = _LRUCache()
_uid_locks: dict[int, asyncio.Lock] = {}
_locks_mu  = asyncio.Lock()


async def _get_lock(uid: int) -> asyncio.Lock:
    async with _locks_mu:
        if uid not in _uid_locks:
            _uid_locks[uid] = asyncio.Lock()
        return _uid_locks[uid]


async def hydrate_user(uid: int, first_name: str = "",
                       tg_lang: str | None = None) -> UserContext:
    """
    Resucita al usuario en RAM desde Greenfield.

    Flujo:
      1. Cache LRU hit (TTL 10 min) → retorno instantáneo ~0ms.
      2. Lock por UID (evita doble-hidratación concurrente).
      3. HEAD a aisynergix/users/{uid_hash} → lee Tags.
      4. Si 404 → create_user() con tags base → devuelve defaults.
      5. Almacena en LRU y retorna UserContext.

    El uid real de Telegram NUNCA sale del servidor.
    """
    cached = await _cache.get(uid)
    if cached:
        cached.first_name = first_name or cached.first_name
        return cached

    lock = await _get_lock(uid)
    async with lock:
        cached = await _cache.get(uid)
        if cached:
            return cached

        from aisynergix.bot.locales import detect_lang
        detected_lang = detect_lang(tg_lang)

        meta = await get_user_metadata(uid)

        if meta is None:
            log.info("Nuevo usuario uid=%d → registrando en GF", uid)
            await create_user(uid, detected_lang)
            ctx = UserContext(
                uid                 = uid,
                uid_hash            = _hash_uid(uid),
                points              = 0,
                rank                = "🌱 Iniciado",
                daily_limit         = 5,
                daily_aportes_count = 0,
                total_uses_count    = 0,
                fsm_state           = "IDLE",
                language            = detected_lang,
                last_seen_ts        = int(time.time()),
                first_name          = first_name,
                welcomed            = False,
                _snap               = {},
            )
        else:
            pts           = meta.get("points", 0)
            rank_n, lim, _ = get_rank(pts)
            ctx = UserContext(
                uid                 = uid,
                uid_hash            = meta.get("uid_hash", _hash_uid(uid)),
                points              = pts,
                rank                = meta.get("rank",                 rank_n),
                daily_limit         = int(meta.get("daily_quota",      lim)),
                daily_aportes_count = int(meta.get("daily_aportes_count", 0)),
                total_uses_count    = int(meta.get("total_uses_count",  0)),
                fsm_state           = meta.get("fsm_state",             "IDLE"),
                language            = meta.get("language",              detected_lang),
                last_seen_ts        = int(meta.get("last_seen_ts",      0)),
                first_name          = first_name,
                welcomed            = meta.get("welcomed", "false") == "true",
                _snap               = meta.copy(),
            )

        await _cache.set(ctx)
        log.debug("Hydrate uid=%d uid_h=%s pts=%d rank=%s lang=%s",
                  uid, ctx.uid_hash, ctx.points, ctx.rank, ctx.language)
        return ctx


async def dehydrate_user(ctx: UserContext) -> None:
    """
    Persiste en Greenfield solo los campos que cambiaron respecto al snapshot.
    Si no hay cambios, no hace ninguna petición (cero gas).
    """
    current = ctx.to_gf()
    changes = {
        k: v for k, v in current.items()
        if str(ctx._snap.get(k, "")) != str(v)
    }
    if not changes:
        return

    ok = await update_user_metadata(ctx.uid, current)
    if ok:
        ctx._snap      = current.copy()
        ctx._dirty     = False
        ctx._cached_at = time.monotonic()
        await _cache.set(ctx)
        log.debug("Dehydrate uid=%d OK → %s", ctx.uid, list(changes.keys()))
    else:
        log.warning("Dehydrate falló uid=%d", ctx.uid)


async def invalidate_cache(uid: int) -> None:
    """Fuerza re-fetch de GF en el próximo mensaje del usuario."""
    await _cache.invalidate(uid)
    log.debug("Cache invalidada uid=%d", uid)


def cache_stats() -> dict:
    return {"ttl_s": _CACHE_TTL, "max": _CACHE_MAX}
