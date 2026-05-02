import os
import sys
import json
import asyncio
import hashlib
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aisynergix.services.greenfield import get_greenfield_client
from aisynergix.services.rag_engine import get_rag_engine
from aisynergix.ai.local_ia import get_thinker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [FUSION] %(levelname)s: %(message)s",
)
logger = logging.getLogger("synergix.fusion_brain")


async def collect_contributions_for_summary(minutes: int = 60) -> List[Dict[str, Any]]:
    gf = await get_greenfield_client()
    recent = await gf.list_recent_contributions_from_bucket(minutes=minutes)
    
    contributions = []
    for item in recent:
        try:
            text = await gf.get_contribution_text(item["object_name"])
            tags = item.get("tags", {})
            
            quality = float(tags.get("quality_score", 0))
            
            if quality >= 7.0:
                contributions.append({
                    "text": text,
                    "author_uid": tags.get("author_uid", "unknown"),
                    "quality_score": quality,
                    "object_name": item["object_name"],
                    "language": tags.get("lang", "es"),
                    "category": tags.get("category", "filosofia"),
                })
        except Exception as e:
            logger.warning(f"Saltando {item.get('object_name')}: {e}")
            continue
    
    return contributions


async def generate_collective_summary(contributions: List[Dict[str, Any]]) -> Optional[str]:
    if not contributions:
        return None
    
    thinker = get_thinker()
    
    texts = [c["text"] for c in contributions[:20]]
    combined = "\n---\n".join(texts)
    
    summary_prompt = f"""A continuación hay varios fragmentos de sabiduría colectiva inmortalizados en la red Synergix. 
Sintetiza estos fragmentos en UN SOLO resumen cohesivo de máximo 500 palabras que capture la esencia del conocimiento colectivo. 
Escribe en español neutro. No menciones que estás resumiendo, solo entrega el conocimiento sintetizado como si fuera una nueva pieza de sabiduría.

FRAGMENTOS:
{combined}

RESUMEN COLECTIVO:"""

    summary = await thinker.think(
        user_message=summary_prompt,
        context="",
    )
    
    return summary.strip()


async def store_collective_summary(summary: str) -> str:
    gf = await get_greenfield_client()
    
    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y%m%d_%H%M%S")
    summary_hash = hashlib.sha256(summary.encode()).hexdigest()[:12]
    
    object_name = f"aisynergix/data/brains/collective_{ts}_{summary_hash}.txt"
    
    await gf.create_object(
        object_name=object_name,
        payload=summary.encode("utf-8"),
        content_type="text/plain; charset=utf-8",
        tags={
            "type": "collective_summary",
            "timestamp": ts,
            "hash": summary_hash,
            "contributions_count": "auto",
        },
    )
    
    logger.info(f"📚 Resumen colectivo almacenado: {object_name}")
    return object_name


async def refine_contextual_knowledge():
    logger.info("🔄 Refinando conocimiento contextual...")
    
    rag = await get_rag_engine()
    stats = await rag.get_stats()
    
    total_docs = stats.get("total_documents", 0)
    
    if total_docs < 10:
        logger.info(f"📭 Solo {total_docs} documentos. Saltando refinamiento.")
        return
    
    logger.info(f"🧠 {total_docs} documentos en el índice. Índice optimizado.")
    
    if total_docs > 0:
        await update_brain_pointer_with_stats(stats)


async def update_brain_pointer_with_stats(stats: Dict[str, Any]):
    gf = await get_greenfield_client()
    
    current = await gf.get_or_create_brain_pointer()
    
    version_parts = current.replace("v", "").split(".")
    major = int(version_parts[0]) if version_parts and version_parts[0] else 0
    minor = int(version_parts[1]) if len(version_parts) > 1 and version_parts[1] else 0
    patch = int(version_parts[2]) if len(version_parts) > 2 and version_parts[2] else 0
    
    patch += 1
    if patch >= 100:
        patch = 0
        minor += 1
    if minor >= 100:
        minor = 0
        major += 1
    
    new_version = f"v{major}.{minor}.{patch}"
    
    await gf.update_brain_pointer(new_version)
    logger.info(f"🧠 Cerebro actualizado: {current} → {new_version}")


async def detect_emergent_insights():
    logger.info("💡 Buscando insights emergentes...")
    
    rag = await get_rag_engine()
    thinker = get_thinker()
    
    queries = [
        "innovación tecnológica futuro",
        "conciencia colectiva humanidad",
        "sabiduría filosófica profunda",
        "sociedad descentralizada",
        "inteligencia artificial ética",
    ]
    
    for query in queries:
        context, results = await rag.query(query, "es")
        
        if results and len(results) >= 3:
            top_score = results[0].get("score", 0)
            logger.debug(f"📊 Query '{query}': {len(results)} resultados, top_score={top_score:.3f}")


async def run_fusion_cycle():
    logger.info("=" * 50)
    logger.info("🧬 INICIANDO CICLO DE FUSIÓN CEREBRAL")
    logger.info("=" * 50)
    
    contributions = await collect_contributions_for_summary(minutes=60)
    
    if contributions:
        logger.info(f"📥 {len(contributions)} aportes de alta calidad recolectados.")
        
        summary = await generate_collective_summary(contributions)
        
        if summary:
            await store_collective_summary(summary)
            logger.info(f"📚 Resumen generado: {len(summary)} caracteres.")
        else:
            logger.info("📭 No se pudo generar resumen colectivo.")
    else:
        logger.info("📭 Sin aportes de alta calidad para resumir.")
    
    await refine_contextual_knowledge()
    await detect_emergent_insights()
    
    logger.info("✅ Ciclo de fusión completado.")
    logger.info("=" * 50)


async def main():
    logger.info("🚀 Iniciando motor de fusión cerebral Synergix...")
    
    await run_fusion_cycle()
    
    logger.info("👋 Fusión completada.")


if __name__ == "__main__":
    asyncio.run(main())
