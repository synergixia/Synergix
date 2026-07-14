"""custody.py — depósito y retiro on-chain de las wallets custodiales (BSC).

Maneja fondos REALES en BNB Chain:

  * DEPÓSITO: no requiere firma.  El usuario transfiere BNB o el token
    SYNERGIX a su dirección custodial; aquí solo se resuelve la dirección y,
    opcionalmente, el saldo.  El bot no hace nada más — llegan al recibir.

  * RETIRO: firma y difunde una transacción real en BSC con la clave privada
    custodial (descifrada vía wallet.load_custodial_account).  Mueve fondos
    reales e IRREVERSIBLES, por eso el flujo del bot exige confirmación
    explícita y validación estricta antes de llamar aquí.

Seguridad:
  - La clave privada solo existe en memoria durante la firma; nunca se loguea.
  - Un lock por-usuario serializa los retiros → sin colisiones de nonce ni
    doble gasto desde este proceso.
  - Los montos pasan por rewards.parse_amount (rechaza nan/inf/no-positivos).
  - En BNB se reserva el gas: nunca se intenta retirar el 100 % del saldo.
  - Requiere SYNERGIX_WALLET_MASTER_KEY (para descifrar la wallet).

Distinción importante: el "SYNERGIX" de aquí es el TOKEN ERC-20 real en BSC
(trading.SYNERGIX_TOKEN), NO el saldo contable "SYNX" de Irys (puntos de la
economía interna).  Son cosas distintas.
"""

import asyncio
import logging
import os
import re
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# El token ERC-20 y el cliente web3 viven en trading; se importan de forma
# PEREZOSA dentro de las funciones (trading arrastra httpx/web3), así los
# helpers puros de validación se pueden usar/testear sin esas dependencias.

TOKEN_DECIMALS = 18
WEI = 10 ** TOKEN_DECIMALS
CHAIN_ID = 56  # BSC mainnet

# Mínimos de retiro (evitan retiros que el gas se comería).
MIN_WITHDRAW_BNB = 0.0005
MIN_WITHDRAW_TOKEN = 1.0

# Límites de gas: transferencia nativa 21000; ERC-20 ~ 60k (fijamos 100k con
# margen y se ajusta con estimate_gas cuando es posible).
GAS_NATIVE = 21000
GAS_TOKEN_FALLBACK = 100_000
GAS_SWAP_FALLBACK = 400_000       # swaps con fee-on-transfer consumen más
GAS_APPROVE_FALLBACK = 60_000

# Slippage de los swaps (%).  SYNERGIX tiene ~1 % de tax + impacto de precio
# en un pool de baja liquidez, así que el mínimo por defecto es holgado para
# que la tx no revierta.  Configurable con SYNERGIX_SWAP_SLIPPAGE.
try:
    SWAP_SLIPPAGE_PCT = max(0.5, min(50.0, float(os.getenv("SYNERGIX_SWAP_SLIPPAGE", "12"))))
except (ValueError, TypeError):
    SWAP_SLIPPAGE_PCT = 12.0
SWAP_DEADLINE_S = 300             # 5 min de validez de la orden

# ABI del router PancakeSwap V2 (solo las variantes fee-on-transfer que
# necesita un token con tax) + approve/allowance del ERC-20.
_ROUTER_ABI = [
    {
        "name": "swapExactETHForTokensSupportingFeeOnTransferTokens",
        "type": "function", "stateMutability": "payable",
        "inputs": [
            {"name": "amountOutMin", "type": "uint256"},
            {"name": "path", "type": "address[]"},
            {"name": "to", "type": "address"},
            {"name": "deadline", "type": "uint256"},
        ],
        "outputs": [],
    },
    {
        "name": "swapExactTokensForETHSupportingFeeOnTransferTokens",
        "type": "function", "stateMutability": "nonpayable",
        "inputs": [
            {"name": "amountIn", "type": "uint256"},
            {"name": "amountOutMin", "type": "uint256"},
            {"name": "path", "type": "address[]"},
            {"name": "to", "type": "address"},
            {"name": "deadline", "type": "uint256"},
        ],
        "outputs": [],
    },
]
_ERC20_ALLOWANCE_ABI = [
    {
        "name": "approve", "type": "function", "stateMutability": "nonpayable",
        "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
        "outputs": [{"name": "", "type": "bool"}],
    },
    {
        "name": "allowance", "type": "function", "stateMutability": "view",
        "inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
    },
]

