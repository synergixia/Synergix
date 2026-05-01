import os                                                                                            import sys                                                                                           import json                                                                                          import time                                                                                          import hashlib                                                                                       import logging                                                                                       import asyncio                                                                                       from datetime import datetime, timezone                                                              from pathlib import Path                                                                             from typing import List, Dict, Any, Optional, Set                                                                                                                                                         sys.path.insert(0, str(Path(__file__).resolve().parent.parent))                                                                                                                                           from aisynergix.services.greenfield import greenfield                                                from aisynergix.services.rag_engine import rag_engine                                                from aisynergix.bot.identity import get_rank_for_points, RANK_HIERARCHY                              from aisynergix.bot.locales import get_locale, get_language_name, get_language_flag, LANG_LIST, DEFAULT_LANG                                                                                                                                                                                                   logger = logging.getLogger("synergix.fusion_brain")                                                                                                                                                       FUSION_INTERVAL_MINUTES = 10                                                                         TOP10_MAX = 10                                                                                                                                                                                                                                                                                                 async def collect_recent_aportes(minutes: int = FUSION_INTERVAL_MINUTES) -> List[Dict[str, Any]]:        aportes = await greenfield.list_recent_aportes(minutes=minutes)                                      enriched: List[Dict[str, Any]] = []                                                                  for obj in aportes:                                                                                      name = obj.get("name", obj.get("Name", obj.get("object_name", "")))
        if not name or not name.endswith(".txt"):                                                                continue
        try:                                                                                                     content = await greenfield.get_object_text(name)                                                     tags = await greenfield.get_aporte_tags(name)                                                    except Exception as e:
            logger.warning(f"Failed to read aporte {name}: {e}")                                                 continue
        if not content or len(content.strip()) < 20:                                                             continue                                                                                         enriched.append({                                                                                        "path": name,                                                                                        "content": content,                                                                                  "tags": tags,
            "ts": int(tags.get("ts", time.time())),
        })                                                                                               enriched.sort(key=lambda x: x.get("ts", 0))                                                          return enriched                                                                                                                                                                                                                                                                                            async def inject_new_aportes_into_index(aportes: List[Dict[str, Any]]) -> int:                           if not aportes:
        return 0                                                                                         texts: List[str] = []                                                                                metas: List[Dict[str, Any]] = []                                                                     for ap in aportes:                                                                                       tags = ap.get("tags", {})
        texts.append(ap["content"])                                                                          metas.append({                                                                                           "ts": ap.get("ts", int(time.time())),                                                                "lang": tags.get("lang", "unknown"),                                                                 "author_uid": tags.get("author_uid", ""),
            "quality_score": tags.get("quality_score", "0"),                                                     "path": ap.get("path", ""),
        })                                                                                               added = await rag_engine.add_texts(texts, metas)                                                     logger.info(f"Injected {added} new vectors into FAISS index (ntotal={rag_engine.index.ntotal if rag_engine.index else 0})")                                                                               return added
                                                                                                                                                                                                          async def save_brain_snapshot() -> Optional[str]:
    success = await rag_engine.save_to_greenfield()                                                      if not success:
        logger.warning("Failed to save brain snapshot to Greenfield")                                        return None
    version = hashlib.sha256(                                                                                f"{rag_engine.index.ntotal}_{time.time()}_{os.urandom(8).hex()}".encode()                        ).hexdigest()[:16]                                                                                   version_str = f"v{version}"
    await greenfield.set_brain_pointer(version_str)                                                      logger.info(f"Brain snapshot saved: version={version_str}, ntotal={rag_engine.index.ntotal if rag_engine.index else 0}")
    return version_str                                                                                                                                                                                                                                                                                         async def rebuild_leaderboard() -> List[Dict[str, str]]:                                                 users = await greenfield.get_all_users()
    ranked: List[Dict[str, Any]] = []                                                                    for u in users:
        tags = u.get("tags", {})
        points = int(tags.get("points", "0"))                                                                rank_name, rank_level, _ = get_rank_for_points(points)
        ranked.append({                                                                                          "uid": u.get("uid_ofuscado", ""),                                                                    "points": points,                                                                                    "rank": rank_name,                                                                                   "rank_level": rank_level,                                                                            "total_uses": int(tags.get("total_uses_count", "0")),                                                "language": tags.get("language", DEFAULT_LANG),                                                  })
    ranked.sort(key=lambda x: x.get("points", 0), reverse=True)                                          top10 = ranked[:TOP10_MAX]
    top10_serializable: List[Dict[str, str]] = []                                                        for entry in top10:
        top10_serializable.append({                                                                              "uid": str(entry["uid"]),                                                                            "points": str(entry["points"]),                                                                      "rank": str(entry["rank"]),
            "rank_level": str(entry["rank_level"]),
            "total_uses": str(entry["total_uses"]),
        })                                                                                               await greenfield.set_top10(top10_serializable)                                                       logger.info(f"Leaderboard rebuilt: {len(top10)} entries")                                            return top10_serializable                                                                                                                                                                                                                                                                                  async def check_rank_ups() -> List[Dict[str, Any]]:
    users = await greenfield.get_all_users()                                                             rank_ups: List[Dict[str, Any]] = []                                                                  for u in users:
        tags = u.get("tags", {})                                                                             uid_full = u.get("uid_ofuscado", "")
        if not uid_full:                                                                                         continue
        points = int(tags.get("points", "0"))                                                                current_rank = tags.get("rank", "🌱 Iniciado")
        new_rank, new_level, new_limit = get_rank_for_points(points)
        if new_rank != current_rank:
            tags["rank"] = new_rank
        elif new_level > 0:
            expected_rank = RANK_HIERARCHY[new_level - 1][0]                                                     if current_rank != expected_rank:
                tags["rank"] = expected_rank
                new_rank = expected_rank                                                                         else:
                continue                                                                                     else:
            continue
        try:
            await greenfield.update_object_tags(u.get("path", f"aisynergix/users/{uid_full}"), tags)
        except Exception as e:
            logger.warning(f"Failed to update rank for {uid_full}: {e}")
            continue                                                                                         rank_ups.append({                                                                                        "uid": uid_full,
            "old_rank": current_rank,                                                                            "new_rank": new_rank,
            "new_limit": new_limit,
            "points": points,
            "language": tags.get("language", DEFAULT_LANG),                                                  })
    logger.info(f"Rank ups detected: {len(rank_ups)}")                                                   return rank_ups


