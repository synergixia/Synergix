"""passport.py — Synergix Passport: identidad de reputación on-chain (§10).

El Passport agrega la reputación VERIFICABLE del usuario y la sella como
DataItem público en Irys/Arweave, ligado únicamente al Ghost ID (nunca al
UID de Telegram — §10.3).  Terceros (cooperativas, microfinancieras, otras
apps) pueden leerlo por GraphQL/gateway sin conocer la identidad real.

Contenido (§10.2): rango y puntos, aportes validados y score medio,
impactos verificados acumulados, SYNX ganados históricos (no el saldo),
y reputación como Juez Oráculo (tasa de acierto).

Nota Fase 3: el Passport vive en Arweave (permanente y verificable).  El
espejo como SBT en un contrato BSC llega cuando se desplieguen contratos;
el esquema de datos ya es el del SBT.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

PASSPORT_VERSION = "1.1"


def _aporte_tags(a: Any) -> Dict[str, str]:
    if isinstance(a, dict):
        return a.get("tags", a) if isinstance(a.get("tags"), dict) else a
    return {}


def _aporte_category(a: Any) -> str:
    tags = _aporte_tags(a)
    return (tags.get("category") or tags.get("topic") or "").strip().lower()


def domain_breakdown(aporte_tags: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    """Desglose de conocimiento por dominio (§10.2, Proof-of-Knowledge).

    Devuelve una lista ordenada por nº de aportes descendente:
    ``[{domain, contributions, avg_score}, …]``. Función pura y testeable —
    convierte el Passport en una credencial de experiencia por dominio
    ("contribuidor verificado en Salud: 12 aportes, score medio 7.8").
    """
    agg: Dict[str, Dict[str, float]] = {}
    for a in aporte_tags:
        domain = _aporte_category(a)
        if not domain:
            continue
        bucket = agg.setdefault(domain, {"n": 0.0, "score_sum": 0.0, "scored": 0.0})
        bucket["n"] += 1
        raw = _aporte_tags(a).get("quality-score") or _aporte_tags(a).get("quality_score")
        try:
            if raw is not None:
                bucket["score_sum"] += float(raw)
                bucket["scored"] += 1
        except (ValueError, TypeError):
            pass
    out: List[Dict[str, Any]] = []
    for domain, b in agg.items():
        avg = round(b["score_sum"] / b["scored"], 2) if b["scored"] else None
        out.append({
            "domain": domain,
            "contributions": int(b["n"]),
            "avg_score": avg,
        })
    out.sort(key=lambda d: (-d["contributions"], d["domain"]))
    return out


def bounties_summary(claim_tags: List[Dict[str, str]]) -> Dict[str, Any]:
    """Resumen de bounties cumplidos: nº y SYNX ganados (función pura)."""
    total = 0.0
    for c in claim_tags:
        try:
            total += float(c.get("amount", 0) or 0)
        except (ValueError, TypeError):
            pass
    return {"fulfilled": len(claim_tags), "synx_earned": round(total, 2)}


def aggregate_impacts(counter_tags: List[Dict[str, str]]) -> Dict[str, Any]:
    """Suma los contadores de impacto de todos los aportes de un autor."""
    total = {"views": 0, "useful": 0, "references": 0, "royalties_synx": 0.0}
    for tags in counter_tags:
        for field in ("views", "useful", "references"):
            try:
                total[field] += int(tags.get(field, 0) or 0)
            except (ValueError, TypeError):
                pass
        try:
            total["royalties_synx"] += float(tags.get("synx-paid", 0) or 0)
        except (ValueError, TypeError):
            pass
    total["royalties_synx"] = round(total["royalties_synx"], 2)
    return total


def oracle_accuracy(stake_tags: Optional[Dict[str, str]]) -> Optional[float]:
    """Tasa de acierto como juez [0..1], o None si nunca votó."""
    if not stake_tags:
        return None
    try:
        total = int(stake_tags.get("votes-total", 0) or 0)
        correct = int(stake_tags.get("votes-correct", 0) or 0)
    except (ValueError, TypeError):
        return None
    if total <= 0:
        return None
    return round(min(1.0, correct / total), 3)


def average_score(aporte_tags: List[Dict[str, str]]) -> Optional[float]:
    """Score medio del Judge sobre los aportes del usuario."""
    scores: List[float] = []
    for a in aporte_tags:
        tags = a.get("tags", a) if isinstance(a, dict) else {}
        raw = tags.get("quality-score") or tags.get("quality_score")
        try:
            if raw is not None:
                scores.append(float(raw))
        except (ValueError, TypeError):
            continue
    if not scores:
        return None
    return round(sum(scores) / len(scores), 2)


async def build_passport(uid: int) -> Optional[Dict[str, Any]]:
    """Agrega la reputación del usuario y sella su Passport en Irys.

    Retorna {"data": ..., "tx": ...} o None si el sellado falla.
    """
    from aisynergix.bot.identity import get_identity_manager, _hash_uid
    from aisynergix.services.irys import (
        list_aportes, list_impact_counters_by_author, read_oracle_stake,
        list_bounty_claims_by_author, write_passport,
    )

    identity = get_identity_manager()
    profile = await identity.get_profile(uid)
    uid_hash = _hash_uid(uid)

    try:
        aportes = await list_aportes(uid_hash, limit=200)
    except Exception:
        aportes = []
    try:
        counters = await list_impact_counters_by_author(uid_hash)
    except Exception:
        counters = []
    try:
        stake_tags = await read_oracle_stake(uid_hash)
    except Exception:
        stake_tags = None
    try:
        bounty_claims = await list_bounty_claims_by_author(uid_hash)
    except Exception:
        bounty_claims = []

    impacts = aggregate_impacts(counters)
    data: Dict[str, Any] = {
        "version": PASSPORT_VERSION,
        "ghost_id": uid_hash,
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "rank": profile.rank,
        "points": profile.points,
        "contributions": max(profile.contribution_count, len(aportes)),
        "avg_quality_score": average_score(aportes),
        "impacts": impacts,
        # Proof-of-Knowledge: experiencia por dominio + bounties cumplidos.
        "domains": domain_breakdown(aportes),
        "bounties": bounties_summary(bounty_claims),
        "synx_earned": round(profile.synx_earned_total, 2),
        "human_verified": profile.human_verified,
        "oracle": {
            "is_oracle": bool(stake_tags and stake_tags.get("stake-status") == "active"),
            "votes_total": int((stake_tags or {}).get("votes-total", 0) or 0),
            "accuracy": oracle_accuracy(stake_tags),
        },
    }

    try:
        tx_id = await write_passport(uid_hash, data)
    except Exception as exc:
        logger.error("build_passport: sellado falló para %s: %s", uid_hash, exc)
        return None
    return {"data": data, "tx": tx_id}


async def get_passport(uid_hash: str) -> Optional[Dict[str, Any]]:
    """Lee el Passport público vigente de un Ghost ID (para terceros/API)."""
    from aisynergix.services.irys import read_passport
    return await read_passport(uid_hash)


__all__ = [
    "PASSPORT_VERSION",
    "aggregate_impacts",
    "oracle_accuracy",
    "average_score",
    "domain_breakdown",
    "bounties_summary",
    "build_passport",
    "get_passport",
]