# ABI mínima para transfer(address,uint256).
_ERC20_TRANSFER_ABI = [{
    "inputs": [
        {"internalType": "address", "name": "to", "type": "address"},
        {"internalType": "uint256", "name": "amount", "type": "uint256"},
    ],
    "name": "transfer",
    "outputs": [{"internalType": "bool", "name": "", "type": "bool"}],
    "stateMutability": "nonpayable",
    "type": "function",
}]

# Locks por-usuario (uid_hash) para serializar retiros / nonce.
_locks: Dict[str, asyncio.Lock] = {}


def _lock_for(uid_hash: str) -> asyncio.Lock:
    lock = _locks.get(uid_hash)
    if lock is None:
        lock = asyncio.Lock()
        _locks[uid_hash] = lock
    return lock


# ── Helpers puros (testeables sin red) ───────────────────────────────────

_ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_ZERO_ADDR = "0x0000000000000000000000000000000000000000"


def is_valid_address(addr: str) -> bool:
    """Formato de dirección EVM válido y distinto de la dirección cero."""
    a = (addr or "").strip()
    return bool(_ADDR_RE.fullmatch(a)) and a.lower() != _ZERO_ADDR


def max_withdrawable_native(balance_bnb: float, gas_cost_bnb: float) -> float:
    """Máximo BNB retirable dejando el gas de la propia transacción."""
    return max(0.0, round(balance_bnb - gas_cost_bnb, 8))


def min_out(expected: float, slippage_pct: float) -> float:
    """Salida mínima aceptable de un swap aplicando slippage. Nunca negativa."""
    if expected is None or expected <= 0:
        return 0.0
    return max(0.0, expected * (1.0 - slippage_pct / 100.0))


def validate_withdrawal(
    to_address: str,
    amount: float,
    *,
    is_native: bool,
    token_balance: float,
    bnb_balance: float,
    gas_cost_bnb: float,
) -> Optional[str]:
    """Valida un retiro y retorna la clave de error, o None si es válido.

    Errores: bad_address | bad_amount | too_small | insufficient | no_gas.
    """
    from aisynergix.services.rewards import is_valid_amount
    if not is_valid_address(to_address):
        return "bad_address"
    if not is_valid_amount(amount):
        return "bad_amount"

    if is_native:
        if amount < MIN_WITHDRAW_BNB:
            return "too_small"
        if amount > max_withdrawable_native(bnb_balance, gas_cost_bnb):
            return "insufficient"
    else:
        if amount < MIN_WITHDRAW_TOKEN:
            return "too_small"
        if amount > token_balance:
            return "insufficient"
        # El token se paga con gas en BNB: la wallet debe tenerlo.
        if bnb_balance < gas_cost_bnb:
            return "no_gas"
    return None


def _raw_tx(signed) -> bytes:
    """Compat web3 v6 (raw_transaction) / v5 (rawTransaction)."""
    return getattr(signed, "raw_transaction", None) or signed.rawTransaction


# ── Depósito ─────────────────────────────────────────────────────────────

async def deposit_info(uid: int) -> Optional[Dict[str, Any]]:
    """Dirección custodial + saldos actuales para mostrar al usuario.

    El depósito es recibir: el usuario envía BNB o SYNERGIX a esta dirección.
    """
    from aisynergix.services.wallet import ensure_custodial_wallet, load_custodial_account
    from aisynergix.services.trading import get_bnb_balance, get_token_balance
    from aisynergix.bot.identity import _hash_uid
    address = await ensure_custodial_wallet(uid)
    if not address:
        return None
    # Salud: la wallet solo sirve si su keystore se puede DESCIFRAR con la
    # master key actual.  Si no (p. ej. se cambió la master key), la dirección
    # es inutilizable y el usuario no debe depositar en ella.
    acct = await load_custodial_account(_hash_uid(uid))
    healthy = bool(acct and acct.address == address)
    bnb, syn = await asyncio.gather(
        get_bnb_balance(address), get_token_balance(address),
    )
    return {"address": address, "bnb": bnb, "synergix": syn, "healthy": healthy}


