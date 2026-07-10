"""bond_chain.py — lectura del contrato de bonds on-chain (Fase D).

INERTE por defecto: solo se activa si ``SYNERGIX_BOND_CONTRACT`` apunta a un
contrato ``SynergixNodeBond`` YA DESPLEGADO Y AUDITADO (ver contracts/README.md).

Esta capa es de SOLO LECTURA: consulta el estado de un bond en la cadena.  La
ruta de ESCRITURA (approve + lock / beginUnbond / withdraw desde la wallet
custodial) se cablea después del despliegue+auditoría, para poder probarla
contra una dirección real; hasta entonces sigue vigente el bond contable de la
Fase A (services/bonds.py).

La clave del bond en el contrato es ``sha256(node_id)`` (bytes32) — determinista
y sin depender de keccak, así el bot escribe y lee con la misma clave.
"""

import hashlib
import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

BOND_CONTRACT: str = os.getenv("SYNERGIX_BOND_CONTRACT", "").strip()

# ABI mínima de solo lectura del contrato desplegado.
_BOND_ABI = [{
    "name": "bondOf", "type": "function", "stateMutability": "view",
    "inputs": [{"name": "nodeId", "type": "bytes32"}],
    "outputs": [
        {"name": "staker", "type": "address"},
        {"name": "amount", "type": "uint256"},
        {"name": "unbondAt", "type": "uint64"},
        {"name": "status", "type": "uint8"},
    ],
}]

# status del contrato: 0=None, 1=Locked, 2=Unbonding.
_STATUS = {0: "none", 1: "locked", 2: "unbonding"}


def contract_enabled() -> bool:
    return bool(BOND_CONTRACT)


def node_id_to_bytes32(node_id: str) -> bytes:
    """Clave bytes32 determinista del bond en el contrato (pura, testeable)."""
    return hashlib.sha256((node_id or "").encode("utf-8")).digest()


async def onchain_bond(node_id: str) -> Optional[Dict[str, Any]]:
    """Lee el bond de un nodo desde el contrato. None si no hay contrato/red."""
    if not contract_enabled():
        return None
    import asyncio
    from aisynergix.services.trading import _get_web3
    try:
        w3 = await asyncio.to_thread(_get_web3)
        contract = w3.eth.contract(
            address=w3.to_checksum_address(BOND_CONTRACT), abi=_BOND_ABI,
        )
        key = node_id_to_bytes32(node_id)
        staker, amount, unbond_at, status = await asyncio.to_thread(
            contract.functions.bondOf(key).call
        )
        return {
            "staker": staker,
            "amount": amount / 1e18,
            "unbond_at": int(unbond_at),
            "status": _STATUS.get(int(status), "none"),
            "onchain": True,
        }
    except Exception as exc:
        logger.warning("onchain_bond %s falló: %s", node_id, exc)
        return None


__all__ = [
    "BOND_CONTRACT",
    "contract_enabled",
    "node_id_to_bytes32",
    "onchain_bond",
]
