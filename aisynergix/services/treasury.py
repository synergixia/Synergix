"""treasury.py — tesoro hot-wallet que paga los canjes en SYNERGIX real (Fase C).

Wallet SEPARADA de la PRIVATE_KEY del nodo (aísla el riesgo de Irys del riesgo
del tesoro).  Debe contener SOLO el float de recompensas de ~1 día; el grueso
de la asignación se guarda en una wallet cuya clave NO está en el servidor.
Así el peor caso de compromiso ≤ el float caliente.

Firma transferencias ERC-20 del token SYNERGIX desde el tesoro hacia la wallet
verificada del usuario.  La clave solo vive en memoria; jamás se loguea.
"""

import asyncio
import logging
import os
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# Clave privada del tesoro (hot wallet). Distinta de PRIVATE_KEY.
TREASURY_KEY: str = os.getenv("SYNERGIX_TREASURY_KEY", "").strip()

_lock = asyncio.Lock()  # serializa pagos → sin colisión de nonce


def treasury_enabled() -> bool:
    return bool(TREASURY_KEY)


def _account():
    from eth_account import Account
    return Account.from_key(TREASURY_KEY)


def treasury_address() -> Optional[str]:
    if not treasury_enabled():
        return None
    try:
        return _account().address
    except Exception as exc:
        logger.error("treasury_address inválida: %s", exc)
        return None


async def treasury_balances() -> Tuple[Optional[float], Optional[float]]:
    """(SYNERGIX, BNB) del tesoro. Para diagnóstico/monitoreo."""
    address = treasury_address()
    if not address:
        return None, None
    from aisynergix.services.trading import get_token_balance, get_bnb_balance
    return await asyncio.gather(get_token_balance(address), get_bnb_balance(address))


async def pay_synergix(to_address: str, amount: float) -> Dict[str, Any]:
    """Transfiere ``amount`` SYNERGIX del tesoro a ``to_address``.

    Retorna {"ok": True, "tx": hash} o {"ok": False, "error": clave}.
    Errores: disabled | network | insufficient | no_gas | broadcast.
    """
    from aisynergix.services.rewards import is_valid_amount
    from aisynergix.services.custody import (
        WEI, CHAIN_ID, GAS_TOKEN_FALLBACK, _ERC20_TRANSFER_ABI, _raw_tx,
    )
    from aisynergix.services.trading import (
        SYNERGIX_TOKEN, _get_web3, get_token_balance, get_bnb_balance,
    )
    from aisynergix.services.custody import is_valid_address

    if not treasury_enabled():
        return {"ok": False, "error": "disabled"}
    if not is_valid_amount(amount) or not is_valid_address(to_address):
        return {"ok": False, "error": "bad_input"}

    async with _lock:
        try:
            acct = _account()
            w3 = await asyncio.to_thread(_get_web3)
        except Exception as exc:
            logger.warning("treasury pay: web3/clave no disponible: %s", exc)
            return {"ok": False, "error": "network"}

        # El tesoro debe tener SYNERGIX suficiente y BNB para el gas.
        syn_bal, bnb_bal = await asyncio.gather(
            get_token_balance(acct.address), get_bnb_balance(acct.address),
        )
        if (syn_bal or 0.0) < amount:
            logger.error("🚨 Tesoro SIN SYNERGIX suficiente: %.2f < %.2f", syn_bal or 0, amount)
            return {"ok": False, "error": "insufficient"}
        gas_cost = 0.0
        try:
            gas_price = await asyncio.to_thread(lambda: w3.eth.gas_price)
            gas_cost = (GAS_TOKEN_FALLBACK * int(gas_price)) / WEI
        except Exception:
            pass
        if (bnb_bal or 0.0) < gas_cost:
            logger.error("🚨 Tesoro SIN BNB para gas: %.6f < %.6f", bnb_bal or 0, gas_cost)
            return {"ok": False, "error": "no_gas"}

        try:
            tx_hash = await asyncio.to_thread(
                _send_token, w3, acct, to_address, amount,
                SYNERGIX_TOKEN, _ERC20_TRANSFER_ABI, CHAIN_ID, GAS_TOKEN_FALLBACK, _raw_tx,
            )
        except Exception as exc:
            logger.error("treasury pay broadcast falló → %s: %s", to_address, exc)
            return {"ok": False, "error": "broadcast"}
        logger.info("🏦 Tesoro pagó %.2f SYNERGIX → %s tx=%s", amount, to_address, tx_hash)
        return {"ok": True, "tx": tx_hash}


def _send_token(w3, acct, to_address, amount, token_addr, abi, chain_id, gas_fallback, raw_tx):
    to = w3.to_checksum_address(to_address)
    contract = w3.eth.contract(address=w3.to_checksum_address(token_addr), abi=abi)
    raw_amount = int(round(amount * (10 ** 18)))
    nonce = w3.eth.get_transaction_count(acct.address, "pending")
    gas_price = w3.eth.gas_price
    base = {"from": acct.address, "gasPrice": gas_price, "nonce": nonce, "chainId": chain_id}
    fn = contract.functions.transfer(to, raw_amount)
    try:
        base["gas"] = int(fn.estimate_gas(base) * 1.25)
    except Exception:
        base["gas"] = gas_fallback
    signed = acct.sign_transaction(fn.build_transaction(base))
    return w3.eth.send_raw_transaction(raw_tx(signed)).hex()


__all__ = [
    "treasury_enabled",
    "treasury_address",
    "treasury_balances",
    "pay_synergix",
]