# ── Retiro ───────────────────────────────────────────────────────────────

async def _gas_cost_bnb(w3, gas_limit: int) -> float:
    """Coste de gas estimado (BNB) para un gas_limit dado."""
    try:
        gas_price = await asyncio.to_thread(lambda: w3.eth.gas_price)
    except Exception:
        gas_price = w3.to_wei(3, "gwei")
    return (gas_limit * int(gas_price)) / WEI


async def withdraw(
    uid: int, to_address: str, amount: float, asset: str
) -> Dict[str, Any]:
    """Retira ``amount`` de ``asset`` ('bnb' | 'synergix') a ``to_address``.

    Retorna {"ok": True, "tx": <hash>} o {"ok": False, "error": <clave>}.
    Firma y difunde una transacción real en BSC — irreversible.
    """
    from aisynergix.services.wallet import (
        load_custodial_account, ensure_custodial_wallet, wallet_enabled,
    )
    from aisynergix.bot.identity import _hash_uid
    from aisynergix.services.trading import get_bnb_balance, get_token_balance, _get_web3

    if not wallet_enabled():
        return {"ok": False, "error": "disabled"}
    is_native = asset == "bnb"
    uid_hash = _hash_uid(uid)

    # Auto-cura: si la wallet nunca se creó (p. ej. el /start fue anterior a
    # configurar la master key), créala ahora antes de intentar cargarla.
    await ensure_custodial_wallet(uid)

    async with _lock_for(uid_hash):
        acct = await load_custodial_account(uid_hash)
        if acct is None:
            # Distinguir "nunca creada" de "existe pero no descifra" (indicio
            # de que se cambió SYNERGIX_WALLET_MASTER_KEY — fondos en riesgo).
            from aisynergix.services.irys import read_custodial_wallet
            try:
                rec = await read_custodial_wallet(uid_hash)
            except Exception:
                rec = None
            if rec and rec.get("keystore"):
                logger.error(
                    "withdraw: keystore de %s existe en Irys pero NO descifra — "
                    "¿cambió SYNERGIX_WALLET_MASTER_KEY?", uid_hash,
                )
                return {"ok": False, "error": "undecryptable"}
            return {"ok": False, "error": "no_wallet"}
        self_address = acct.address

        try:
            w3 = await asyncio.to_thread(_get_web3)
        except Exception as exc:
            logger.warning("withdraw: web3 no disponible: %s", exc)
            return {"ok": False, "error": "network"}

        gas_limit = GAS_NATIVE if is_native else GAS_TOKEN_FALLBACK
        gas_cost = await _gas_cost_bnb(w3, gas_limit)
        # Saldos de la PROPIA wallet custodial (origen del retiro).
        bnb_bal, token_bal = await asyncio.gather(
            get_bnb_balance(self_address),
            get_token_balance(self_address),
        )
        # SYNERGIX bloqueado como bond de nodo NO es retirable.
        if not is_native:
            from aisynergix.services import bonds as bonds_svc
            token_bal = max(0.0, (token_bal or 0.0) - await bonds_svc.locked_synergix(uid))

        err = validate_withdrawal(
            to_address, amount, is_native=is_native,
            token_balance=token_bal or 0.0, bnb_balance=bnb_bal or 0.0,
            gas_cost_bnb=gas_cost,
        )
        if err:
            return {"ok": False, "error": err}

        try:
            tx_hash = await asyncio.to_thread(
                _sign_and_send, w3, acct, to_address, amount, is_native, gas_limit
            )
        except Exception as exc:
            logger.error("withdraw firma/broadcast falló uid_hash=%s: %s", uid_hash, exc)
            return {"ok": False, "error": "broadcast"}
        logger.info("💸 Retiro %s de %s %s → %s tx=%s",
                    amount, asset, uid_hash, to_address, tx_hash)
        return {"ok": True, "tx": tx_hash}


# ── Swap: comprar / vender SYNERGIX desde la wallet custodial ────────────

async def swap_buy(uid: int, bnb_amount: float) -> Dict[str, Any]:
    """Compra SYNERGIX gastando ``bnb_amount`` BNB de la wallet custodial.

    Retorna {"ok": True, "tx": hash} o {"ok": False, "error": clave}.
    """
    return await _swap(uid, bnb_amount, side="buy")


