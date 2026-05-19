"""
trading.py — BSC / PancakeSwap V2 price and link utilities for Synergix.

All operations are READ-ONLY on-chain (price via reserves) or generate
deep-link URLs for the user to sign in their own wallet. The bot never
holds or moves funds.
"""

import asyncio
import logging
import os
import time
from typing import Optional, Tuple
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)

# ── Token constants ────────────────────────────────────────────────────────
SYNERGIX_TOKEN   = "0x6485907278c389e70c572f441ce7052da58effff"
WBNB_ADDRESS     = "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"
ROUTER_ADDRESS   = "0x10ED43C718714eb63d5aA57B78B54704E256024E"  # PancakeSwap V2
BSC_RPC_URL      = os.getenv("BSC_RPC_URL", "https://bsc-dataseed1.binance.org")

# four.meme token page URL (used when token is still on bonding curve)
FOUR_MEME_TOKEN_URL = os.getenv("FOUR_MEME_TOKEN_URL", "https://four.meme/token")

# PancakeSwap V2 pair for SYNERGIX/WBNB (resolved lazily at runtime)
PAIR_ADDRESS: Optional[str] = None

MIN_BUY_BNB   = 0.0001
MIN_SELL_BNB_EQUIV = 0.0002
DEFAULT_SLIPPAGE = 1           # percent

# Price cache: (bnb_per_token, token_per_bnb, timestamp)
_price_cache: Optional[Tuple[float, float, float]] = None
_CACHE_TTL = 120   # seconds