async def generate_collective_summaries(aportes: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    if not aportes:
        return []
    summaries: List[Dict[str, str]] = []
    categories: Dict[str, List[str]] = {}
    for ap in aportes:                                                                                       tags = ap.get("tags", {})
        category = tags.get("category", "general")
        if category not in categories:
            categories[category] = []                                                                        categories[category].append(ap["content"][:200])
    for category, contents in categories.items():
        combined = " | ".join(contents[:5])
        summaries.append({
            "category": category,
            "sample_count": str(len(contents)),                                                                  "combined_snippet": combined[:300],                                                                  "ts": str(int(time.time())),                                                                     })
    return summaries
                                                                                                     
async def fusion_cycle() -> Dict[str, Any]:                                                              start_ts = time.time()                                                                               logger.info("Fusion cycle started")                                                                  aportes = await collect_recent_aportes(minutes=FUSION_INTERVAL_MINUTES)                              logger.info(f"Collected {len(aportes)} recent aportes")                                              added = await inject_new_aportes_into_index(aportes)                                                 version = await save_brain_snapshot()
    top10 = await rebuild_leaderboard()
    rank_ups = await check_rank_ups()                                                                    summaries = await generate_collective_summaries(aportes)
    elapsed = time.time() - start_ts                                                                     result = {                                                                                               "success": True,                                                                                     "aportes_collected": len(aportes),
        "vectors_added": added,                                                                              "brain_version": version,
        "leaderboard_entries": len(top10),
        "rank_ups": len(rank_ups),
        "category_summaries": len(summaries),
        "elapsed_seconds": round(elapsed, 2),                                                                "timestamp": datetime.now(timezone.utc).isoformat(),                                             }
    logger.info(f"Fusion cycle complete: {json.dumps(result, ensure_ascii=False)}")                      return result                                                                                    

async def run_fusion_once() -> Dict[str, Any]:
    await rag_engine.initialize()
    return await fusion_cycle()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,                                                                                  format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    result = asyncio.run(run_fusion_once())
    print(json.dumps(result, ensure_ascii=False, indent=2))