async def swap_sell(uid: int, syn_amount: float) -> Dict[str, Any]:
    """Vende ``syn_amount`` SYNERGIX de la wallet custodial por BNB."""
    return await _swap(uid, syn_amount, side="sell")


async def _swap(uid: int, amount: float, side: str) -> Dict[str, Any]:
    from aisynergix.services.wallet import (
        load_custodial_account, ensure_custodial_wallet, wallet_enabled,
    )
    from aisynergix.bot.identity import _hash_uid
    from aisynergix.services.trading import (
        get_bnb_balance, get_token_balance, calculate_buy_amount,
        calculate_sell_amount, _get_web3,
    )
    from aisynergix.services.rewards import is_valid_amount

    if not wallet_enabled():
        return {"ok": False, "error": "disabled"}
    if not is_valid_amount(amount):
        return {"ok": False, "error": "bad_amount"}

    uid_hash = _hash_uid(uid)
    await ensure_custodial_wallet(uid)

    async with _lock_for(uid_hash):
        acct = await load_custodial_account(uid_hash)
        if acct is None:
            return {"ok": False, "error": "no_wallet"}

        try:
            w3 = await asyncio.to_thread(_get_web3)
        except Exception as exc:
            logger.warning("swap: web3 no disponible: %s", exc)
            return {"ok": False, "error": "network"}

        gas_cost = await _gas_cost_bnb(w3, GAS_SWAP_FALLBACK)
        bnb_bal = await get_bnb_balance(acct.address) or 0.0

        # Salida esperada (aplica la comisión del pool); si es None el token
        # aún no cotiza en PancakeSwap (bonding curve) → el caller usa el link.
        if side == "buy":
            if bnb_bal < amount + gas_cost:
                return {"ok": False, "error": "insufficient"}
            expected = await calculate_buy_amount(amount)
            if expected is None:
                return {"ok": False, "error": "not_graduated"}
        else:
            token_bal = await get_token_balance(acct.address) or 0.0
            # El SYNERGIX bloqueado como bond de nodo no se puede vender.
            from aisynergix.services import bonds as bonds_svc
            token_bal = max(0.0, token_bal - await bonds_svc.locked_synergix(uid))
            if amount > token_bal:
                return {"ok": False, "error": "insufficient"}
            if bnb_bal < gas_cost:
                return {"ok": False, "error": "no_gas"}
            expected = await calculate_sell_amount(amount)
            if expected is None:
                return {"ok": False, "error": "not_graduated"}

        out_min = min_out(expected, SWAP_SLIPPAGE_PCT)
        try:
            tx_hash = await asyncio.to_thread(
                _swap_tx, w3, acct, side, amount, out_min,
            )
        except Exception as exc:
            logger.error("swap %s falló uid_hash=%s: %s", side, uid_hash, exc)
            return {"ok": False, "error": "broadcast"}
        logger.info("🔁 Swap %s %s uid_hash=%s tx=%s", side, amount, uid_hash, tx_hash)
        return {"ok": True, "tx": tx_hash, "expected": expected}