# Minimal ABI for PancakeSwap V2 Pair (getReserves)
_PAIR_ABI = [
    {
        "inputs": [],
        "name": "getReserves",
        "outputs": [
            {"internalType": "uint112", "name": "_reserve0", "type": "uint112"},
            {"internalType": "uint112", "name": "_reserve1", "type": "uint112"},
            {"internalType": "uint32",  "name": "_blockTimestampLast", "type": "uint32"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "token0",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
]

# Minimal ABI for PancakeSwap V2 Factory (getPair)
_FACTORY_ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "tokenA", "type": "address"},
            {"internalType": "address", "name": "tokenB", "type": "address"},
        ],
        "name": "getPair",
        "outputs": [{"internalType": "address", "name": "pair", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    }
]
FACTORY_ADDRESS = "0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73"   # PancakeSwap V2 Factory


# Minimal ERC-20 ABI (balanceOf only)
_ERC20_ABI = [
    {
        "inputs": [{"internalType": "address", "name": "account", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    }
]


def _get_web3():
    """Return a Web3 instance connected to BSC. Imported lazily to avoid startup cost."""
    from web3 import Web3
    w3 = Web3(Web3.HTTPProvider(BSC_RPC_URL))
    return w3


async def _resolve_pair() -> Optional[str]:
    """Find the SYNERGIX/WBNB pair address via the factory contract."""
    global PAIR_ADDRESS
    if PAIR_ADDRESS:
        return PAIR_ADDRESS

    try:
        w3 = await asyncio.to_thread(_get_web3)
        factory = w3.eth.contract(
            address=w3.to_checksum_address(FACTORY_ADDRESS),
            abi=_FACTORY_ABI,
        )
        pair = await asyncio.to_thread(
            factory.functions.getPair(
                w3.to_checksum_address(SYNERGIX_TOKEN),
                w3.to_checksum_address(WBNB_ADDRESS),
            ).call
        )
        if pair and pair != "0x0000000000000000000000000000000000000000":
            PAIR_ADDRESS = pair
            logger.info("Resolved SYNERGIX/WBNB pair: %s", pair)
            return pair
    except Exception as exc:
        logger.warning("Could not resolve pair address: %s", exc)
    return None


async def get_token_price() -> Optional[Tuple[float, float]]:
    """
    Return (bnb_per_token, token_per_bnb).  Tries PancakeSwap V2 reserves
    first (works after graduation); falls back to DexScreener (works for
    both bonding-curve and graduated tokens).  Cached for CACHE_TTL seconds.
    """
    global _price_cache

    now = time.monotonic()
    if _price_cache and now - _price_cache[2] < _CACHE_TTL:
        return _price_cache[0], _price_cache[1]

    # ── 1. Try PancakeSwap V2 (on-chain, accurate) ─────────────────────
    pair_addr = await _resolve_pair()
    if pair_addr:
        try:
            w3 = await asyncio.to_thread(_get_web3)
            pair = w3.eth.contract(
                address=w3.to_checksum_address(pair_addr),
                abi=_PAIR_ABI,
            )
            reserves = await asyncio.to_thread(pair.functions.getReserves().call)
            token0   = await asyncio.to_thread(pair.functions.token0().call)

            r0, r1 = reserves[0], reserves[1]
            if token0.lower() == WBNB_ADDRESS.lower():
                reserve_bnb, reserve_syn = r0, r1
            else:
                reserve_syn, reserve_bnb = r0, r1

            if reserve_syn > 0 and reserve_bnb > 0:
                bnb_per_token = (reserve_bnb / 1e18) / (reserve_syn / 1e18)
                token_per_bnb = (reserve_syn / 1e18) / (reserve_bnb / 1e18)
                _price_cache = (bnb_per_token, token_per_bnb, now)
                return bnb_per_token, token_per_bnb
        except Exception as exc:
            logger.warning("PancakeSwap price fetch error: %s", exc)

    # ── 2. Fallback: DexScreener API (works for bonding-curve tokens too) ─
    try:
        from aisynergix.services.dexscreener import get_token_market
        data = await get_token_market(SYNERGIX_TOKEN)
        if data:
            price_native = float(data.get("price_native") or 0)
            if price_native > 0:
                token_per_bnb = 1.0 / price_native
                _price_cache = (price_native, token_per_bnb, now)
                logger.info("DexScreener price: %.10f BNB/token", price_native)
                return price_native, token_per_bnb
    except Exception as exc:
        logger.warning("DexScreener price fallback failed: %s", exc)

    return None


async def calculate_buy_amount(bnb_in: float) -> Optional[float]:
    """
    Return estimated SYNERGIX received for `bnb_in` BNB,
    applying 0.25% PancakeSwap fee (exact-in constant-product formula).
    """
    prices = await get_token_price()
    if not prices:
        return None
    _, token_per_bnb = prices

    # PancakeSwap V2: amount_out = (amount_in * 9975 * reserve_out) /
    #                              (reserve_in * 10000 + amount_in * 9975)
    # Simplified approximation using reserves ratio:
    pair_addr = await _resolve_pair()
    if not pair_addr:
        return token_per_bnb * bnb_in * 0.9975

    try:
        w3 = await asyncio.to_thread(_get_web3)
        pair = w3.eth.contract(address=w3.to_checksum_address(pair_addr), abi=_PAIR_ABI)
        reserves = await asyncio.to_thread(pair.functions.getReserves().call)
        token0   = await asyncio.to_thread(pair.functions.token0().call)

        r0, r1 = reserves[0], reserves[1]
        if token0.lower() == WBNB_ADDRESS.lower():
            reserve_in, reserve_out = r0, r1
        else:
            reserve_out, reserve_in = r0, r1

        amount_in_raw = int(bnb_in * 1e18)
        amount_in_fee = amount_in_raw * 9975
        numerator     = amount_in_fee * reserve_out
        denominator   = reserve_in * 10000 + amount_in_fee
        amount_out    = numerator // denominator
        return amount_out / 1e18
    except Exception:
        return token_per_bnb * bnb_in * 0.9975


async def calculate_sell_amount(syn_in: float) -> Optional[float]:
    """
    Return estimated BNB received for selling `syn_in` SYNERGIX tokens.
    """
    pair_addr = await _resolve_pair()
    prices = await get_token_price()
    if not prices or not pair_addr:
        return None

    bnb_per_token, _ = prices

    try:
        w3 = await asyncio.to_thread(_get_web3)
        pair = w3.eth.contract(address=w3.to_checksum_address(pair_addr), abi=_PAIR_ABI)
        reserves = await asyncio.to_thread(pair.functions.getReserves().call)
        token0   = await asyncio.to_thread(pair.functions.token0().call)

        r0, r1 = reserves[0], reserves[1]
        if token0.lower() == WBNB_ADDRESS.lower():
            reserve_out, reserve_in = r0, r1
        else:
            reserve_in, reserve_out = r0, r1

        amount_in_raw = int(syn_in * 1e18)
        amount_in_fee = amount_in_raw * 9975
        numerator     = amount_in_fee * reserve_out
        denominator   = reserve_in * 10000 + amount_in_fee
        amount_out    = numerator // denominator
        return amount_out / 1e18
    except Exception:
        return bnb_per_token * syn_in * 0.9975


async def get_bnb_usd_price() -> Optional[float]:
    """Fetch BNB/USD price from a public API (CoinGecko). Cached 2 min."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": "binancecoin", "vs_currencies": "usd"},
            )
            r.raise_for_status()
            return float(r.json()["binancecoin"]["usd"])
    except Exception:
        return None


def generate_buy_link(bnb_amount: float) -> str:
    """
    Build a PancakeSwap deep link to buy SYNERGIX with exactly `bnb_amount` BNB.
    Slippage is set to 1%.
    """
    # PancakeSwap V2 swap URL format
    # https://pancakeswap.finance/swap?inputCurrency=BNB&outputCurrency=<token>&exactAmount=<bnb>&exactField=input
    params = {
        "inputCurrency": "BNB",
        "outputCurrency": SYNERGIX_TOKEN,
        "exactAmount": str(bnb_amount),
        "exactField": "input",
        "slippage": str(DEFAULT_SLIPPAGE * 100),  # PancakeSwap uses basis points (100 = 1%)
    }
    return f"https://pancakeswap.finance/swap?{urlencode(params)}"


def generate_sell_link(syn_amount: float) -> str:
    """
    Build a PancakeSwap deep link to sell exactly `syn_amount` SYNERGIX for BNB.
    """
    params = {
        "inputCurrency": SYNERGIX_TOKEN,
        "outputCurrency": "BNB",
        "exactAmount": str(int(syn_amount)),
        "exactField": "input",
        "slippage": str(DEFAULT_SLIPPAGE * 100),
    }
    return f"https://pancakeswap.finance/swap?{urlencode(params)}"


async def get_token_balance(wallet_address: str) -> Optional[float]:
    """Read SYNERGIX (ERC-20) balance for a wallet on BSC. Returns whole tokens (1e18 decimals)."""
    try:
        w3 = await asyncio.to_thread(_get_web3)
        contract = w3.eth.contract(
            address=w3.to_checksum_address(SYNERGIX_TOKEN),
            abi=_ERC20_ABI,
        )
        raw = await asyncio.to_thread(
            contract.functions.balanceOf(w3.to_checksum_address(wallet_address)).call
        )
        return raw / 1e18
    except Exception as exc:
        logger.warning("Token balance fetch failed for %s: %s", wallet_address, exc)
        return None


async def get_bnb_balance(wallet_address: str) -> Optional[float]:
    """Read native BNB balance for a wallet on BSC."""
    try:
        w3 = await asyncio.to_thread(_get_web3)
        raw = await asyncio.to_thread(
            w3.eth.get_balance, w3.to_checksum_address(wallet_address)
        )
        return raw / 1e18
    except Exception as exc:
        logger.warning("BNB balance fetch failed for %s: %s", wallet_address, exc)
        return None


async def get_trade_link(amount: float, side: str = "buy") -> str:
    """
    Return the correct trading deep-link depending on whether SYNERGIX
    has graduated from the four.meme bonding curve to PancakeSwap V2.

    - Not graduated → four.meme token page link
    - Graduated      → PancakeSwap V2 swap link
    """
    try:
        from aisynergix.services.four_meme import get_curve_progress
        curve = await get_curve_progress(SYNERGIX_TOKEN)
        graduated = bool(curve and curve.get("graduated", False))
    except Exception:
        graduated = False

    if not graduated:
        return f"{FOUR_MEME_TOKEN_URL}/{SYNERGIX_TOKEN}"

    if side == "buy":
        return generate_buy_link(amount)
    return generate_sell_link(amount)


__all__ = [
    "SYNERGIX_TOKEN",
    "MIN_BUY_BNB",
    "MIN_SELL_BNB_EQUIV",
    "DEFAULT_SLIPPAGE",
    "get_token_price",
    "get_bnb_usd_price",
    "calculate_buy_amount",
    "calculate_sell_amount",
    "generate_buy_link",
    "generate_sell_link",
    "get_trade_link",
    "get_token_balance",
    "get_bnb_balance",
]
