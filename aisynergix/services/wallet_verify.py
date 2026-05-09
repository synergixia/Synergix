"""
wallet_verify.py — EIP-191 personal_sign verification for the /verify flow.

Flow:
1. Bot shows the user a challenge message to sign (e.g. with MetaMask / Trust Wallet).
2. User pastes the signature hex.
3. Bot recovers the signer address and stores it in the user's Greenfield tags.

The bot never requests the user's private key.
"""

import hashlib
import logging
import os
import re
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Challenge template — injected with the user's Telegram uid_hash to prevent replay
_CHALLENGE_TEMPLATE = (
    "Synergix Identity Verification\n"
    "User: {uid_hash}\n"
    "Timestamp: {ts}\n"
    "This signature proves wallet ownership. It cannot be used for transactions."
)

# Signatures expire after 10 minutes
_CHALLENGE_TTL = 600

# In-memory store: uid → (challenge_text, issued_at)
_pending: dict = {}


def build_challenge(uid_hash: str) -> str:
    """Generate a unique challenge string for the user to sign."""
    ts = int(time.time())
    text = _CHALLENGE_TEMPLATE.format(uid_hash=uid_hash, ts=ts)
    _pending[uid_hash] = (text, ts)
    return text


def _is_hex_address(s: str) -> bool:
    return bool(re.fullmatch(r"0x[0-9a-fA-F]{40}", s.strip()))


def _is_hex_signature(s: str) -> bool:
    cleaned = s.strip()
    if cleaned.startswith("0x"):
        cleaned = cleaned[2:]
    return bool(re.fullmatch(r"[0-9a-fA-F]{130}", cleaned))


def verify_signature(uid_hash: str, signature_hex: str) -> Optional[str]:
    """
    Recover the signer address from the signature.
    Returns the checksummed address on success, None on failure.
    """
    entry = _pending.get(uid_hash)
    if not entry:
        logger.warning("verify_signature: no pending challenge for %s", uid_hash)
        return None

    challenge_text, issued_at = entry
    if time.time() - issued_at > _CHALLENGE_TTL:
        _pending.pop(uid_hash, None)
        logger.warning("verify_signature: challenge expired for %s", uid_hash)
        return None

    if not _is_hex_signature(signature_hex):
        return None

    try:
        from eth_account import Account
        from eth_account.messages import encode_defunct

        msg = encode_defunct(text=challenge_text)
        recovered = Account.recover_message(msg, signature=signature_hex.strip())
        # Clear challenge after use
        _pending.pop(uid_hash, None)
        return recovered  # checksummed address
    except Exception as exc:
        logger.warning("Signature recovery failed for %s: %s", uid_hash, exc)
        return None


async def get_verified_wallet(uid_hash: str) -> Optional[str]:
    """Read the verified wallet address from Greenfield tags."""
    from aisynergix.services.greenfield import read_user_tags
    try:
        tags = await read_user_tags(uid_hash)
        return tags.get("wallet_address") or None
    except Exception:
        return None


async def save_verified_wallet(uid_hash: str, address: str) -> None:
    """Persist the verified wallet address into the user's Greenfield tags."""
    from aisynergix.services.greenfield import read_user_tags, write_user_tags
    try:
        tags = await read_user_tags(uid_hash)
        tags["wallet_address"] = address.lower()
        await write_user_tags(uid_hash, tags)
        logger.info("Wallet verified for %s → %s", uid_hash, address)
    except Exception as exc:
        logger.warning("Could not save wallet for %s: %s", uid_hash, exc)


__all__ = [
    "build_challenge",
    "verify_signature",
    "get_verified_wallet",
    "save_verified_wallet",
    "_is_hex_address",
    "_is_hex_signature",
]
