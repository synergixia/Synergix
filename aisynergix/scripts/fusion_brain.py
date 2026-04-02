"""
aisynergix/scripts/fusion_brain.py
═══════════════════════════════════════════════════════════════════════════════
Ciclo de evolución federada de Synergix.
Se ejecuta cada 20 minutos desde el bot (federation_loop).

Flujo:
  1. Lee todos los summaries del RAG cache (aportes de la comunidad)
  2. Fusiona con Qwen 1.5B → wisdom colectivo
  3. Construye el cerebro completo (header + wisdom + inventario)
  4. Sube a aisynergix/SYNERGIXAI/Synergix_ia_{ts}.txt en Greenfield
  5. Actualiza brain_latest en la DB local
═══════════════════════════════════════════════════════════════════════════════
"""

import asyncio
import hashlib
import json
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger("synergix.fusion")


async def build_and_upload_brain(db: dict, rag_cache: dict,
                                  gf_upload_fn, save_db_fn) -> bool:
    """
    Construye y sube el cerebro fusionado a Greenfield.

    Args:
        db:           DB local de Synergix
        rag_cache:    Cache RAG con todos los aportes indexados
        gf_upload_fn: Función gf_upload(content, object_name, metadata)
        save_db_fn:   Función save_db()

    Returns:
        True si el upload fue exitoso
    """
    from aisynergix.config.paths import GF, LOCAL_BRAIN_DIR
    from aisynergix.bot.local_ia import fuse_brain

    now = datetime.now(timezone.utc)
    ts  = now.strftime("%Y%m%d_%H%M%S")

    # ── 1. Recopilar summaries del RAG cache ──────────────────────────────────
    all_summaries = []
    for obj, meta in rag_cache.items():
        summary = meta.get("ai-summary", meta.get("summary", ""))
        if summary and len(summary) > 5:
            all_summaries.append(summary)

    total_aportes = db.get("global_stats", {}).get("total_contributions", len(all_summaries))
    total_users   = len(db.get("reputation", {}))

    logger.info("🧠 Fusionando cerebro: %d summaries, %d usuarios",
                len(all_summaries), total_users)

    # ── 2. Generar wisdom con Qwen 1.5B ──────────────────────────────────────
    wisdom = ""
    if all_summaries:
        try:
            wisdom = await fuse_brain(all_summaries[:30])
        except Exception as e:
            logger.warning("⚠️ fuse_brain error: %s — usando summaries directos", e)
            wisdom = " | ".join(all_summaries[:5])

    if not wisdom:
        wisdom = db["global_stats"].get("collective_wisdom", "Synergix collective brain initializing...")

    # ── 3. Construir inventario del RAG cache ─────────────────────────────────
    inventory_lines = []
    for obj, meta in sorted(rag_cache.items(),
                             key=lambda x: -x[1].get("quality-score", 0))[:50]:
        tag     = meta.get("knowledge-tag", "general")
        summary = meta.get("ai-summary", "")[:100]
        score   = meta.get("quality-score", 5)
        impact  = meta.get("impact", 0)
        inventory_lines.append(f"- [{tag}] score:{score}/10 impact:{impact} | {summary}")

    inventory_text = "\n".join(inventory_lines) if inventory_lines else "No contributions yet."

    # ── 4. Construir el texto del cerebro ─────────────────────────────────────
    brain_text = (
        f"=== SYNERGIX COLLECTIVE BRAIN ===\n"
        f"Actualizado: {now.isoformat()}\n"
        f"Aportes procesados: {total_aportes}\n"
        f"Usuarios activos: {total_users}\n"
        f"Versión: {ts}\n\n"
        f"=== CONOCIMIENTO FUSIONADO ===\n"
        f"{wisdom}\n\n"
        f"=== INVENTARIO ===\n"
        f"{inventory_text}\n"
    )

    # ── 5. Hash de integridad ─────────────────────────────────────────────────
    brain_hash = hashlib.sha256(brain_text.encode()).hexdigest()[:16]

    # ── 6. Metadatos on-chain ─────────────────────────────────────────────────
    metadata = {
        "x-amz-meta-last-sync":      now.strftime("%Y-%m-%dT%H:%M:%S"),
        "x-amz-meta-vector-count":   str(len(all_summaries)),
        "x-amz-meta-last-fusion-ts": f"{ts}|total:{total_aportes}",
        "x-amz-meta-integrity-hash": brain_hash,
    }

    # ── 7. Guardar copia local ────────────────────────────────────────────────
    os.makedirs(LOCAL_BRAIN_DIR, exist_ok=True)
    local_path = os.path.join(LOCAL_BRAIN_DIR, f"Synergix_ia_{ts}.txt")
    try:
        with open(local_path, "w", encoding="utf-8") as f:
            f.write(brain_text)
    except Exception as e:
        logger.warning("⚠️ No se pudo guardar copia local del cerebro: %s", e)

    # ── 8. Subir a Greenfield ─────────────────────────────────────────────────
    versioned_name = GF.brain_versioned(ts)
    try:
        import asyncio as _aio
        loop = _aio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: gf_upload_fn(brain_text, versioned_name, metadata,
                                  uid="system", upsert=True, only_tags=False)
        )
        if result.get("success"):
            # Actualizar puntero en la DB
            db.setdefault("global_stats", {})
            db["global_stats"]["brain_latest"]       = versioned_name
            db["global_stats"]["collective_wisdom"]  = wisdom[:500]
            db["global_stats"]["last_fusion"]        = now.isoformat()
            save_db_fn()
            logger.info("✅ Cerebro subido: %s | hash=%s", versioned_name, brain_hash)
            return True
        else:
            logger.error("❌ Upload cerebro falló: %s", result)
            return False
    except Exception as e:
        logger.error("❌ Error subiendo cerebro: %s", e)
        return False
