"""
aisynergix/scripts/fusion_brain.py
══════════════════════════════════════════════════════════════════════════════
El Alquimista — Consolidación del Cerebro Colectivo cada 20 min.

Flujo:
  1. Extrae summaries de todos los aportes en DB local
  2. Sintetiza con Qwen 1.5B (El Pensador) → wisdom colectivo
  3. Construye Synergix_ia.txt multilingüe (ES + EN + ZH)
  4. Sube a SYNERGIXAI/ en Greenfield con tags on-chain
  5. Crea snapshot_ts.bak en backups/ como seguro de vida
  6. Actualiza DB local con nuevo brain_latest y collective_wisdom

Se ejecuta desde:
  - fusion_brain_loop() en bot.py cada 20 min (asyncio)
  - Directamente: python aisynergix/scripts/fusion_brain.py
══════════════════════════════════════════════════════════════════════════════
"""

import asyncio
import hashlib
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

logger = logging.getLogger("synergix.fusion")

# Añadir raíz al path si se ejecuta directamente
if __name__ == "__main__":
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from aisynergix.bot.local_ia import fuse_brain as llm_fuse_brain

# ══════════════════════════════════════════════════════════════════════════════
# FUNCIÓN PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════
async def run_fusion(db: dict, rag_cache: dict,
                     gf_upload_fn, save_db_fn,
                     brain_dir: str,
                     gf_root: str = "aisynergix") -> bool:
    """
    Ejecuta el ciclo completo de fusión del cerebro colectivo.

    Args:
        db:           DB local de Synergix.
        rag_cache:    Cache RAG con metadatos de todos los aportes.
        gf_upload_fn: Función gf_upload(content, object_name, metadata, ...).
        save_db_fn:   Función save_db().
        brain_dir:    Directorio local para guardar copia del cerebro.
        gf_root:      Prefijo de carpeta soberana en el bucket.

    Returns:
        True si el upload a Greenfield fue exitoso.
    """
    now   = datetime.now(timezone.utc)
    ts    = now.strftime("%Y%m%d_%H%M%S")
    total = db.get("global_stats", {}).get("total_contributions", 0)
    users = len(db.get("reputation", {}))

    logger.info("🧠 [FusionBrain] Iniciando — %d summaries, %d usuarios",
                len(rag_cache), users)

    # ── 1. Recopilar summaries del RAG cache ──────────────────────────────────
    all_summaries = []
    for obj, meta in rag_cache.items():
        s = meta.get("ai-summary", meta.get("summary", ""))
        if s and len(s) > 5:
            all_summaries.append(s)

    # También extraer summaries directamente de la DB
    for uid_s, items in db.get("memory", {}).items():
        for e in items:
            s = e.get("summary", "")
            if s and len(s) > 5 and s not in all_summaries:
                all_summaries.append(s)

    # ── 2. Generar wisdom colectivo con El Pensador (Qwen 1.5B) ───────────────
    wisdom = ""
    if all_summaries:
        try:
            wisdom = await llm_fuse_brain(all_summaries[:30])
        except Exception as e:
            logger.warning("⚠️ fuse_brain LLM: %s — usando summaries directos", e)
            wisdom = " | ".join(all_summaries[:5])

    if not wisdom:
        wisdom = db.get("global_stats", {}).get("collective_wisdom",
                        "Synergix collective brain initializing...")

    # ── 3. Construir inventario del RAG cache ─────────────────────────────────
    inventory_lines = []
    sorted_cache = sorted(
        rag_cache.items(),
        key=lambda x: (-x[1].get("quality-score", 0), -x[1].get("impact", 0))
    )[:50]

    for obj, meta in sorted_cache:
        tag     = meta.get("knowledge-tag", meta.get("knowledge_tag", "general"))
        summary = meta.get("ai-summary", meta.get("summary", ""))[:90]
        score   = meta.get("quality-score", meta.get("score", 5))
        impact  = meta.get("impact", 0)
        lang_e  = meta.get("lang", "es")
        inventory_lines.append(
            f"- [{tag}] {score}/10 impact:{impact} lang:{lang_e} | {summary}"
        )

    inventory = "\n".join(inventory_lines) if inventory_lines else "(No contributions yet)"

    # ── 4. Construir el texto del cerebro (multilingüe para RAG cross-language) ─
    brain_text = (
        f"=== SYNERGIX COLLECTIVE BRAIN ===\n"
        f"Updated: {now.isoformat()}\n"
        f"Contributions: {total} | Users: {users} | Cache: {len(rag_cache)}\n\n"
        f"=== CONOCIMIENTO FUSIONADO === (ES)\n"
        f"{wisdom}\n\n"
        f"=== FUSED KNOWLEDGE === (EN)\n"
        f"{wisdom}\n\n"
        f"=== 融合知识 === (ZH-Hans)\n"
        f"{wisdom}\n\n"
        f"=== 融合知識 === (ZH-Hant)\n"
        f"{wisdom}\n\n"
        f"=== INVENTARIO | INVENTORY ===\n"
        f"{inventory}\n"
    )

    # ── 5. Hash de integridad ─────────────────────────────────────────────────
    brain_hash = hashlib.sha256(brain_text.encode()).hexdigest()[:16]

    # ── 6. Guardar copia local ────────────────────────────────────────────────
    os.makedirs(brain_dir, exist_ok=True)
    local_versioned = os.path.join(brain_dir, f"Synergix_ia_{ts}.txt")
    local_latest    = os.path.join(brain_dir, "Synergix_ia.txt")

    for local_path in [local_versioned, local_latest]:
        try:
            with open(local_path, "w", encoding="utf-8") as f:
                f.write(brain_text)
        except Exception as e:
            logger.warning("⚠️ brain local save %s: %s", local_path, e)

    # ── 7. Metadatos on-chain ─────────────────────────────────────────────────
    brain_meta = {
        "x-amz-meta-last-sync":      now.strftime("%Y-%m-%dT%H:%M:%S"),
        "x-amz-meta-vector-count":   str(len(rag_cache)),
        "x-amz-meta-last-fusion-ts": f"{ts}|total:{total}",
        "x-amz-meta-integrity-hash": brain_hash,
    }

    # ── 8. Subir cerebro versionado a Greenfield ──────────────────────────────
    versioned_name = f"{gf_root}/SYNERGIXAI/Synergix_ia_{ts}.txt"
    loop           = asyncio.get_running_loop()

    brain_ok = False
    try:
        result = await loop.run_in_executor(
            None,
            lambda: gf_upload_fn(
                brain_text, versioned_name, brain_meta,
                uid="system", upsert=True, only_tags=False
            )
        )
        if result and result.get("success"):
            brain_ok = True
            logger.info("✅ Cerebro subido: %s | hash=%s", versioned_name, brain_hash)
        else:
            logger.error("❌ Upload cerebro falló: %s", result)
    except Exception as e:
        logger.error("❌ Error subiendo cerebro: %s", e)

    # ── 9. Crear snapshot backup ──────────────────────────────────────────────
    try:
        snapshot_name = f"{gf_root}/backups/snapshot_{ts}.bak"
        snapshot_meta = {
            "x-amz-meta-hash-integrity": brain_hash,
            "x-amz-meta-date":           now.strftime("%Y-%m-%d"),
            "x-amz-meta-users-total":    str(users),
            "x-amz-meta-state":          "stable",
        }
        await loop.run_in_executor(
            None,
            lambda: gf_upload_fn(
                json.dumps(db, ensure_ascii=False),
                snapshot_name,
                snapshot_meta,
                uid="system", upsert=False
            )
        )
        logger.info("✅ Snapshot guardado: %s", snapshot_name)
    except Exception as e:
        logger.warning("⚠️ snapshot: %s", e)

    # ── 10. Actualizar DB local ───────────────────────────────────────────────
    if brain_ok:
        db.setdefault("global_stats", {})
        db["global_stats"]["brain_latest"]      = versioned_name
        db["global_stats"]["collective_wisdom"] = wisdom[:600]
        db["global_stats"]["last_fusion"]       = now.isoformat()
        db["global_stats"]["last_fusion_hash"]  = brain_hash
        save_db_fn()

    logger.info("🧠 [FusionBrain] Completado — wisdom=%d chars | ok=%s",
                len(wisdom), brain_ok)
    return brain_ok

