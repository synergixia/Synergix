"""knowledge_api.py — API de Conocimiento que paga a humanos (Proof-of-Knowledge ③).

Una app de IA externa consulta el conocimiento verificado de Synergix con una
API key. Synergix responde con fragmentos FUNDAMENTADOS y sus CITAS (autor
Ghost ID + tx en Arweave), cobra a la key y reparte el pago: la mayor parte
(``AUTHOR_SHARE_PCT``) va a los humanos cuyos aportes se usaron. Es el foso del
protocolo: la capa de datos que remunera a las personas.

Arquitectura segura entre procesos:
  * La API (proceso read-only) cobra a la KEY (dato propio de la API, sin
    carrera con el ledger de usuarios) y registra un evento ``api-usage`` con
    el reparto por autor, en estado "pending".
  * La LIQUIDACIÓN corre en el proceso del bot (dueño del ledger SYNX):
    acredita a cada autor su parte y marca el evento "settled". Idempotente
    por evento (última versión gana), igual que el canje.

La contabilidad (precio, reparto, codificación) es pura y testeable sin red.
"""

import asyncio
import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

API_QUERY_PRICE = 1.0        # SYNX por consulta (precio plano, predecible)
AUTHOR_SHARE_PCT = 0.70     # 70% para los humanos citados; 30% protocolo
DEFAULT_TOP_K = 5

# Locks por key en el proceso de la API para que dos consultas concurrentes
# no gasten el mismo saldo (un solo proceso → asyncio.Lock basta).
_key_locks: Dict[str, asyncio.Lock] = {}
# Locks por evento en el proceso del bot para la liquidación idempotente.
_settle_locks: Dict[str, asyncio.Lock] = {}


def _lock(store: Dict[str, asyncio.Lock], key: str) -> asyncio.Lock:
    lk = store.get(key)
    if lk is None:
        lk = asyncio.Lock()
        store[key] = lk
    return lk


def hash_api_key(raw_key: str) -> str:
    """Hash de la API key (nunca se guarda la clave en claro)."""
    return "ak_" + hashlib.sha256((raw_key or "").encode()).hexdigest()[:32]


def generate_usage_id(key_hash: str, ts: int, nonce: str) -> str:
    raw = f"{key_hash}:{ts}:{nonce}"
    return "use_" + hashlib.sha256(raw.encode()).hexdigest()[:16]


# ── Contabilidad pura (testeable) ────────────────────────────────────────

def _f(v: Any, d: float = 0.0) -> float:
    try:
        return float(v)
    except (ValueError, TypeError):
        return d


def price_for(_n_fragments: int) -> float:
    """Precio de una consulta. Plano por ahora (predecible para el cliente)."""
    return round(API_QUERY_PRICE, 2)


def distribute(price: float, author_hashes: List[str]) -> Tuple[float, Dict[str, float]]:
    """Reparte el precio: (parte_protocolo, {autor: monto}).

    La cuota de autores (``AUTHOR_SHARE_PCT``) se divide en partes iguales
    entre autores DISTINTOS citados. Si no hay autores, todo va al protocolo.
    Función pura: la suma de las partes de autor + protocolo == precio.
    """
    price = round(max(0.0, _f(price)), 2)
    uniq = [h for h in dict.fromkeys(a for a in author_hashes if a)]
    if not uniq or price <= 0:
        return price, {}
    author_pool = round(price * AUTHOR_SHARE_PCT, 2)
    each = round(author_pool / len(uniq), 4)
    per: Dict[str, float] = {h: each for h in uniq}
    distributed = round(each * len(uniq), 2)
    protocol = round(price - distributed, 2)
    return protocol, per


def encode_distribution(per: Dict[str, float]) -> str:
    """Codifica {autor: monto} como 'h:monto,h:monto' para el tag de Irys."""
    return ",".join(f"{h}:{amt:.4f}" for h, amt in per.items())


def decode_distribution(encoded: str) -> Dict[str, float]:
    """Inverso de encode_distribution; ignora entradas corruptas."""
    out: Dict[str, float] = {}
    for part in (encoded or "").split(","):
        if ":" not in part:
            continue
        h, _, amt = part.partition(":")
        h = h.strip()
        if h:
            out[h] = round(_f(amt), 4)
    return out


# ── API keys (escrow prepago de la key) ──────────────────────────────────

async def verify_and_charge(raw_key: str, price: float) -> Tuple[Optional[Dict[str, str]], Optional[str]]:
    """Comprueba la key y le descuenta ``price`` de su saldo prepago.

    Devuelve (registro_key, None) o (None, error). Errores: "bad_key" |
    "inactive" | "insufficient_credit". El saldo de la key es dato propio de
    la API (sin carrera con el ledger de usuarios).
    """
    from aisynergix.services.irys import read_api_key, write_api_key
    key_hash = hash_api_key(raw_key)
    async with _lock(_key_locks, key_hash):
        rec = await read_api_key(key_hash)
        if not rec:
            return None, "bad_key"
        if rec.get("key-status", "active") != "active":
            return None, "inactive"
        balance = _f(rec.get("balance", 0))
        if balance + 1e-9 < price:
            return None, "insufficient_credit"
        await write_api_key(
            key_hash, rec.get("owner", ""), round(balance - price, 4),
            status="active",
        )
        return rec, None


