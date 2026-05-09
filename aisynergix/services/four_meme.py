"""
four_meme.py — Four.meme bonding curve progress reader for SYNERGIX.

Four.meme is a token launchpad on BSC where tokens trade against a
bonding curve until they raise enough BNB to "graduate" and migrate
liquidity to PancakeSwap V2.

This module queries the Four.meme TokenManager proxy to read the current
state of the curve (`offers` raised vs `maxRaising` target) and computes
a percentage. If the token has already graduated (no longer present in
the manager mapping), `get_curve_progress` returns a graduated marker so
the UI can switch to "100% — Graduated".
"""

import asyncio
import logging
import os
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# Public Four.meme TokenManager proxies on BSC. We try both because
# Four.meme has migrated managers over time and tokens can live on
# either contract.
FOUR_MEME_MANAGERS = [
    os.getenv(
        "FOUR_MEME_MANAGER",
        "0x5c952063c7fc8610FFDB798152D69F0B9550762b",
    ),
    "0xEc4549caDcE5DA21Df6E6422d448034B5233bFbC",
]

# The TokenManager exposes `_tokenInfos(address)` returning a tuple with
# the bonding-curve state. The exact field order varies slightly between
# manager versions; we read the public getter and pull the two fields
# that matter: total BNB raised and target raising threshold.
_TOKEN_MANAGER_ABI = [
    {
        "inputs": [{"internalType": "address", "name": "", "type": "address"}],
        "name": "_tokenInfos",
        "outputs": [
            {"internalType": "address", "name": "base", "type": "address"},
            {"internalType": "address", "name": "quote", "type": "address"},
            {"internalType": "uint256", "name": "template", "type": "uint256"},
            {"internalType": "uint256", "name": "totalSupply", "type": "uint256"},
            {"internalType": "uint256", "name": "maxOffers", "type": "uint256"},
            {"internalType": "uint256", "name": "maxRaising", "type": "uint256"},
            {"internalType": "uint256", "name": "launchTime", "type": "uint256"},
            {"internalType": "uint256", "name": "offers", "type": "uint256"},
            {"internalType": "uint256", "name": "funds", "type": "uint256"},
            {"internalType": "uint256", "name": "lastPrice", "type": "uint256"},
            {"internalType": "uint256", "name": "K", "type": "uint256"},
            {"internalType": "uint256", "name": "T", "type": "uint256"},
            {"internalType": "uint256", "name": "status", "type": "uint256"},
        ],
        "stateMutability": "view",
        "type": "function",
    }
]


def _get_web3():
    from web3 import Web3
    rpc = os.getenv("BSC_RPC_URL", "https://bsc-dataseed1.binance.org")
    return Web3(Web3.HTTPProvider(rpc))


def _try_read_manager(w3, manager_addr: str, token_addr: str) -> Optional[Dict[str, Any]]:
    """Read `_tokenInfos(token)` from a single manager proxy."""
    try:
        contract = w3.eth.contract(
            address=w3.to_checksum_address(manager_addr),
            abi=_TOKEN_MANAGER_ABI,
        )
        info = contract.functions._tokenInfos(
            w3.to_checksum_address(token_addr)
        ).call()
    except Exception as exc:
        logger.debug("Manager %s read failed: %s", manager_addr, exc)
        return None

    base = info[0] if len(info) > 0 else "0x0"
    if not base or int(base, 16) == 0:
        # Token not registered in this manager
        return None

    max_raising = int(info[5]) if len(info) > 5 else 0
    funds = int(info[8]) if len(info) > 8 else 0
    status = int(info[12]) if len(info) > 12 else 0

    return {
        "manager": manager_addr,
        "max_raising_bnb": max_raising / 1e18 if max_raising else 0.0,
        "funds_bnb": funds / 1e18 if funds else 0.0,
        "status": status,
    }


async def get_curve_progress(token_address: str) -> Optional[Dict[str, Any]]:
    """
    Return bonding-curve progress for a Four.meme token.
    Result keys:
      - graduated: bool
      - percent: float in [0, 100]
      - raised_bnb: float
      - target_bnb: float
    Returns None on RPC failure.
    """
    try:
        w3 = await asyncio.to_thread(_get_web3)
        for manager in FOUR_MEME_MANAGERS:
            data = await asyncio.to_thread(
                _try_read_manager, w3, manager, token_address
            )
            if data is None:
                continue

            target = data["max_raising_bnb"] or 0
            raised = data["funds_bnb"] or 0
            percent = (raised / target * 100) if target > 0 else 0.0
            graduated = data["status"] >= 2 or (target > 0 and raised >= target)

            return {
                "graduated": bool(graduated),
                "percent": round(min(percent, 100.0), 2),
                "raised_bnb": round(raised, 6),
                "target_bnb": round(target, 6),
                "manager": data["manager"],
            }

        # Not found in any manager — likely already graduated or never on Four.meme
        return {
            "graduated": True,
            "percent": 100.0,
            "raised_bnb": 0.0,
            "target_bnb": 0.0,
            "manager": "",
        }
    except Exception as exc:
        logger.warning("Four.meme curve query failed: %s", exc)
        return None


__all__ = ["get_curve_progress"]
