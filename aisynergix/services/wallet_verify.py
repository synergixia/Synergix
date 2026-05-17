"""
wallet_verify.py — EIP-4361 (Sign-In with Ethereum) verification flow.

Flow:
1. User pastes their wallet address (0x…40 hex chars).
2. Bot generates a SIWE-formatted challenge bound to that address.
3. User signs the challenge in their wallet (no gas, no transaction).
4. User pastes the signature; bot recovers the signer and confirms it
   matches the declared address. The address is then stored in Irys.

The bot never requests the user's private key.
"""

import logging
import os
import re
import secrets
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# SIWE message template — strict EIP-4361 format
_DOMAIN = os.getenv("SYNERGIX_SIWE_DOMAIN", "synergix.bot")
_URI = os.getenv("SYNERGIX_SIWE_URI", "https://synergix.bot")
_CHAIN_ID = int(os.getenv("SYNERGIX_SIWE_CHAIN_ID", "56"))
_STATEMENT = (
    "I confirm ownership of this wallet for Synergix Identity Verification. "
    "This signature is gasless and cannot move funds or execute transactions."
)

# Challenge expires after 10 minutes
_CHALLENGE_TTL = 600

# In-memory store: uid_hash -> (address_lower, challenge_text, issued_at)
_pending: dict = {}


def _is_hex_address(s: str) -> bool:
    return bool(re.fullmatch(r"0x[0-9a-fA-F]{40}", s.strip()))


def _is_hex_signature(s: str) -> bool:
    cleaned = s.strip()
    if cleaned.startswith("0x"):
        cleaned = cleaned[2:]
    return bool(re.fullmatch(r"[0-9a-fA-F]{130}", cleaned))


def _build_siwe_message(address: str, nonce: str, issued_at: str, expiration_at: str) -> str:
    """Build a strict EIP-4361 message bound to `address`."""
    return (
        f"{_DOMAIN} wants you to sign in with your Ethereum account:\n"
        f"{address}\n\n"
        f"{_STATEMENT}\n\n"
        f"URI: {_URI}\n"
        f"Version: 1\n"
        f"Chain ID: {_CHAIN_ID}\n"
        f"Nonce: {nonce}\n"
        f"Issued At: {issued_at}\n"
        f"Expiration Time: {expiration_at}"
    )


def build_challenge(uid_hash: str, address: str) -> Optional[str]:
    """
    Generate a unique SIWE challenge for the user's declared address.
    Returns the challenge text, or None if `address` is malformed.
    """
    if not _is_hex_address(address):
        return None

    addr = address.strip()
    if not addr.startswith("0x"):
        addr = "0x" + addr

    nonce = secrets.token_hex(8)
    now = datetime.now(timezone.utc)
    issued_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    expiration_at = (now + timedelta(seconds=_CHALLENGE_TTL)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    text = _build_siwe_message(addr, nonce, issued_at, expiration_at)
    _pending[uid_hash] = (addr.lower(), text, time.time())
    return text


def verify_signature(uid_hash: str, signature_hex: str) -> Optional[str]:
    """
    Recover the signer from the SIWE signature and confirm it matches the
    declared address. Returns the checksummed address on success, None
    otherwise.
    """
    entry = _pending.get(uid_hash)
    if not entry:
        logger.warning("verify_signature: no pending challenge for %s", uid_hash)
        return None

    declared, challenge_text, issued_at = entry
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
    except Exception as exc:
        logger.warning("Signature recovery failed for %s: %s", uid_hash, exc)
        return None

    if recovered.lower() != declared:
        logger.warning(
            "verify_signature: address mismatch for %s (declared=%s recovered=%s)",
            uid_hash,
            declared,
            recovered,
        )
        return None

    _pending.pop(uid_hash, None)
    return recovered  # checksummed by eth_account


def clear_pending(uid_hash: str) -> None:
    _pending.pop(uid_hash, None)


async def get_verified_wallet(uid_hash: str) -> Optional[str]:
    """Read the verified wallet address from Irys tags."""
    from aisynergix.services.irys import read_user_tags
    try:
        tags = await read_user_tags(uid_hash)
        return tags.get("wallet_address") or None
    except Exception:
        return None


async def save_verified_wallet(uid_hash: str, address: str) -> None:
    """Persist the verified wallet address and human_verified flag into Irys."""
    from aisynergix.services.irys import read_user_tags, write_user_tags
    try:
        tags = await read_user_tags(uid_hash)
        tags["wallet_address"] = address.lower()
        tags["human_verified"] = "true"
        await write_user_tags(uid_hash, tags)
        logger.info("Wallet verified for %s -> %s", uid_hash, address)
    except Exception as exc:
        logger.warning("Could not save wallet for %s: %s", uid_hash, exc)


__all__ = [
    "build_challenge",
    "verify_signature",
    "clear_pending",
    "get_verified_wallet",
    "save_verified_wallet",
    "_is_hex_address",
    "_is_hex_signature",
]
