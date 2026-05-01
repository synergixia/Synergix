import hashlib
import time
import asyncio
from typing import Dict, Optional, Any, Tuple
from datetime import datetime, timezone

from aisynergix.services.greenfield import greenfield

SALT = "Synergix_"

RANK_HIERARCHY = [
    ("🌱 Iniciado", 0, 5),
    ("📈 Activo", 100, 12),
    ("🧬 Sincronizado", 500, 25),
    ("🏗️ Arquitecto", 1500, 40),
    ("🧠 Mente Colmena", 5000, 60),
    ("🔮 Oráculo", 15000, -1),
]


def hash_uid(uid: str) -> str:
    raw = hashlib.sha256(f"{SALT}{uid}".encode()).hexdigest()
    return raw[:12]


def hash_uid_full(uid: str) -> str:
    return hashlib.sha256(f"{SALT}{uid}".encode()).hexdigest()


def get_rank_for_points(points: int) -> Tuple[str, int, int]:
    for rank_name, min_pts, limit in reversed(RANK_HIERARCHY):
        if points >= min_pts:
            return (rank_name, RANK_HIERARCHY.index((rank_name, min_pts, limit)) + 1, limit)
    return ("🌱 Iniciado", 1, 5)


def get_daily_limit_from_points(points: int) -> int:
    _, _, limit = get_rank_for_points(points)
    return limit


class UserProfile:
    __slots__ = (
        "uid", "uid_hash", "fsm_state", "points", "rank", "rank_level",
        "daily_aportes_count", "total_uses_count", "language", "last_seen_ts",
        "daily_limit", "exists",
    )

    def __init__(self, uid: str):
        self.uid = uid
        self.uid_hash = hash_uid(uid)
        self.fsm_state = "idle"
        self.points = 0
        self.rank = "🌱 Iniciado"
        self.rank_level = 1
        self.daily_aportes_count = 0
        self.total_uses_count = 0
        self.language = "es"
        self.last_seen_ts = 0
        self.daily_limit = 5
        self.exists = False

    @classmethod
    async def hydrate(cls, uid: str) -> "UserProfile":
        profile = cls(uid)
        try:
            tags = await greenfield.get_user_tags(uid)
            profile.exists = True
            profile.fsm_state = tags.get("fsm_state", "idle")
            profile.points = int(tags.get("points", "0"))
            profile.rank = tags.get("rank", "🌱 Iniciado")
            profile.daily_aportes_count = int(tags.get("daily_aportes_count", "0"))
            profile.total_uses_count = int(tags.get("total_uses_count", "0"))
            profile.language = tags.get("language", "es")
            profile.last_seen_ts = int(tags.get("last_seen_ts", "0"))
            rn, rl, dl = get_rank_for_points(profile.points)
            profile.rank = rn
            profile.rank_level = rl
            profile.daily_limit = dl
        except Exception:
            profile.exists = False
            profile.fsm_state = "idle"
            profile.points = 0                                                                                   profile.rank = "🌱 Iniciado"                                                                         profile.rank_level = 1                                                                               profile.daily_aportes_count = 0                                                                      profile.total_uses_count = 0                                                                         profile.language = "es"                                                                              profile.last_seen_ts = int(time.time())                                                              profile.daily_limit = 5                                                                              try:                                                                                                     await greenfield.get_user_tags(uid)                                                                  profile.exists = True                                                                            except Exception:                                                                                        pass                                                                                         return profile                                                                                                                                                                                        def to_dict(self) -> Dict[str, Any]:                                                                     return {                                                                                                 "uid_hash": self.uid_hash,                                                                           "fsm_state": self.fsm_state,                                                                         "points": self.points,                                                                               "rank": self.rank,                                                                                   "rank_level": self.rank_level,                                                                       "daily_aportes_count": self.daily_aportes_count,                                                     "total_uses_count": self.total_uses_count,                                                           "language": self.language,                                                                           "last_seen_ts": self.last_seen_ts,                                                                   "daily_limit": self.daily_limit,                                                                     "exists": self.exists,                                                                           }                                                                                                                                                                                                     async def save(self) -> None:                                                                            tags = {                                                                                                 "fsm_state": self.fsm_state,                                                                         "points": str(self.points),                                                                          "rank": self.rank,                                                                                   "daily_aportes_count": str(self.daily_aportes_count),                                                "total_uses_count": str(self.total_uses_count),                                                      "language": self.language,                                                                           "last_seen_ts": str(int(time.time())),                                                           }                                                                                                    await greenfield.set_user_tags(self.uid, tags)                                                                                                                                                        async def add_points(self, amount: int) -> int:                                                          self.points += amount                                                                                old_rank = self.rank                                                                                 rn, rl, dl = get_rank_for_points(self.points)                                                        self.rank = rn                                                                                       self.rank_level = rl                                                                                 self.daily_limit = dl                                                                                await self.save()                                                                                    return self.points                                                                                                                                                                                    async def increment_uses(self, amount: int = 1) -> int:
        self.total_uses_count += amount
        await greenfield.set_user_tag(self.uid, "total_uses_count", str(self.total_uses_count))              return self.total_uses_count                                                                                                                                                                          async def increment_daily(self) -> int:                                                                  self.daily_aportes_count += 1                                                                        await greenfield.set_user_tag(self.uid, "daily_aportes_count", str(self.daily_aportes_count))        return self.daily_aportes_count                                                                                                                                                                       async def set_fsm_state(self, state: str) -> None:                                                       self.fsm_state = state                                                                               await greenfield.set_user_tag(self.uid, "fsm_state", state)                                                                                                                                           async def set_language(self, lang: str) -> None:                                                         self.language = lang                                                                                 await greenfield.set_user_language(self.uid, lang)                                                                                                                                                    def can_contribute(self) -> Tuple[bool, str]:                                                            if self.daily_limit > 0 and self.daily_aportes_count >= self.daily_limit:                                return (False, "quota_exceeded")                                                                 return (True, "")                                                                                                                                                                                     def is_new_user(self) -> bool:                                                                           return not self.exists or self.total_uses_count == 0                                         
                                                                                                     class ProfileCache:                                                                                      def __init__(self, ttl: int = 300):                                                                      self._cache: Dict[str, Tuple[UserProfile, float]] = {}                                               self._lock = asyncio.Lock()                                                                          self._ttl = ttl                                                                                                                                                                                       async def get(self, uid: str) -> UserProfile:
        async with self._lock:
            now = time.time()
            entry = self._cache.get(uid)
            if entry and (now - entry[1]) < self._ttl:
                profile = entry[0]
                profile.last_seen_ts = int(now)
                return profile
        profile = await UserProfile.hydrate(uid)
        async with self._lock:
            self._cache[uid] = (profile, time.time())
        return profile

    async def invalidate(self, uid: str) -> None:
        async with self._lock:
            self._cache.pop(uid, None)

    async def set_fsm(self, uid: str, state: str) -> None:
        async with self._lock:
            entry = self._cache.get(uid)
            if entry:
                entry[0].fsm_state = state
        await greenfield.set_user_tag(uid, "fsm_state", state)

    async def cleanup(self) -> None:
        async with self._lock:
            now = time.time()
            expired = [uid for uid, (_, ts) in self._cache.items() if (now - ts) > self._ttl]
            for uid in expired:
                del self._cache[uid]


profile_cache = ProfileCache(ttl=300)
