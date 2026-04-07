"""
aisynergix/bot/middlewares/rate_limit.py
══════════════════════════════════════════════════════════════════════════════
Middlewares de Seguridad y Límites para Synergix.

Implementa:
  · RateLimitMiddleware  — Anti-flood: máx N mensajes por usuario por minuto.
  · GreenFieldHeadMiddleware — Verifica perfil en GF al primer mensaje del día.

Diseño:
  - En memoria (dict) — sin Redis, sin DB. Reset al reiniciar.
  - HEAD a Greenfield solo en primer mensaje del día por usuario.
  - Transparente para el handler si no hay flood.
══════════════════════════════════════════════════════════════════════════════
"""

import logging
import time
from collections import defaultdict
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

logger = logging.getLogger("synergix.middleware")

# ══════════════════════════════════════════════════════════════════════════════
# RATE LIMIT MIDDLEWARE
# ══════════════════════════════════════════════════════════════════════════════
class RateLimitMiddleware(BaseMiddleware):
    """
    Anti-flood: limita mensajes por usuario por ventana de tiempo.

    Por defecto: máx 15 mensajes por 60 segundos.
    Si se supera: silencia el mensaje (no responde, no procesa).
    Al desbloqueo: informa al usuario con un mensaje amigable.

    No afecta a MASTER_UIDS — ellos tienen acceso ilimitado.
    """

    def __init__(self, rate: int = 15, window: int = 60,
                 master_uids: set = None):
        """
        Args:
            rate:        Máx mensajes permitidos por ventana.
            window:      Ventana de tiempo en segundos.
            master_uids: UIDs exentos del rate limit.
        """
        self.rate        = rate
        self.window      = window
        self.master_uids = master_uids or set()

        # {uid: [timestamp1, timestamp2, ...]}
        self._history: dict[int, list[float]] = defaultdict(list)
        # {uid: warned_at}  — para no spamear el mensaje de aviso
        self._warned: dict[int, float] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event:   TelegramObject,
        data:    dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message):
            return await handler(event, data)

        uid = event.from_user.id if event.from_user else None
        if uid is None:
            return await handler(event, data)

        # Masters son inmunes
        if uid in self.master_uids:
            return await handler(event, data)

        now    = time.monotonic()
        cutoff = now - self.window

        # Limpiar historial antiguo
        self._history[uid] = [t for t in self._history[uid] if t > cutoff]

        if len(self._history[uid]) >= self.rate:
            # Flood detectado — informar una vez por bloqueo
            last_warn = self._warned.get(uid, 0)
            if now - last_warn > self.window:
                self._warned[uid] = now
                lang = data.get("user_lang", {}).get(uid, "es")
                msgs = {
                    "es": f"⏸️ Tranquilo, vas muy rápido. Espera {self.window}s. 🔄",
                    "en": f"⏸️ Slow down! Wait {self.window}s. 🔄",
                    "zh_cn": f"⏸️ 请慢一点。等待{self.window}秒。🔄",
                    "zh":    f"⏸️ 請慢一點。等待{self.window}秒。🔄",
                }
                try:
                    await event.answer(msgs.get(lang, msgs["en"]))
                except Exception:
                    pass
            logger.debug("🛡️ Rate limit uid=%d (%d msgs/%ds)",
                         uid, len(self._history[uid]), self.window)
            return  # Descartar mensaje sin procesar

        self._history[uid].append(now)
        return await handler(event, data)


# ══════════════════════════════════════════════════════════════════════════════
# GREENFIELD HEAD MIDDLEWARE
# ══════════════════════════════════════════════════════════════════════════════
class GreenFieldHeadMiddleware(BaseMiddleware):
    """
    Hace HEAD a Greenfield una vez por día por usuario nuevo para:
      1. Verificar si el perfil ya existe on-chain.
      2. Sincronizar puntos y lang desde GF → DB local.

    Operación barata: solo lee tags, no descarga contenido.
    Se ejecuta solo en el primer mensaje del día para minimizar latencia.
    """

    def __init__(self, db: dict, gf_head_fn: Callable,
                 user_lang: dict, master_uids: set = None):
        """
        Args:
            db:          Referencia a la DB local de Synergix.
            gf_head_fn:  Función gf_head_user(uid) → {"exists": bool, "meta": {...}}.
            user_lang:   Dict {uid: lang} en memoria.
            master_uids: UIDs que no necesitan sync desde GF.
        """
        self.db          = db
        self.gf_head     = gf_head_fn
        self.user_lang   = user_lang
        self.master_uids = master_uids or set()

        # {uid: last_sync_ts}
        self._synced: dict[int, float] = {}
        self._SYNC_INTERVAL = 86400  # 24 horas

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event:   TelegramObject,
        data:    dict[str, Any],
    ) -> Any:
        if isinstance(event, Message) and event.from_user:
            uid = event.from_user.id
            now = time.monotonic()

            # Solo sincronizar una vez por día por usuario
            last = self._synced.get(uid, 0)
            if uid not in self.master_uids and (now - last) > self._SYNC_INTERVAL:
                await self._sync_from_gf(uid, event.from_user.language_code or "")
                self._synced[uid] = now

        return await handler(event, data)

    async def _sync_from_gf(self, uid: int, tg_lang: str) -> None:
        """Sincroniza perfil del usuario desde Greenfield a DB local."""
        import asyncio
        uid_s = str(uid)
        try:
            loop    = asyncio.get_running_loop()
            profile = await loop.run_in_executor(None, lambda: self.gf_head(uid))

            if not profile.get("exists"):
                return  # Usuario nuevo — no hay nada que sincronizar

            meta = profile.get("meta", {})

            # Sincronizar lang
            role_lang = meta.get("role", "")
            if "|lang:" in role_lang:
                saved_lang = role_lang.split("|lang:")[-1]
                VALID_LANGS = {"es", "en", "zh_cn", "zh"}
                if saved_lang in VALID_LANGS and uid not in self.user_lang:
                    self.user_lang[uid] = saved_lang
                    logger.debug("🌐 GF sync lang uid=%d → %s", uid, saved_lang)

            # Sincronizar puntos (tomar el máximo para evitar pérdidas)
            pts_raw = meta.get("points", "0").split("|")
            gf_pts  = int(pts_raw[0]) if pts_raw[0].isdigit() else 0

            if gf_pts > 0:
                self.db["reputation"].setdefault(
                    uid_s, {"points": 0, "contributions": 0, "impact": 0}
                )
                current_pts = self.db["reputation"][uid_s].get("points", 0)
                if gf_pts > current_pts:
                    self.db["reputation"][uid_s]["points"] = gf_pts
                    logger.debug("🔄 GF sync pts uid=%d: %d → %d",
                                 uid, current_pts, gf_pts)

        except Exception as e:
            logger.debug("⚠️ GF head sync uid=%d: %s", uid, e)
