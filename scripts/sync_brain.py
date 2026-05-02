import os
import sys
import asyncio
import logging
import signal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from aisynergix.bot.locales import load_all_locales, t, LANG_NAMES
from aisynergix.services.rag_engine import get_rag_engine
from aisynergix.services.greenfield import get_greenfield_client
from aisynergix.ai.manager import get_ai_manager
from aisynergix.ai.local_ia import get_thinker, get_judge

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [SYNC] %(levelname)s: %(message)s",
)
logger = logging.getLogger("synergix.sync_brain")

scheduler = AsyncIOScheduler(timezone="UTC")
shutdown_event = asyncio.Event()


async def federated_evolution():
    logger.info("🧬 Iniciando evolución federada...")
    
    try:
        gf = await get_greenfield_client()
        rag = await get_rag_engine()
        
        recent = await gf.list_recent_contributions_from_bucket(minutes=10)
        
        if not recent:
            logger.info("📭 Sin nuevos aportes en los últimos 10 minutos.")
            return
        
        contributions = []
        for item in recent:
            try:
                text = await gf.get_contribution_text(item["object_name"])
                tags = item.get("tags", {})
                
                contributions.append({
                    "text": text,
                    "author_uid": tags.get("author_uid", "unknown"),
                    "language": tags.get("lang", "es"),
                    "quality_score": float(tags.get("quality_score", 0)),
                    "object_name": item["object_name"],
                })
            except Exception as e:
                logger.warning(f"Error leyendo {item.get('object_name')}: {e}")
                continue
        
        if contributions:
            ids = await rag.add_contributions_batch(contributions)
            logger.info(f"✅ {len(ids)} nuevos aportes inyectados al índice FAISS.")
        else:
            logger.info("📭 No se pudieron procesar aportes nuevos.")
        
        await update_leaderboard()
        await update_brain_version()
        
    except Exception as e:
        logger.error(f"❌ Error en evolución federada: {e}")


async def update_leaderboard():
    logger.info("📊 Actualizando leaderboard...")
    
    try:
        gf = await get_greenfield_client()
        
        users = await gf.list_all_users()
        
        leaderboard = []
        for uid_hash in users:
            try:
                object_name = f"aisynergix/users/{uid_hash}"
                tags = await gf.get_object_tags(object_name)
                
                if not tags:
                    continue
                
                points = int(tags.get("points", 0))
                rank = tags.get("rank", "🌱 Iniciado")
                language = tags.get("language", "es")
                total_uses = int(tags.get("total_uses_count", 0))
                
                leaderboard.append({
                    "uid_hash": uid_hash,
                    "points": points,
                    "rank": rank,
                    "language": language,
                    "total_uses_count": total_uses,
                })
            except Exception:
                continue
        
        leaderboard.sort(key=lambda x: x["points"], reverse=True)
        top10 = leaderboard[:10]
        
        await gf.update_top10_leaderboard(top10)
        logger.info(f"🏆 Top 10 actualizado: {len(top10)} mentes.")
        
        await hydrate_ranks(gf, leaderboard)
        
    except Exception as e:
        logger.error(f"❌ Error actualizando leaderboard: {e}")


async def hydrate_ranks(gf, leaderboard):
    from aisynergix.bot.identity import RANK_TABLE
    
    ranks_changed = 0
    
    for user in leaderboard:
        try:
            current_rank = user["rank"]
            points = user["points"]
            
            sorted_ranks = sorted(
                RANK_TABLE.items(),
                key=lambda x: x[1]["min_points"],
                reverse=True,
            )
            
            new_rank = None
            for rank_name, config in sorted_ranks:
                if points >= config["min_points"]:
                    new_rank = rank_name
                    break
            
            if new_rank and new_rank != current_rank:
                object_name = f"aisynergix/users/{user['uid_hash']}"
                await gf.update_object_tags(object_name, {"rank": new_rank})
                ranks_changed += 1
                
                lang = user.get("language", "es")
                old_rank = current_rank
                
                try:
                    from aisynergix.bot.bot import bot
                    
                    notification = t(
                        "rank_up",
                        lang,
                        old_rank=old_rank,
                        new_rank=new_rank,
                    )
                    
                    logger.info(f"🎉 {user['uid_hash']}: {old_rank} → {new_rank}")
                except Exception:
                    pass
        
        except Exception:
            continue
    
    if ranks_changed > 0:
        logger.info(f"🎉 {ranks_changed} ascensos de rango procesados.")


async def update_brain_version():
    try:
        gf = await get_greenfield_client()
        rag = await get_rag_engine()
        
        stats = await rag.get_stats()
        current = await gf.get_or_create_brain_pointer()
        
        version_parts = current.replace("v", "").split(".")
        major = int(version_parts[0]) if version_parts[0] else 0
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
        logger.info(f"🧠 Cerebro versionado: {new_version} (docs: {stats['total_documents']})")
        
    except Exception as e:
        logger.error(f"❌ Error versionando cerebro: {e}")


async def daily_cleanup():
    logger.info("🧹 Iniciando limpieza diaria...")
    
    try:
        gf = await get_greenfield_client()
        
        import gzip
        from datetime import datetime, timezone
        
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        
        log_content = "Synergix daily audit log\nTimestamp: " + datetime.now(timezone.utc).isoformat() + "\n"
        
        compressed = gzip.compress(log_content.encode("utf-8"))
        await gf.upload_log(today, compressed)
        logger.info("📦 Logs comprimidos y subidos.")
        
        reset_count = await gf.reset_daily_counts()
        logger.info(f"🔄 Daily counts reseteados: {reset_count} usuarios.")
        
    except Exception as e:
        logger.error(f"❌ Error en limpieza diaria: {e}")


