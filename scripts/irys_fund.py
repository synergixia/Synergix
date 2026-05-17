"""
irys_fund.py — Utilidad para gestionar el balance en el nodo Irys.

Uso:
    python scripts/irys_fund.py status          # Ver balance actual
    python scripts/irys_fund.py fund 0.001      # Fondear con 0.001 BNB
    python scripts/irys_fund.py estimate        # Estimar coste de operaciones
"""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

import httpx
from eth_account import Account
from eth_account.messages import encode_defunct
from web3 import AsyncWeb3

PRIVATE_KEY   = os.getenv("PRIVATE_KEY", "")
IRYS_NODE_URL = os.getenv("IRYS_NODE_URL", "https://uploader.irys.xyz")
IRYS_TOKEN    = os.getenv("IRYS_TOKEN", "bnb")
BSC_RPC_URL   = os.getenv("BSC_RPC_URL", "https://bsc-dataseed.binance.org/")

BNB_DECIMALS = 18


def _account() -> Account:
    if not PRIVATE_KEY:
        sys.exit("❌ PRIVATE_KEY no está configurada en .env")
    return Account.from_key(PRIVATE_KEY)


async def get_status() -> None:
    """Muestra el balance disponible en el nodo Irys y en la wallet BSC."""
    account = _account()
    address = account.address

    async with httpx.AsyncClient(timeout=15) as cli:
        # ── Balance en Irys ──────────────────────────────────────────────
        try:
            resp = await cli.get(
                f"{IRYS_NODE_URL}/account/balance/{IRYS_TOKEN}",
                params={"address": address},
            )
            if resp.status_code == 200:
                atomic = int(resp.json().get("balance", 0))
                bnb_irys = atomic / 10**BNB_DECIMALS
                print(f"💰 Saldo en Irys nodo : {bnb_irys:.6f} BNB ({atomic} wei)")
            else:
                print(f"⚠️  Irys balance HTTP {resp.status_code}: {resp.text[:200]}")
        except Exception as exc:
            print(f"❌ Error consultando Irys: {exc}")

        # ── Balance en BSC wallet ─────────────────────────────────────────
        try:
            w3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(BSC_RPC_URL))
            wei = await w3.eth.get_balance(address)
            bnb_wallet = wei / 10**BNB_DECIMALS
            print(f"🔑 Saldo en wallet BSC : {bnb_wallet:.6f} BNB")
        except Exception as exc:
            print(f"⚠️  No se pudo consultar balance BSC: {exc}")

        # ── Info del nodo ────────────────────────────────────────────────
        try:
            info = await cli.get(f"{IRYS_NODE_URL}/info")
            if info.status_code == 200:
                d = info.json()
                print(f"\n📡 Nodo Irys: {IRYS_NODE_URL}")
                print(f"   Versión  : {d.get('version', '?')}")
                print(f"   Dirección: {address}")
        except Exception:
            pass


