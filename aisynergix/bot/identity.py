import hashlib
import time
import asyncio
from typing import Dict, Optional, Any
from dataclasses import dataclass, field


SALT_UID = "Synergix_"
UID_HASH_LENGTH = 12

# Tabla de rangos del Diseño Maestro v1.0 (§5.5).
RANK_TABLE = {
    "🌱 Iniciado":     {"min_points": 0,     "daily_limit": 10,         "multiplier": 1.0, "beneficio": "10 aportes/día"},
    "⚡ Explorador":   {"min_points": 500,   "daily_limit": 15,         "multiplier": 1.2, "beneficio": "15 aportes/día + ×1.2"},
    "🔥 Contribuidor": {"min_points": 2000,  "daily_limit": 20,         "multiplier": 1.5, "beneficio": "20 aportes/día + ×1.5"},
    "💎 Experto":      {"min_points": 5000,  "daily_limit": 25,         "multiplier": 1.8, "beneficio": "25 aportes/día + ×1.8"},
    "🌌 Arquitecto":   {"min_points": 10000, "daily_limit": 30,         "multiplier": 2.5, "beneficio": "30 aportes/día + ×2.5"},
    "🔮 Oráculo":      {"min_points": 15000, "daily_limit": float("inf"), "multiplier": 3.0, "beneficio": "Sin límite + ×3.0"},
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
    wallet_address: Optional[str] = None
    human_verified: bool = False
    trust_score: float = 5.0
    # Economía SYNX: saldo contable (no on-chain todavía) registrado en Irys.
    synx_balance: float = 0.0
    # Nodo de comunidad activo: los aportes se asocian a este nodo.
    active_node: Optional[str] = None
    # Wallet BSC custodial generada silenciosamente por el bot (§3.2).  No
    # confundir con wallet_address: esa es la wallet PROPIA del usuario,
    # verificada por firma.
    custodial_address: Optional[str] = None

    def __post_init__(self):
        self.uid_hash = _hash_uid(self.uid)

    @property
    def daily_limit(self) -> int:
        rank_config = RANK_TABLE.get(self.rank, RANK_TABLE["🌱 Iniciado"])
        lim = rank_config["daily_limit"]
        return 9999 if lim == float("inf") else int(lim)

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
            contribution_count=int(tags.get("contribution_count", 0)),
            total_uses_count=int(tags.get("total_uses_count", 0)),
            language=tags.get("language", "es"),
            fsm_state=tags.get("fsm_state", "idle"),
            wallet_address=tags.get("wallet_address") or None,
            human_verified=tags.get("human_verified", "false").lower() == "true",
            trust_score=float(tags.get("trust_score", "5.0")),
            synx_balance=float(tags.get("synx_balance", "0") or 0),
            active_node=tags.get("active_node") or None,
            custodial_address=tags.get("custodial_address") or None,
        )

        if profile.rank not in RANK_TABLE:
            # Migración: el rango guardado pertenece a una tabla anterior
            # (📈 Activo, 🧬 Sincronizado, 🏗️ Arquitecto, 🧠 Mente Colmena…).
            # El rango siempre se deriva de los puntos, así que se recalcula
            # con la tabla vigente en vez de degradar al usuario a Iniciado.
            profile.rank = "🌱 Iniciado"
            profile.calculate_rank()

        return profile

    def to_tags(self) -> Dict[str, str]:
        base: Dict[str, str] = {
            "points": str(self.points),
            "rank": self.rank,
            "daily_aportes_count": str(self.daily_aportes_count),
            "contribution_count": str(self.contribution_count),
            "total_uses_count": str(self.total_uses_count),
            "language": self.language,
            "fsm_state": self.fsm_state,
            "last_seen_ts": str(int(self.last_seen_ts)),
        }
        if self.wallet_address:
            base["wallet_address"] = self.wallet_address.lower()
        base["trust_score"] = f"{self.trust_score:.2f}"
        base["human_verified"] = "true" if self.human_verified else "false"
        base["synx_balance"] = f"{self.synx_balance:.2f}"
        if self.active_node:
            base["active_node"] = self.active_node
        if self.custodial_address:
            base["custodial_address"] = self.custodial_address
        return base

    def update_trust_score(self, delta: float) -> None:
        self.trust_score = max(0.0, min(10.0, self.trust_score + delta))

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
    """
    Cache de perfiles con TTL corto para garantizar que Irys
    sea la fuente de verdad en tiempo real. TTL=30s significa staleness máxima
    de 30 segundos — suficiente para que cualquier escritura externa al
    IdentityManager (p. ej. wallet_verify.py, sync_brain.py) se refleje pronto.
    """

    TTL_SECONDS: int = 300

    def __init__(self, max_size: int = 500):
        self._cache: Dict[int, tuple] = {}  # uid -> (profile, cached_at_ts)
        self._max_size = max_size

    def get(self, uid: int) -> Optional[UserProfile]:
        entry = self._cache.get(uid)
        if entry is None:
            return None
        profile, cached_at = entry
        if time.time() - cached_at > self.TTL_SECONDS:
            # TTL expirado — eliminar y forzar relectura desde Irys
            self._cache.pop(uid, None)
            return None
        return profile

    def set(self, uid: int, profile: UserProfile) -> None:
        self._cache[uid] = (profile, time.time())
        if len(self._cache) > self._max_size:
            oldest = min(
                self._cache.items(),
                key=lambda x: x[1][1],
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
        # Per-user write locks (keyed by uid_hash) serialize all read-modify-write
        # sequences on a profile within this process, so the author's own writes
        # and residual-reward writes can never interleave and clobber each other.
        self._write_locks: Dict[str, asyncio.Lock] = {}
        # Write-ledger (keyed by uid_hash): the counters this process has already
        # SEALED to Irys. Unlike the display cache, it is NEVER invalidated by
        # reads, so increments always build on the true latest value even while
        # Irys's GraphQL index lags (5-30 s). Display still reads pure Irys; this
        # only guarantees the seal uses current data.
        self._sealed: Dict[str, Dict[str, int]] = {}

    _SEALED_FIELDS = (
        "points", "contribution_count", "daily_aportes_count",
        "total_uses_count", "synx_balance",
    )

    def _sealed_base(self, uid_hash: str, profile: "UserProfile", field: str) -> int:
        """Latest known counter: max of the Irys-read value and our sealed ledger."""
        irys_v = getattr(profile, field)
        led = self._sealed.get(uid_hash, {}).get(field)
        return max(irys_v, led) if led is not None else irys_v

    def _record_sealed(self, uid_hash: str, profile: "UserProfile") -> None:
        self._sealed[uid_hash] = {f: getattr(profile, f) for f in self._SEALED_FIELDS}
        if len(self._sealed) > 2000:
            # Soft cap: drop an arbitrary entry (active users re-populate on write).
            self._sealed.pop(next(iter(self._sealed)), None)

    def _lock_for(self, uid_hash: str) -> asyncio.Lock:
        lock = self._write_locks.get(uid_hash)
        if lock is None:
            lock = asyncio.Lock()
            self._write_locks[uid_hash] = lock
        return lock

    def invalidate_cache(self, uid: int) -> None:
        """Drop the cached profile so the next get_profile() re-reads from Irys.
        Used after background writes (e.g. residual rewards) that bypass this
        manager and would otherwise leave stale data in the in-memory cache.
        """
        self._cache.remove(uid)

    def invalidate_cache_by_hash(self, uid_hash: str) -> None:
        """Drop cached profile for a user identified by their uid_hash.
        Used by _process_residual_rewards which only has the obfuscated hash.
        Iterates the cache to find the matching entry (small cache, max 500).
        """
        # Search through the cache entries to find matching uid_hash
        to_remove = None
        for uid, entry in list(self._cache._cache.items()):
            if entry is not None:
                profile, _ = entry
                if profile.uid_hash == uid_hash:
                    to_remove = uid
                    break
        if to_remove is not None:
            self._cache.remove(to_remove)

    async def get_profile(self, uid: int) -> UserProfile:
        from aisynergix.services.irys import read_user_tags, list_aportes
        import logging
        _log = logging.getLogger(__name__)

        cached = self._cache.get(uid)
        if cached:
            return cached

        uid_hash = _hash_uid(uid)
        tags = await read_user_tags(uid_hash)
        profile = UserProfile.from_tags(uid, tags)

        # Detección de "lectura silenciosamente fallida": Irys puede
        # devolver _USER_TAG_DEFAULTS tanto para usuarios genuinamente
        # nuevos como cuando el GraphQL falla o todavía no ha indexado
        # la última escritura.  Para distinguir ambos casos, si el perfil
        # leído parece "todo defaults", verificamos vía list_aportes si
        # el usuario tiene historial real en Irys.  Si lo tiene → la
        # lectura está corrupta: NO cacheamos el perfil falso (la próxima
        # llamada reintentará Irys) y reconstruimos contribution_count
        # a partir de los aportes reales para que "Ver estado" muestre
        # información mínima coherente mientras tanto.
        looks_default = (
            profile.points == 0
            and profile.contribution_count == 0
            and profile.total_uses_count == 0
            and not profile.wallet_address
        )
        if looks_default:
            try:
                aportes = await list_aportes(uid_hash, limit=10)
            except Exception as exc:
                _log.warning(
                    "get_profile: list_aportes falló para uid_hash=%s — "
                    "no cacheo el perfil defaulted (reintentaremos): %s",
                    uid_hash, exc,
                )
                # Sin confirmación, conservador: no cachear → reintento
                # en la siguiente llamada.
                return profile

            if aportes:
                _log.warning(
                    "🛑 Lectura sospechosa para uid_hash=%s: perfil dice "
                    "0 puntos/contribs/uses pero Irys tiene %d aportes. "
                    "No se cachea el perfil; se reintentará en la próxima "
                    "llamada.  contribution_count reconstruido a %d.",
                    uid_hash, len(aportes), len(aportes),
                )
                profile.contribution_count = len(aportes)
                # Crítico: NO llamar a self._cache.set — así forzamos
                # que la próxima get_profile vuelva a leer Irys.
                return profile
            # Cero aportes → usuario genuinamente nuevo.  Seguro cachear.

        self._cache.set(uid, profile)
        return profile

    async def credit_residual(self, uid_hash: str) -> Optional[int]:
        """Atomically +1 points and +1 total_uses_count for an author on Irys.

        Serialized with the same per-user lock as update_profile, so a concurrent
        author write cannot clobber the increment. Returns the new uses count, or
        None on failure.
        """
        from aisynergix.services.irys import read_user_tags, write_user_tags
        import logging
        _log = logging.getLogger(__name__)
        async with self._lock_for(uid_hash):
            try:
                tags = await read_user_tags(uid_hash)
                # Use the write-ledger as the authoritative base for the counters
                # this writer touches, so rapid residual credits aren't lost to
                # Irys index lag.
                base_points = max(int(tags.get("points", 0)),
                                  self._sealed.get(uid_hash, {}).get("points", 0))
                base_uses = max(int(tags.get("total_uses_count", 0)),
                                self._sealed.get(uid_hash, {}).get("total_uses_count", 0))
                tags["points"] = str(base_points + 1)
                tags["total_uses_count"] = str(base_uses + 1)
                await write_user_tags(uid_hash, tags)
                # Update the ledger for the fields we changed.
                led = self._sealed.setdefault(uid_hash, {})
                led["points"] = base_points + 1
                led["total_uses_count"] = base_uses + 1
                # Author's display cache (if any) is now stale — drop it.
                self.invalidate_cache_by_hash(uid_hash)
                return base_uses + 1
            except Exception as exc:
                _log.warning("credit_residual failed for uid_hash=%s: %s", uid_hash, exc)
                return None

    async def update_profile(self, uid: int, profile: UserProfile) -> None:
        """Persist the profile to Irys under the per-user write lock."""
        async with self._lock_for(_hash_uid(uid)):
            await self._do_update_profile(uid, profile)

    async def apply_deltas(
        self,
        uid: int,
        *,
        points: int = 0,
        contribution: int = 0,
        daily: int = 0,
        uses: int = 0,
        synx: float = 0.0,
        trust_delta: float = 0.0,
        language: Optional[str] = None,
        active_node: Optional[str] = None,
    ) -> UserProfile:
        """Atomically apply INCREMENTS to a profile and seal it on Irys.

        The correct way to grow a counter shared by several writers: read the
        latest known value (max of Irys + our last-written cache, to survive
        Irys's 5-30 s index lag between rapid writes) and ADD the delta — never
        write an absolute value computed from a possibly-stale snapshot, which is
        what made point/uses increments silently fail to seal. Serialized per
        user with the same lock as update_profile. Returns the updated profile.
        """
        from aisynergix.services.irys import read_user_tags, write_user_tags
        import logging
        _log = logging.getLogger(__name__)
        uid_hash = _hash_uid(uid)

        async with self._lock_for(uid_hash):
            try:
                tags = await read_user_tags(uid_hash)
                profile = UserProfile.from_tags(uid, tags)
            except Exception as exc:
                _log.error("apply_deltas read failed uid_hash=%s: %s", uid_hash, exc)
                profile = await self.get_profile(uid)

            # Build each counter on the latest known value (max of Irys-read and
            # our write-ledger), then ADD the delta. The ledger survives the
            # cache invalidations done by status reads / residual rewards, so the
            # seal always uses current data even under Irys index lag.
            profile.points = self._sealed_base(uid_hash, profile, "points") + points
            profile.contribution_count = (
                self._sealed_base(uid_hash, profile, "contribution_count") + contribution
            )
            profile.daily_aportes_count = (
                self._sealed_base(uid_hash, profile, "daily_aportes_count") + daily
            )
            profile.total_uses_count = (
                self._sealed_base(uid_hash, profile, "total_uses_count") + uses
            )
            profile.synx_balance = (
                self._sealed_base(uid_hash, profile, "synx_balance") + synx
            )
            if trust_delta:
                profile.update_trust_score(trust_delta)
            if language:
                profile.set_language(language)
            if active_node is not None:
                profile.active_node = active_node or None
            profile.calculate_rank()
            profile.last_seen_ts = time.time()

            try:
                await write_user_tags(uid_hash, profile.to_tags())
                self._record_sealed(uid_hash, profile)
            except Exception as exc:
                _log.error("apply_deltas write failed uid_hash=%s: %s", uid_hash, exc)
            self._cache.set(uid, profile)
            return profile

    async def _do_update_profile(self, uid: int, profile: UserProfile) -> None:
        """Persist the profile to Irys and refresh the local cache.

        Irys write errors are logged but not re-raised — losing a single
        update is recoverable (the next get_user_status will reconcile and
        retry the write).  Silent failures, however, are not: every error
        appears in logs so misbehaviour can be diagnosed.

        SAFETY GUARD: if the profile we are about to write looks like a
        *regression* compared to whatever was last cached (points dropped
        to 0 with no real reason, contribution_count went backwards, etc.),
        refuse the write.  This protects users from data loss when an
        upstream Irys-read failure produces a "fresh user" profile that
        would otherwise overwrite their real history.
        """
        from aisynergix.services.irys import write_user_tags, read_user_tags
        import logging
        _log = logging.getLogger(__name__)

        uid_hash = _hash_uid(uid)

        # Anti-regression check: triggers when the new profile is notably
        # smaller than a known-good reference across multiple counters.
        # We accept a single counter going down (e.g. daily_aportes_count
        # reset) but refuse writes that would strip the user of their
        # cumulative history.
        #
        # Reference selection (in order):
        #   a) cached profile if present (hot path)
        #   b) fresh Irys read otherwise (cold/post-restart path)
        #
        # The cold path is the critical bug fix: previously this guard
        # only fired when the cache had data, so post-restart writes
        # bypassed the check entirely and could overwrite real Irys
        # state with stale/defaulted profiles.
        cached = self._cache._cache.get(uid)
        old_profile: Optional[UserProfile] = None
        ref_source = "none"
        if cached is not None:
            old_profile, _ts = cached
            ref_source = "cache"

        # Always read the latest Irys tags. Two uses:
        #   (a) cold-cache anti-regression reference (when nothing is cached);
        #   (b) merge counters that an EXTERNAL writer (residual rewards) bumps
        #       directly on Irys — total_uses_count and points — so this write,
        #       which may carry a stale cached value, never clobbers them.
        irys_profile: Optional[UserProfile] = None
        try:
            ref_tags = await read_user_tags(uid_hash)
            irys_profile = UserProfile.from_tags(uid, ref_tags)
        except Exception as exc:
            _log.warning(
                "update_profile: Irys reference read failed for uid_hash=%s — "
                "merge/guard degraded: %s",
                uid_hash, exc,
            )

        if old_profile is None and irys_profile is not None:
            old_profile = irys_profile
            ref_source = "irys"

        # Counters are owned by the increment writers (apply_deltas /
        # credit_residual). A plain profile write (fsm_state, language, etc.)
        # must never regress them, so preserve the highest known value.
        if irys_profile is not None:
            profile.points = max(profile.points, irys_profile.points)
            profile.total_uses_count = max(profile.total_uses_count, irys_profile.total_uses_count)
            profile.contribution_count = max(profile.contribution_count, irys_profile.contribution_count)
            profile.daily_aportes_count = max(profile.daily_aportes_count, irys_profile.daily_aportes_count)
            # SYNX lo poseen los acreditadores (apply_deltas); una escritura de
            # perfil normal (idioma, fsm_state…) nunca debe reducirlo.
            profile.synx_balance = max(profile.synx_balance, irys_profile.synx_balance)
            # La referencia a la wallet custodial se escribe una sola vez; un
            # perfil stale sin el campo no debe borrarla del siguiente sellado.
            if not profile.custodial_address and irys_profile.custodial_address:
                profile.custodial_address = irys_profile.custodial_address

        if old_profile is not None:
            regressed_fields = 0
            if profile.points < old_profile.points:
                regressed_fields += 1
            if profile.contribution_count < old_profile.contribution_count:
                regressed_fields += 1
            if profile.total_uses_count < old_profile.total_uses_count:
                regressed_fields += 1
            if regressed_fields >= 2:
                _log.error(
                    "🛑 Refusing to write regressed profile for uid_hash=%s "
                    "(ref=%s): points %d→%d, contribs %d→%d, uses %d→%d.  "
                    "This usually means an Irys read failed and produced "
                    "a 'fresh user' profile.  Data preserved.",
                    uid_hash, ref_source,
                    old_profile.points, profile.points,
                    old_profile.contribution_count, profile.contribution_count,
                    old_profile.total_uses_count, profile.total_uses_count,
                )
                # Restaurar la referencia (verdad de Irys) en caché para
                # que el siguiente acceso post-restart ya tenga el estado
                # real, no el escrito-stale que se acaba de rechazar.
                self._cache.set(uid, old_profile)
                return  # Skip the write entirely; keep cache as-is.

            # Salvaguarda extra: rechazar escrituras "todo ceros" cuando
            # la referencia muestra historial real.  Cubre casos donde
            # un solo counter sería suficiente para evitar el guard
            # anterior pero el patrón de reset total es claramente un bug.
            if (profile.points == 0
                and profile.contribution_count == 0
                and profile.total_uses_count == 0
                and (old_profile.points > 0
                     or old_profile.contribution_count > 0
                     or old_profile.total_uses_count > 0)):
                _log.error(
                    "🛑 Refusing all-zero overwrite for uid_hash=%s "
                    "(ref=%s): points=%d contribs=%d uses=%d.  Likely "
                    "a stale/failed read trying to reset the user.",
                    uid_hash, ref_source,
                    old_profile.points,
                    old_profile.contribution_count,
                    old_profile.total_uses_count,
                )
                self._cache.set(uid, old_profile)
                return

        try:
            await write_user_tags(uid_hash, profile.to_tags())
            _log.debug(
                "✅ profile updated on Irys uid_hash=%s points=%d contribs=%d uses=%d",
                uid_hash, profile.points, profile.contribution_count, profile.total_uses_count,
            )
        except Exception as exc:
            _log.error(
                "❌ Irys write failed for uid_hash=%s — local cache updated but "
                "Irys is now stale (will retry on next reconciliation): %s",
                uid_hash, exc, exc_info=True,
            )
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