async def weekly_challenge():
    logger.info("🎯 Generando reto semanal...")
    
    try:
        gf = await get_greenfield_client()
        thinker = get_thinker()
        
        challenge_prompt = """Genera un reto técnico semanal inspirador para la comunidad de Synergix (inteligencia colectiva descentralizada). 
El reto debe ser:
1. Un tema concreto y accionable (ej: "Mejor estrategia DeFi 2026", "El futuro de la gobernanza descentralizada", "Cómo la IA transformará la educación en 2030")
2. Motivador y que invite a la reflexión profunda
3. Relacionado con tecnología, blockchain, IA, filosofía digital o sociedad futura

Responde SOLO con el texto del reto, máximo 150 caracteres. Sin comillas ni formato adicional."""

        challenge_text = await thinker.think(
            user_message=challenge_prompt,
            context="",
        )
        
        challenge_text = challenge_text.strip().replace('"', '').replace("'", "")
        
        if len(challenge_text) > 200:
            challenge_text = challenge_text[:197] + "..."
        
        from datetime import datetime, timezone
        challenge_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
        
        challenge_data = {
            "id": challenge_id,
            "description": challenge_text,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "bonus_points": 5,
            "active": True,
        }
        
        await gf.create_challenge(challenge_id, challenge_data)
        logger.info(f"🎯 Reto creado: {challenge_text}")
        
        users = await gf.list_all_users()
        
        broadcast_count = 0
        for uid_hash in users:
            try:
                object_name = f"aisynergix/users/{uid_hash}"
                tags = await gf.get_object_tags(object_name)
                lang = tags.get("language", "es")
                
                notification = t("challenge_broadcast", lang, challenge_description=challenge_text)
                
                broadcast_count += 1
            except Exception:
                continue
        
        logger.info(f"📢 Broadcast enviado a {broadcast_count} usuarios.")
        
    except Exception as e:
        logger.error(f"❌ Error generando reto semanal: {e}")


async def health_monitor():
    try:
        ai = get_ai_manager()
        health = await ai.health_check()
        
        if not health["all_healthy"]:
            logger.warning(f"⚠️ Health Check: Thinker={health['thinker']}, Judge={health['judge']}")
        else:
            logger.debug("💚 Health Check: Todos los servicios OK")
    except Exception as e:
        logger.error(f"❌ Error en health monitor: {e}")


def setup_schedulers():
    scheduler.add_job(
        federated_evolution,
        IntervalTrigger(minutes=10),
        id="federated_evolution",
        name="Evolución federada cada 10 min",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    
    scheduler.add_job(
        update_leaderboard,
        IntervalTrigger(minutes=10),
        id="update_leaderboard",
        name="Actualización de leaderboard",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    
    scheduler.add_job(
        daily_cleanup,
        CronTrigger(hour=0, minute=0),
        id="daily_cleanup",
        name="Limpieza diaria 00:00 UTC",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    
    scheduler.add_job(
        weekly_challenge,
        CronTrigger(day_of_week="mon", hour=0, minute=5),
        id="weekly_challenge",
        name="Reto semanal Lunes 00:05 UTC",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    
    scheduler.add_job(
        health_monitor,
        IntervalTrigger(minutes=2),
        id="health_monitor",
        name="Health monitor cada 2 min",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )


async def on_startup():
    logger.info("🚀 Arrancando Nodo Fantasma Synergix...")
    
    await load_all_locales()
    logger.info(f"🌐 {len(LANG_NAMES)} idiomas cargados en RAM.")
    
    rag = await get_rag_engine()
    await rag.rebuild_from_bucket()
    logger.info("🧠 Motor RAG reconstruido desde el bucket.")
    
    ai = get_ai_manager()
    health = await ai.health_check()
    logger.info(f"🤖 IA: Thinker={health['thinker']}, Judge={health['judge']}")
    
    gf = await get_greenfield_client()
    brain_version = await gf.get_or_create_brain_pointer()
    logger.info(f"🔗 Conectado a Greenfield. Cerebro: {brain_version}")
    
    setup_schedulers()
    scheduler.start()
    logger.info("⏰ Cron jobs inicializados.")
    
    await federated_evolution()
    await update_leaderboard()
    
    logger.info("✅ Synergix Nodo Fantasma operativo.")


async def on_shutdown():
    logger.info("🛑 Apagando Nodo Fantasma...")
    
    shutdown_event.set()
    
    scheduler.shutdown(wait=False)
    logger.info("⏰ Scheduler detenido.")
    
    gf = await get_greenfield_client()
    await gf.close()
    
    thinker = get_thinker()
    judge = get_judge()
    await thinker.close()
    await judge.close()
    
    logger.info("👋 Synergix fuera de línea.")


def handle_signal(signum, frame):
    logger.info(f"Señal {signum} recibida. Iniciando apagado...")
    shutdown_event.set()


async def main():
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    
    await on_startup()
    
    try:
        await shutdown_event.wait()
    finally:
        await on_shutdown()


if __name__ == "__main__":
    asyncio.run(main())