# ── Consulta con atribución ──────────────────────────────────────────────

async def answer_query(
    raw_key: str, question: str, top_k: int = DEFAULT_TOP_K,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Responde una consulta externa con fragmentos citados y cobra a la key.

    Devuelve (respuesta, None) o (None, error). No credita a los autores aquí
    (eso lo hace la liquidación en el proceso del bot); registra el evento.
    """
    question = (question or "").strip()
    if len(question) < 3:
        return None, "bad_query"

    # Recuperar conocimiento fundamentado (RAG). Import perezoso: mantiene la
    # API ligera si el motor no está cargado en este proceso.
    from aisynergix.services.rag_engine import get_rag_engine
    rag = await get_rag_engine()
    _context, results = await rag.query(question, target_language="en")
    results = (results or [])[:top_k]

    citations: List[Dict[str, Any]] = []
    author_hashes: List[str] = []
    for r in results:
        meta = r.get("metadata", {})
        author = meta.get("author_uid", "")
        tx = meta.get("object_name", "")
        if tx.startswith("local:"):
            tx = ""
        citations.append({
            "author": author,
            "arweave_tx": tx,
            "language": meta.get("language", ""),
            "score": round(float(r.get("score", 0) or 0), 3),
            "excerpt": (r.get("text", "") or "")[:280],
        })
        if author:
            author_hashes.append(author)

    price = price_for(len(citations))
    _rec, err = await verify_and_charge(raw_key, price)
    if err:
        return None, err

    protocol, per_author = distribute(price, author_hashes)
    ts = int(datetime.now(timezone.utc).timestamp())
    key_hash = hash_api_key(raw_key)
    usage_id = generate_usage_id(key_hash, ts, question[:16])
    try:
        from aisynergix.services.irys import write_api_usage
        await write_api_usage(
            usage_id, key_hash, price, encode_distribution(per_author),
            status="pending",
        )
    except Exception as exc:
        logger.warning("answer_query: registro de uso falló: %s", exc)

    return {
        "usage_id": usage_id,
        "question": question,
        "citations": citations,
        "price_synx": price,
        "author_share_synx": round(sum(per_author.values()), 2),
        "protocol_share_synx": protocol,
        "authors_paid": len(per_author),
    }, None


# ── Liquidación (proceso del bot; dueño del ledger SYNX) ──────────────────

async def settle_pending_usage(limit: int = 50) -> Dict[str, Any]:
    """Acredita a los autores citados de los eventos pendientes. Idempotente.

    Se ejecuta en el proceso del bot. Cada evento se marca "settled" tras
    pagar; un evento ya saldado se ignora (última versión gana).
    """
    from aisynergix.bot.identity import get_identity_manager
    from aisynergix.services.irys import (
        list_pending_api_usage, write_api_usage,
    )
    identity = get_identity_manager()
    events = await list_pending_api_usage(limit=limit)
    paid_events = 0
    paid_synx = 0.0
    for ev in events:
        usage_id = ev.get("usage-id", "")
        if not usage_id:
            continue
        async with _lock(_settle_locks, usage_id):
            # Re-leer no es necesario: list_pending ya filtró; el lock evita
            # doble liquidación concurrente dentro del proceso.
            if ev.get("usage-status", "pending") != "pending":
                continue
            per = decode_distribution(ev.get("authors", ""))
            for author, amount in per.items():
                if amount > 0:
                    try:
                        await identity.credit_synx(author, round(amount, 4))
                        paid_synx += amount
                    except Exception as exc:
                        logger.warning("settle: crédito a %s falló: %s", author, exc)
            try:
                await write_api_usage(
                    usage_id, ev.get("key-hash", ""), _f(ev.get("price", 0)),
                    ev.get("authors", ""), status="settled",
                )
                paid_events += 1
            except Exception as exc:
                logger.error("settle: no se pudo marcar %s: %s", usage_id, exc)
    if paid_events:
        logger.info("💸 API: %d eventos liquidados, %.2f SYNX a autores",
                    paid_events, paid_synx)
    return {"events": paid_events, "synx_to_authors": round(paid_synx, 2)}


__all__ = [
    "API_QUERY_PRICE", "AUTHOR_SHARE_PCT", "DEFAULT_TOP_K",
    "hash_api_key", "generate_usage_id", "price_for", "distribute",
    "encode_distribution", "decode_distribution",
    "verify_and_charge", "answer_query", "settle_pending_usage",
]