async def fund(amount_bnb: float) -> None:
    """
    Fondea el nodo Irys enviando BNB on-chain (BSC) y notificando al nodo.

    Proceso:
      1. Obtiene la dirección de depósito del nodo Irys
      2. Envía la tx BNB en BSC hacia esa dirección
      3. Notifica al nodo Irys con el tx hash para que acredite el saldo
    """
    account = _account()
    amount_wei = int(amount_bnb * 10**BNB_DECIMALS)

    print(f"🔄 Fondeando Irys con {amount_bnb} BNB ({amount_wei} wei)...")

    async with httpx.AsyncClient(timeout=30) as cli:
        # ── Paso 1: Obtener la dirección de depósito del nodo ────────────
        try:
            resp = await cli.get(f"{IRYS_NODE_URL}/info")
            resp.raise_for_status()
            node_address = resp.json().get("addresses", {}).get(IRYS_TOKEN)
            if not node_address:
                # Fallback: algunos nodos exponen la dirección en /account
                resp2 = await cli.get(f"{IRYS_NODE_URL}/account/{IRYS_TOKEN}")
                node_address = resp2.json().get("address") if resp2.status_code == 200 else None
        except Exception as exc:
            sys.exit(f"❌ No se pudo obtener la dirección del nodo Irys: {exc}")

        if not node_address:
            sys.exit(
                "❌ No se encontró la dirección de depósito del nodo Irys.\n"
                f"   Visita {IRYS_NODE_URL}/info para obtenerla manualmente\n"
                "   y envía BNB directamente desde tu wallet a esa dirección."
            )

        print(f"📬 Dirección de depósito Irys: {node_address}")

        # ── Paso 2: Enviar BNB on-chain ──────────────────────────────────
        w3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(BSC_RPC_URL))
        nonce = await w3.eth.get_transaction_count(account.address)
        gas_price = await w3.eth.gas_price

        tx = {
            "to":       node_address,
            "value":    amount_wei,
            "gas":      21000,
            "gasPrice": gas_price,
            "nonce":    nonce,
            "chainId":  56,  # BSC mainnet
        }

        signed = account.sign_transaction(tx)
        tx_hash = await w3.eth.send_raw_transaction(signed.rawTransaction)
        tx_hash_hex = tx_hash.hex()
        print(f"✅ TX enviada en BSC: {tx_hash_hex}")
        print("⏳ Esperando confirmación...")

        receipt = await w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        if receipt["status"] != 1:
            sys.exit(f"❌ TX fallida en BSC: {tx_hash_hex}")
        print(f"✅ TX confirmada en bloque {receipt['blockNumber']}")

        # ── Paso 3: Notificar al nodo Irys ───────────────────────────────
        try:
            payload = {"tx_id": tx_hash_hex}
            fund_resp = await cli.post(
                f"{IRYS_NODE_URL}/account/balance/{IRYS_TOKEN}",
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=30,
            )
            if fund_resp.status_code in (200, 201):
                new_balance = fund_resp.json().get("balance", "?")
                print(f"💰 Nuevo saldo en Irys: {int(new_balance) / 10**18:.6f} BNB")
            else:
                print(
                    f"⚠️  Notificación al nodo Irys retornó HTTP {fund_resp.status_code}.\n"
                    f"   El saldo puede tardar unos minutos en reflejarse.\n"
                    f"   Respuesta: {fund_resp.text[:200]}"
                )
        except Exception as exc:
            print(
                f"⚠️  No se pudo notificar al nodo Irys: {exc}\n"
                f"   TX on-chain confirmada ({tx_hash_hex}). El balance\n"
                "   se acreditará automáticamente en unos minutos."
            )


async def estimate() -> None:
    """Estima el coste en BNB de las operaciones principales de Synergix."""
    async with httpx.AsyncClient(timeout=15) as cli:
        sizes = {
            "Perfil de usuario  (tags, ~100 B)":   100,
            "Aporte texto       (~500 B)":          500,
            "Aporte texto largo (~2 KB)":          2048,
            "Brain index FAISS  (~5 MB)":       5 * 1024 * 1024,
        }
        print(f"📊 Estimación de costes en {IRYS_NODE_URL} (token: {IRYS_TOKEN})\n")
        for label, size_bytes in sizes.items():
            try:
                resp = await cli.get(
                    f"{IRYS_NODE_URL}/price/{IRYS_TOKEN}/{size_bytes}"
                )
                if resp.status_code == 200:
                    atomic = int(resp.text.strip())
                    bnb = atomic / 10**BNB_DECIMALS
                    print(f"   {label}: {bnb:.8f} BNB")
                else:
                    print(f"   {label}: HTTP {resp.status_code}")
            except Exception as exc:
                print(f"   {label}: error — {exc}")


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"

    if cmd == "status":
        asyncio.run(get_status())
    elif cmd == "fund":
        if len(sys.argv) < 3:
            sys.exit("Uso: python scripts/irys_fund.py fund <cantidad_BNB>")
        asyncio.run(fund(float(sys.argv[2])))
    elif cmd == "estimate":
        asyncio.run(estimate())
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
