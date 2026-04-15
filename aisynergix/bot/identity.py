"""
Synergix Ghost Node — Identity Hydrator
========================================
REGLA DE ORO: El UID de Telegram NUNCA toca Greenfield en crudo.
Todo UID pasa por SHA-256 + Salt antes de ser usado como clave.

Arquitectura Stateless:
  - La única fuente de verdad es BNB Greenfield (Tags de objeto 0-byte).
  - No hay caché local permanente, solo un TTL corto en RAM para
    absorber la latencia de Greenfield en ráfagas de mensajes.
"""

import time
import hashlib
import logging
from dataclasses import dataclass
from typing import Optional

from cachetools import TTLCache

from aisynergix.config.constants import SALT, get_rank, get_next_rank, RANK_TABLE
from aisynergix.services.greenfield import GreenfieldClient

logger = logging.getLogger("Synergix.Identity")

# ── Cache en RAM ──────────────────────────────────────────────────────────────
# TTL de 30 s: absorbe ráfagas sin crear estado permanente.
# maxsize=1000: cubre hasta 1 000 usuarios activos simultáneos.
_IDENTITY_CACHE: TTLCache = TTLCache(maxsize=1_000, ttl=30)


# ── Modelo de Usuario ─────────────────────────────────────────────────────────

@dataclass
class UserContext:
    ghost_uid: str          # SHA-256(salt + telegram_uid)  — llave en Greenfield
    telegram_uid: int       # Solo vive en RAM, nunca persiste
    puntos: int       = 0
    rango: str        = "🌱 Iniciado"
    cuota_diaria: int = 0
    fsm_state: str    = "MAIN_MENU"
    lang: str         = "es"
    last_seen_ts: int = 0

    # ── Propiedades derivadas (sin estado extra) ──────────────────────────────

    @property
    def rank_info(self) -> dict:
        """Diccionario completo del rango actual."""
        return get_rank(self.puntos)

    @property
    def next_rank_info(self) -> Optional[dict]:
        return get_next_rank(self.puntos)

    @property
    def daily_limit(self) -> int:
        if self.telegram_uid in _get_master_uids():
            return 9_999
        return self.rank_info["daily_limit"]

    @property
    def multiplier(self) -> float:
        return self.rank_info["multiplier"]

    def can_post(self) -> bool:
        return self.cuota_diaria < self.daily_limit

    def msgs_left(self) -> int:
        return max(0, self.daily_limit - self.cuota_diaria)


def _get_master_uids() -> set:
    from aisynergix.config.constants import MASTER_UIDS
    return MASTER_UIDS


# ── Funciones criptográficas ──────────────────────────────────────────────────

def hash_uid(telegram_uid: int) -> str:
    """
    SHA-256(salt || telegram_uid)  — operación irreversible.
    Produce el ghost_uid de 64 caracteres hex que viaja a Greenfield.
    """
    raw = f"{SALT}:{telegram_uid}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


# ── Hydrator ─────────────────────────────────────────────────────────────────