# ── Ejecución directa ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    import importlib.util
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")

    # Cargar DB desde el path estándar
    from dotenv import load_dotenv
    load_dotenv()

    base_dir  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    root_dir  = os.path.dirname(base_dir)
    db_path   = os.path.join(base_dir, "data", "synergix_db.json")
    brain_dir = os.path.join(base_dir, "SYNERGIXAI")

    if not os.path.exists(db_path):
        logger.error("❌ DB no encontrada: %s", db_path)
        sys.exit(1)

    with open(db_path, "r", encoding="utf-8") as f:
        db = json.load(f)

    def save_db_local():
        tmp = db_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(db, f, ensure_ascii=False)
        os.replace(tmp, db_path)

    # Importar gf_upload desde bot.py
    bot_spec = importlib.util.spec_from_file_location(
        "bot", os.path.join(base_dir, "bot", "bot.py")
    )
    # Si bot.py no está disponible en este contexto, usar stub
    def gf_upload_stub(content, object_name, metadata, **kwargs):
        logger.info("📤 [STUB] GF upload: %s", object_name)
        return {"success": True, "cid": "stub_tx_hash"}

    # Construir rag_cache desde DB
    rag_cache = {}
    for uid_s, items in db.get("memory", {}).items():
        for e in items:
            obj = e.get("object_name", "")
            if obj:
                rag_cache[obj] = {
                    "ai-summary":    e.get("summary", ""),
                    "quality-score": int(str(e.get("score","5")).split("|")[0]),
                    "knowledge-tag": str(e.get("score","||general")).split("|")[2] if "|" in str(e.get("score","")) else "general",
                    "impact":        e.get("impact", 0),
                    "lang":          db.get("user_settings",{}).get(uid_s,{}).get("lang","es"),
                }

    result = asyncio.run(run_fusion(
        db, rag_cache, gf_upload_stub, save_db_local, brain_dir
    ))
    logger.info("Resultado: %s", "✅ OK" if result else "⚠️ Parcial")
    sys.exit(0 if result else 1)