def _swap_tx(w3, acct, side: str, amount: float, out_min: float) -> str:
    """Construye, firma y difunde el swap (bloqueante → to_thread).

    En venta, primero asegura la aprobación del router para gastar el token.
    """
    from aisynergix.services.trading import ROUTER_ADDRESS, WBNB_ADDRESS, SYNERGIX_TOKEN

    router_addr = w3.to_checksum_address(ROUTER_ADDRESS)
    token_addr = w3.to_checksum_address(SYNERGIX_TOKEN)
    wbnb = w3.to_checksum_address(WBNB_ADDRESS)
    router = w3.eth.contract(address=router_addr, abi=_ROUTER_ABI)
    gas_price = w3.eth.gas_price
    deadline = int(time.time()) + SWAP_DEADLINE_S

    if side == "buy":
        amount_in_wei = int(round(amount * WEI))
        out_min_wei = int(out_min * WEI)
        path = [wbnb, token_addr]
        fn = router.functions.swapExactETHForTokensSupportingFeeOnTransferTokens(
            out_min_wei, path, acct.address, deadline
        )
        nonce = w3.eth.get_transaction_count(acct.address, "pending")
        base = {"from": acct.address, "value": amount_in_wei, "gasPrice": gas_price,
                "nonce": nonce, "chainId": CHAIN_ID}
        try:
            base["gas"] = int(fn.estimate_gas(base) * 1.25)
        except Exception:
            base["gas"] = GAS_SWAP_FALLBACK
        signed = acct.sign_transaction(fn.build_transaction(base))
        return w3.eth.send_raw_transaction(_raw_tx(signed)).hex()

    # ── Venta: approve (si falta) + swap ─────────────────────────────────
    amount_in_wei = int(round(amount * WEI))
    out_min_wei = int(out_min * WEI)
    token = w3.eth.contract(address=token_addr, abi=_ERC20_ALLOWANCE_ABI)
    nonce = w3.eth.get_transaction_count(acct.address, "pending")

    allowance = token.functions.allowance(acct.address, router_addr).call()
    if allowance < amount_in_wei:
        approve_fn = token.functions.approve(router_addr, 2 ** 256 - 1)
        approve_tx = {"from": acct.address, "gasPrice": gas_price, "nonce": nonce,
                      "chainId": CHAIN_ID}
        try:
            approve_tx["gas"] = int(approve_fn.estimate_gas(approve_tx) * 1.25)
        except Exception:
            approve_tx["gas"] = GAS_APPROVE_FALLBACK
        signed_approve = acct.sign_transaction(approve_fn.build_transaction(approve_tx))
        approve_hash = w3.eth.send_raw_transaction(_raw_tx(signed_approve))
        w3.eth.wait_for_transaction_receipt(approve_hash, timeout=120)
        nonce += 1

    path = [token_addr, wbnb]
    fn = router.functions.swapExactTokensForETHSupportingFeeOnTransferTokens(
        amount_in_wei, out_min_wei, path, acct.address, deadline
    )
    base = {"from": acct.address, "gasPrice": gas_price, "nonce": nonce, "chainId": CHAIN_ID}
    try:
        base["gas"] = int(fn.estimate_gas(base) * 1.25)
    except Exception:
        base["gas"] = GAS_SWAP_FALLBACK
    signed = acct.sign_transaction(fn.build_transaction(base))
    return w3.eth.send_raw_transaction(_raw_tx(signed)).hex()


def _sign_and_send(w3, acct, to_address, amount: float, is_native: bool, gas_limit: int) -> str:
    """Construye, firma y difunde la transacción (bloqueante → to_thread)."""
    from aisynergix.services.trading import SYNERGIX_TOKEN
    to = w3.to_checksum_address(to_address)
    nonce = w3.eth.get_transaction_count(acct.address, "pending")
    gas_price = w3.eth.gas_price

    if is_native:
        tx = {
            "to": to,
            "value": int(round(amount * WEI)),
            "gas": gas_limit,
            "gasPrice": gas_price,
            "nonce": nonce,
            "chainId": CHAIN_ID,
        }
    else:
        contract = w3.eth.contract(
            address=w3.to_checksum_address(SYNERGIX_TOKEN), abi=_ERC20_TRANSFER_ABI,
        )
        raw_amount = int(round(amount * WEI))
        try:
            gas_limit = contract.functions.transfer(to, raw_amount).estimate_gas(
                {"from": acct.address}
            )
            gas_limit = int(gas_limit * 1.2)
        except Exception:
            gas_limit = GAS_TOKEN_FALLBACK
        tx = contract.functions.transfer(to, raw_amount).build_transaction({
            "from": acct.address,
            "gas": gas_limit,
            "gasPrice": gas_price,
            "nonce": nonce,
            "chainId": CHAIN_ID,
        })

    signed = acct.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(_raw_tx(signed))
    return tx_hash.hex()


__all__ = [
    "MIN_WITHDRAW_BNB",
    "MIN_WITHDRAW_TOKEN",
    "SWAP_SLIPPAGE_PCT",
    "is_valid_address",
    "max_withdrawable_native",
    "min_out",
    "validate_withdrawal",
    "deposit_info",
    "withdraw",
    "swap_buy",
    "swap_sell",
]