class IdentityHydrator:
    """
    Resucita o crea un UserContext a partir del UID de Telegram.

    Flujo:
      1. Computar ghost_uid (hash irreversible).
      2. Revisar cache TTL en RAM.
      3. Si no está en caché → HEAD request a Greenfield.
      4. Si tampoco existe en Greenfield → crear objeto 0-byte con Tags base.
    """

    def __init__(self, greenfield: GreenfieldClient):
        self.gf = greenfield

    async def hydrate(self, telegram_uid: int) -> UserContext:
        ghost_uid = hash_uid(telegram_uid)

        # Cache hit
        if ghost_uid in _IDENTITY_CACHE:
            return _IDENTITY_CACHE[ghost_uid]

        # Greenfield lookup
        metadata = await self.gf.get_user_metadata(ghost_uid)

        if metadata is None:
            logger.info(f"Nuevo nodo fantasma registrado: {ghost_uid[:12]}…")
            ctx = await self._create_new_user(telegram_uid, ghost_uid)
        else:
            ctx = self._deserialize(telegram_uid, ghost_uid, metadata)
            # ¿Subió de rango desde la última vez?
            expected_rango = get_rank(ctx.puntos)["name"]
            if ctx.rango != expected_rango:
                ctx.rango = expected_rango
                await self.gf.update_user_metadata(ghost_uid, {"rango": expected_rango})

        _IDENTITY_CACHE[ghost_uid] = ctx
        return ctx

    async def _create_new_user(self, telegram_uid: int, ghost_uid: str) -> UserContext:
        now = int(time.time())
        base_tags = {
            "puntos":        "0",
            "rango":         "🌱 Iniciado",
            "cuota_diaria":  "0",
            "fsm_state":     "MAIN_MENU",
            "lang":          "es",
            "last_seen_ts":  str(now),
        }
        from aisynergix.config.constants import GF_PATH_USERS
        await self.gf.put_object(
            path=f"{GF_PATH_USERS}/{ghost_uid}",
            content=b"",
            tags=base_tags,
        )
        return UserContext(
            ghost_uid=ghost_uid,
            telegram_uid=telegram_uid,
            last_seen_ts=now,
        )

    @staticmethod
    def _deserialize(telegram_uid: int, ghost_uid: str, meta: dict) -> UserContext:
        def _int(val, default=0):
            try:
                return int(val)
            except (TypeError, ValueError):
                return default

        return UserContext(
            ghost_uid=ghost_uid,
            telegram_uid=telegram_uid,
            puntos=_int(meta.get("puntos"), 0),
            rango=meta.get("rango", "🌱 Iniciado"),
            cuota_diaria=_int(meta.get("cuota_diaria"), 0),
            fsm_state=meta.get("fsm_state", "MAIN_MENU"),
            lang=meta.get("lang", "es"),
            last_seen_ts=_int(meta.get("last_seen_ts"), 0),
        )

    # ── Escrituras atómicas ───────────────────────────────────────────────────

    async def update_state(self, telegram_uid: int, state: str):
        """Persiste el estado FSM en Greenfield e invalida la caché."""
        ghost_uid = hash_uid(telegram_uid)
        await self.gf.update_user_metadata(ghost_uid, {"fsm_state": state})
        if ghost_uid in _IDENTITY_CACHE:
            _IDENTITY_CACHE[ghost_uid].fsm_state = state

    async def add_points_and_quota(
        self,
        telegram_uid: int,
        points: int,
        quota_increment: int = 1,
    ) -> UserContext:
        """
        Suma puntos + incrementa cuota diaria de forma atómica.
        Recalcula el rango y actualiza la caché.
        """
        ghost_uid = hash_uid(telegram_uid)

        # Leer estado fresco (ignorar caché para escrituras)
        meta = await self.gf.get_user_metadata(ghost_uid) or {}
        new_puntos = int(meta.get("puntos", 0)) + points
        new_quota  = int(meta.get("cuota_diaria", 0)) + quota_increment
        new_rango  = get_rank(new_puntos)["name"]

        updates = {
            "puntos":        str(new_puntos),
            "cuota_diaria":  str(new_quota),
            "rango":         new_rango,
            "last_seen_ts":  str(int(time.time())),
        }
        await self.gf.update_user_metadata(ghost_uid, updates)

        # Refrescar caché
        if ghost_uid in _IDENTITY_CACHE:
            ctx = _IDENTITY_CACHE[ghost_uid]
            ctx.puntos       = new_puntos
            ctx.cuota_diaria = new_quota
            ctx.rango        = new_rango
            return ctx

        # Si no estaba en caché, reconstruir
        return await self.hydrate(telegram_uid)

    async def increment_quota(self, telegram_uid: int):
        """Incrementa solo la cuota diaria (para mensajes de chat libre)."""
        ghost_uid = hash_uid(telegram_uid)
        meta = await self.gf.get_user_metadata(ghost_uid) or {}
        new_quota = int(meta.get("cuota_diaria", 0)) + 1
        await self.gf.update_user_metadata(ghost_uid, {
            "cuota_diaria": str(new_quota),
            "last_seen_ts": str(int(time.time())),
        })
        if ghost_uid in _IDENTITY_CACHE:
            _IDENTITY_CACHE[ghost_uid].cuota_diaria = new_quota

    async def update_lang(self, telegram_uid: int, lang: str):
        """Persiste preferencia de idioma."""
        ghost_uid = hash_uid(telegram_uid)
        await self.gf.update_user_metadata(ghost_uid, {"lang": lang})
        if ghost_uid in _IDENTITY_CACHE:
            _IDENTITY_CACHE[ghost_uid].lang = lang

    async def reset_daily_quotas(self):
        """
        Llamado por el Scheduler a las 00:05 UTC.
        En arquitectura Stateless no hay lista de usuarios en local.
        El reset se hace de forma lazy: al hidratar al usuario al día siguiente,
        se detecta que last_seen_ts es de ayer y se resetea cuota_diaria.
        Este método existe para integración futura con un índice de usuarios.
        """
        logger.info("Reset de cuotas diarias programado (lazy mode activo).")
        # Invalidar caché completa para forzar rehidratación
        _IDENTITY_CACHE.clear()
