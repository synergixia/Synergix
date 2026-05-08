import hashlib
import time
from typing import Dict, Optional, Any
from dataclasses import dataclass, field


SALT_UID = "Synergix_"
UID_HASH_LENGTH = 12

RANK_TABLE = {
    "🌱 Iniciado": {"min_points": 0, "daily_limit": 5},
    "📈 Activo": {"min_points": 100, "daily_limit": 12},
    "🧬 Sincronizado": {"min_points": 500, "daily_limit": 25},
    "🏗️ Arquitecto": {"min_points": 1500, "daily_limit": 40},
    "🧠 Mente Colmena": {"min_points": 5000, "daily_limit": 60},
    "🔮 Oráculo": {"min_points": 15000, "daily_limit": float("inf")},
}

LANGUAGES = {
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


def _hash_uid(uid: int) -> str:
    raw = f"{SALT_UID}{uid}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:UID_HASH_LENGTH]


@dataclass
class UserProfile:
    uid: int
    uid_hash: str = field(init=False)
    points: int = 0
    rank: str = "🌱 Iniciado"
    daily_aportes_count: int = 0
    total_uses_count: int = 0
    language: str = "es"
    last_seen_ts: float = field(default_factory=time.time)
    fsm_state: str = "idle"
    contribution_count: int = 0

    def __post_init__(self):
        self.uid_hash = _hash_uid(self.uid)

    @property
    def daily_limit(self) -> int:
        rank_config = RANK_TABLE.get(self.rank, RANK_TABLE["🌱 Iniciado"])
        return int(rank_config["daily_limit"])

    @property
    def can_contribute(self) -> bool:
        return self.daily_aportes_count < self.daily_limit

    @property
    def remaining_contributions(self) -> int:
        return max(0, self.daily_limit - self.daily_aportes_count)

    def calculate_rank(self) -> Optional[str]:
        sorted_ranks = sorted(
            RANK_TABLE.items(),
            key=lambda x: x[1]["min_points"],
            reverse=True,
        )

        for rank_name, config in sorted_ranks:
            if self.points >= config["min_points"]:
                if rank_name != self.rank:
                    self.rank = rank_name
                    return rank_name
                return None

        return None

    @classmethod
    def from_tags(cls, uid: int, tags: Dict[str, str]) -> "UserProfile":
        profile = cls(
            uid=uid,
            points=int(tags.get("points", 0)),
            rank=tags.get("rank", "🌱 Iniciado"),
            daily_aportes_count=int(tags.get("daily_aportes_count", 0)),
            total_uses_count=int(tags.get("total_uses_count", 0)),
            language=tags.get("language", "es"),
            fsm_state=tags.get("fsm_state", "idle"),
        )

        if profile.rank not in RANK_TABLE:
            profile.rank = "🌱 Iniciado"

        return profile

    def to_tags(self) -> Dict[str, str]:
        return {
            "points": str(self.points),
            "rank": self.rank,
            "daily_aportes_count": str(self.daily_aportes_count),
            "total_uses_count": str(self.total_uses_count),
            "language": self.language,
            "fsm_state": self.fsm_state,
            "last_seen_ts": str(int(self.last_seen_ts)),
        }

    def add_points(self, points_to_add: int) -> None:
        self.points += points_to_add

    def increment_contribution(self) -> None:
        self.daily_aportes_count += 1
        self.contribution_count += 1
        self.last_seen_ts = time.time()

    def set_language(self, lang_code: str) -> bool:
        if lang_code in LANGUAGES:
            self.language = lang_code
            return True
        return False


class UserCache:
    def __init__(self, max_size: int = 500):
        self._cache: Dict[int, UserProfile] = {}
        self._max_size = max_size

    def get(self, uid: int) -> Optional[UserProfile]:
        return self._cache.get(uid)

    def set(self, uid: int, profile: UserProfile) -> None:
        self._cache[uid] = profile
        if len(self._cache) > self._max_size:
            oldest = min(
                self._cache.items(),
                key=lambda x: x[1].last_seen_ts,
            )
            del self._cache[oldest[0]]

    def remove(self, uid: int) -> None:
        self._cache.pop(uid, None)

    def clear(self) -> None:
        self._cache.clear()

    def __len__(self) -> int:
        return len(self._cache)


class IdentityManager:
    def __init__(self):
        self._cache = UserCache(max_size=500)

    async def get_profile(self, uid: int) -> UserProfile:
        from aisynergix.services.greenfield import read_user_tags

        cached = self._cache.get(uid)
        if cached:
            return cached

        uid_hash = _hash_uid(uid)
        tags = await read_user_tags(uid_hash)

        # read_user_tags devuelve defaults si el objeto no existe en Greenfield.
        # NO escribimos aquí — MsgCreateObject con primary_sp_approval=None
        # falla para objetos 0 bytes. La primera escritura ocurre cuando el
        # usuario realiza una acción real (aporte, cambio de idioma).
        profile = UserProfile.from_tags(uid, tags)

        self._cache.set(uid, profile)
        return profile

    async def update_profile(self, uid: int, profile: UserProfile) -> None:
        from aisynergix.services.greenfield import write_user_tags

        uid_hash = _hash_uid(uid)
        try:
            await write_user_tags(uid_hash, profile.to_tags())
        except Exception:
            # El objeto puede no existir aún en Greenfield (usuario nuevo).
            # Los datos quedan en cache RAM hasta que el usuario haga su
            # primera contribución real.
            pass
        self._cache.set(uid, profile)

    async def set_language(self, uid: int, lang_code: str) -> bool:
        profile = await self.get_profile(uid)
        if profile.set_language(lang_code):
            await self.update_profile(uid, profile)
            return True
        return False

    async def get_language(self, uid: int) -> str:
        profile = await self.get_profile(uid)
        return profile.language

    async def check_and_update_rank(self, uid: int, profile: UserProfile) -> Optional[str]:
        new_rank = profile.calculate_rank()
        if new_rank:
            await self.update_profile(uid, profile)
            return new_rank
        return None

    def _hash_uid(self, uid: int) -> str:
        return _hash_uid(uid)


_identity_manager: Optional[IdentityManager] = None


def get_identity_manager() -> IdentityManager:
    global _identity_manager
    if _identity_manager is None:
        _identity_manager = IdentityManager()
    return _identity_manager


__all__ = [
    "IdentityManager",
    "get_identity_manager",
    "UserProfile",
    "UserCache",
    "RANK_TABLE",
    "LANGUAGES",
    "_hash_uid",
]
