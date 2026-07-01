"""wallet.py — wallet BSC custodial automática (diseño §3.2-3.3).

En /start el bot genera una wallet BSC para el usuario de forma silenciosa:
sin seed phrase, sin dirección hex, sin fricción.  El usuario solo ve su
saldo; la wallet existe para que en fases posteriores el SYNX contable pueda
liquidarse on-chain y para recibir fondos.

Modelo de seguridad
───────────────────
La clave privada NUNCA se persiste en claro.  Se cifra como keystore V3
estándar de Ethereum (``eth_account.Account.encrypt``, scrypt) con una
contraseña única por usuario derivada de una clave maestra del servidor:

    password(uid_hash) = HMAC-SHA256(SYNERGIX_WALLET_MASTER_KEY, uid_hash)

El keystore cifrado se sube a Irys (``data-type=custodial-wallet``), que es
público y permanente.  Esto preserva la propiedad "ghost node": si el
servidor se borra, cada wallet se recupera desde Irys + la clave maestra.
Sin la clave maestra los keystores publicados no sirven de nada.

Si ``SYNERGIX_WALLET_MASTER_KEY`` no está configurada, la función queda
deshabilitada: el bot funciona igual que antes y solo se loguea un aviso.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Clave maestra del servidor.  Debe ser un secreto largo y aleatorio
# (p. ej. `openssl rand -hex 32`).  Distinta de PRIVATE_KEY (la wallet del
# nodo que paga los uploads a Irys).
_MASTER_KEY: str = os.getenv("SYNERGIX_WALLET_MASTER_KEY", "").strip()

# UIDs con una creación de wallet en curso — evita duplicados si el usuario
# manda /start dos veces seguidas antes de que la primera termine.
_creating: set = set()


def wallet_enabled() -> bool:
    return bool(_MASTER_KEY)


def _derive_password(uid_hash: str) -> str:
    """Contraseña de keystore única por usuario, derivada de la clave maestra.

    HMAC-SHA256 → comprometer un keystore no expone los demás, y la clave
    maestra sola (más los datos públicos de Irys) basta para recuperar todo.
    """
    return hmac.new(
        _MASTER_KEY.encode("utf-8"), uid_hash.encode("utf-8"), hashlib.sha256
    ).hexdigest()


async def create_custodial_wallet(uid_hash: str) -> Optional[str]:
    """Genera una wallet nueva, cifra el keystore y lo sella en Irys.

    Retorna la dirección (checksum) o None si la función está deshabilitada
    o la subida falla.  La clave privada solo existe en memoria durante la
    generación; jamás se loguea ni se persiste en claro.
    """
    if not wallet_enabled():
        logger.warning(
            "SYNERGIX_WALLET_MASTER_KEY no configurada — wallet custodial deshabilitada."
        )
        return None

    from eth_account import Account
    from aisynergix.services.irys import write_custodial_wallet

    def _generate() -> tuple:
        acct = Account.create()
        # scrypt es deliberadamente costoso (~1 s); por eso todo el bloque
        # corre en un thread para no bloquear el event loop.
        keystore = Account.encrypt(acct.key, _derive_password(uid_hash))
        return acct.address, keystore

    address, keystore = await asyncio.to_thread(_generate)

    try:
        await write_custodial_wallet(uid_hash, address, keystore)
    except Exception as exc:
        logger.error(
            "❌ No se pudo sellar el keystore custodial de %s en Irys: %s",
            uid_hash, exc,
        )
        # Sin keystore sellado la wallet sería irrecuperable tras un restart:
        # mejor no devolver la dirección y reintentar en el próximo /start.
        return None

    logger.info("👛 Wallet custodial creada para %s → %s", uid_hash, address)
    return address


async def ensure_custodial_wallet(uid: int) -> Optional[str]:
    """Idempotente: devuelve la dirección custodial del usuario, creándola si no existe.

    Orden de resolución:
      1. ``profile.custodial_address`` (camino caliente).
      2. Keystore ya sellado en Irys (recuperación: el perfil perdió el tag).
      3. Crear wallet nueva + sellar keystore.
    En 2 y 3 la dirección se persiste en el perfil.
    """
    if not wallet_enabled():
        return None

    from aisynergix.bot.identity import get_identity_manager, _hash_uid
    from aisynergix.services.irys import read_custodial_wallet

    uid_hash = _hash_uid(uid)
    identity = get_identity_manager()

    profile = await identity.get_profile(uid)
    if profile.custodial_address:
        return profile.custodial_address

    if uid_hash in _creating:
        return None  # otra corrutina ya la está creando
    _creating.add(uid_hash)
    try:
        # Recuperación: quizá el keystore existe pero el perfil no lo referencia.
        address: Optional[str] = None
        try:
            existing = await read_custodial_wallet(uid_hash)
            if existing:
                address = existing.get("address")
        except Exception:
            pass

        if not address:
            address = await create_custodial_wallet(uid_hash)
        if not address:
            return None

        profile = await identity.get_profile(uid)
        profile.custodial_address = address
        await identity.update_profile(uid, profile)
        return address
    finally:
        _creating.discard(uid_hash)


async def load_custodial_account(uid_hash: str):
    """Descifra y devuelve la cuenta (``eth_account`` LocalAccount) del usuario.

    Para uso interno futuro (retiros/liquidación on-chain, Fase 2+).  Retorna
    None si no hay keystore o el descifrado falla.
    """
    if not wallet_enabled():
        return None

    from eth_account import Account
    from aisynergix.services.irys import read_custodial_wallet

    record = await read_custodial_wallet(uid_hash)
    if not record or not record.get("keystore"):
        return None
    try:
        key = await asyncio.to_thread(
            Account.decrypt, record["keystore"], _derive_password(uid_hash)
        )
        return Account.from_key(key)
    except Exception as exc:
        logger.error("No se pudo descifrar el keystore de %s: %s", uid_hash, exc)
        return None


__all__ = [
    "wallet_enabled",
    "create_custodial_wallet",
    "ensure_custodial_wallet",
    "load_custodial_account",
]
