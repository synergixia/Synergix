import os
import sys
import asyncio
import logging
import signal
from pathlib import Path
from typing import Dict, List, Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from aisynergix.bot.locales import load_all_locales, t, LANG_NAMES
from aisynergix.services.rag_engine import get_rag_engine, BRAIN_CODES, CATEGORY_TO_BRAIN
from aisynergix.services.greenfield import (
    get_greenfield_client,
    get_all_user_uids,
    list_aportes,
    read_aporte,
    read_user_tags,
    write_user_tags,
    rebuild_top10,
    reset_all_daily_counts,
    upload_log,
    save_challenge,
    get_all_brain_pointers,
    update_brain_pointer_tag,
    load_ai_guard,
    load_system_config,
    check_emergency_lock,
    is_emergency_locked,
    load_current_challenge_from_greenfield,
)
from aisynergix.ai.manager import get_ai_manager
from aisynergix.ai.local_ia import get_thinker, get_judge

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [SYNC] %(levelname)s: %(message)s",
)
logger = logging.getLogger("synergix.sync_brain")


def _log_exc(msg: str, exc: Exception) -> None:
    from tenacity import RetryError
    if isinstance(exc, RetryError) and exc.last_attempt is not None:
        inner = exc.last_attempt.exception()
        if inner is not None:
            logger.error("%s (after retries): %s", msg, inner, exc_info=inner)
            return
    logger.exception(msg)

scheduler = AsyncIOScheduler(timezone="UTC")
shutdown_event = asyncio.Event()


async def federated_evolution():
    if is_emergency_locked():
        logger.warning("🔒 Emergency lock activo — saltando evolución federada")
        return
    logger.info("🧬 Iniciando evolución federada...")

    try:
        rag = await get_rag_engine()
        all_indexed = rag.get_all_indexed_objects()

        user_uids = await get_all_user_uids()
        logger.info(
            "🔍 Scan: %d usuarios en bucket, %d aportes ya indexados en RAM.",
            len(user_uids), len(all_indexed),
        )
        if not user_uids:
            logger.info("📭 Sin usuarios registrados.")
            return

        # Collect new aportes not yet indexed, grouped by brain code
        new_by_code: Dict[str, List[Dict[str, Any]]] = {c: [] for c in BRAIN_CODES}
        total_scanned = 0
        total_skipped_indexed = 0
        total_skipped_empty = 0
        total_read_errors = 0

        for uid in user_uids:
            try:
                aportes = await list_aportes(uid, limit=10)
                total_scanned += len(aportes)
                for aporte in aportes:
                    if aporte["path"] in all_indexed:
                        total_skipped_indexed += 1
                        continue
                    try:
                        texto, tags = await read_aporte(aporte["path"])
                        if not texto.strip():
                            total_skipped_empty += 1
                            continue
                        category = tags.get("category", "filosofia")
                        code = CATEGORY_TO_BRAIN.get(category, "know")
                        new_by_code[code].append({
                            "text": texto,
                            "author_uid": tags.get("author_uid", uid),
                            "language": tags.get("lang", "es"),
                            "quality_score": float(tags.get("quality_score", 0)),
                            "object_name": aporte["path"],
                        })
                    except Exception as e:
                        total_read_errors += 1
                        logger.warning("Error leyendo %s: %s", aporte.get("path"), e)
                        continue
            except Exception:
                continue

        logger.info(
            "📊 Scan resultado: %d aportes SEALED encontrados | "
            "%d ya indexados | %d vacíos | %d errores lectura | "
            "nuevos por cerebro: prog=%d tech=%d cien=%d know=%d",
            total_scanned, total_skipped_indexed, total_skipped_empty,
            total_read_errors,
            len(new_by_code["prog"]), len(new_by_code["tech"]),
            len(new_by_code["cien"]), len(new_by_code["know"]),
        )

        total_new = sum(len(v) for v in new_by_code.values())
        if total_new == 0:
            logger.info("📭 Sin nuevos aportes — saltando actualización de índices.")
            await update_leaderboard()
            return

        logger.info("📥 %d nuevos aportes detectados. Actualizando cerebros...", total_new)

        # Read current brain pointer versions before any upload
        current_pointers = await get_all_brain_pointers()

        for code, contribs in new_by_code.items():
            if not contribs:
                continue

            added = await rag.add_contributions_to_brain(code, contribs)
            logger.info("✅ Brain [%s]: +%d aportes inyectados en RAM.", code, added)

            # Upload new version to Greenfield — only update pointer on success
            new_version = await rag.save_brain_to_greenfield(code, current_pointers[code])
            if new_version:
                try:
                    await update_brain_pointer_tag(code, new_version)
                    logger.info("🧠 Brain [%s] → %s", code, new_version)
                except Exception as e:
                    logger.error("❌ No se pudo actualizar brain pointer [%s]: %s", code, e)
            else:
                logger.error(
                    "❌ Fallo al subir índice brain [%s] a Greenfield — "
                    "pointer NO actualizado.",
                    code,
                )

        await update_leaderboard()

    except Exception as e:
        _log_exc("❌ Error en evolución federada", e)


async def update_leaderboard():
    logger.info("📊 Actualizando leaderboard...")

    try:
        top10 = await rebuild_top10()
        logger.info("🏆 Top 10 actualizado: %d mentes.", len(top10))

        user_uids = await get_all_user_uids()
        leaderboard = []
        for uid_hash in user_uids:
            try:
                tags = await read_user_tags(uid_hash)
                if not tags:
                    continue
                leaderboard.append({
                    "uid_hash": uid_hash,
                    "points": int(tags.get("points", 0)),
                    "rank": tags.get("rank", "🌱 Iniciado"),
                    "language": tags.get("language", "es"),
                    "total_uses_count": int(tags.get("total_uses_count", 0)),
                })
            except Exception:
                continue

        await hydrate_ranks(leaderboard)

    except Exception as e:
        _log_exc("❌ Error actualizando leaderboard", e)


async def hydrate_ranks(leaderboard):
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
                current_tags = await read_user_tags(user["uid_hash"])
                current_tags["rank"] = new_rank
                await write_user_tags(user["uid_hash"], current_tags)
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
                    logger.info("🎉 %s: %s → %s", user["uid_hash"], old_rank, new_rank)
                except Exception:
                    pass

        except Exception:
            continue

    if ranks_changed > 0:
        logger.info("🎉 %d ascensos de rango procesados.", ranks_changed)


async def daily_cleanup():
    if is_emergency_locked():
        logger.warning("🔒 Emergency lock activo — saltando limpieza diaria")
        return
    logger.info("🧹 Iniciando limpieza diaria...")

    try:
        import gzip
        from datetime import datetime, timezone

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        log_content = (
            "Synergix daily audit log\nTimestamp: "
            + datetime.now(timezone.utc).isoformat()
            + "\n"
        )

        await upload_log(today, log_content)
        logger.info("📦 Logs subidos.")

        reset_count = await reset_all_daily_counts()
        logger.info("🔄 Daily counts reseteados: %d usuarios.", reset_count)

    except Exception as e:
        _log_exc("❌ Error en limpieza diaria", e)


async def weekly_challenge():
    if is_emergency_locked():
        logger.warning("🔒 Emergency lock activo — saltando reto semanal")
        return
    logger.info("🎯 Generando reto semanal...")

    try:
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

        await save_challenge(challenge_data)
        logger.info("🎯 Reto creado: %s", challenge_text)

        user_uids = await get_all_user_uids()
        broadcast_count = 0
        for uid_hash in user_uids:
            try:
                tags = await read_user_tags(uid_hash)
                lang = tags.get("language", "es")
                notification = t(
                    "challenge_broadcast", lang, challenge_description=challenge_text
                )
                broadcast_count += 1
            except Exception:
                continue

        logger.info("📢 Broadcast enviado a %d usuarios.", broadcast_count)

    except Exception as e:
        _log_exc("❌ Error generando reto semanal", e)


async def health_monitor():
    try:
        ai = get_ai_manager()
        health = await ai.health_check()

        if not health["all_healthy"]:
            logger.warning(
                "⚠️ Health Check: Thinker=%s, Judge=%s",
                health["thinker"],
                health["judge"],
            )
        else:
            logger.debug("💚 Health Check AI: OK")

        try:
            brain_versions = await get_all_brain_pointers()
            logger.debug(
                "💚 Health Check Greenfield: OK — brains=%s",
                {c: v for c, v in brain_versions.items()},
            )
        except Exception as gf_exc:
            logger.error(
                "🔴 Health Check Greenfield: FALLO — %s",
                gf_exc,
                exc_info=gf_exc,
            )

        await check_emergency_lock()

    except Exception as e:
        _log_exc("❌ Error en health monitor", e)


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
    logger.info("🌐 %d idiomas cargados en RAM.", len(LANG_NAMES))

    # sync_brain es el único proceso autorizado a CREAR archivos singleton
    # (system_config.json, ai_guard.txt) — evita race condition de nonce con bot
    await load_system_config(auto_create=True)
    logger.info("⚙️ System config cargado.")

    await load_ai_guard(auto_create=True)
    logger.info("🛡️ AI Guard inicializado.")

    locked = await check_emergency_lock()
    if locked:
        logger.warning("🔒 Emergency lock ACTIVO al inicio")

    rag = await get_rag_engine()

    # Try to load each brain from Greenfield (avoids full bucket rebuild)
    brain_versions = await get_all_brain_pointers()
    loaded_any = False
    for code, version in brain_versions.items():
        if version == f"{code}_v0":
            continue  # No real data yet
        loaded = await rag.load_brain_from_greenfield(code, version)
        if loaded:
            loaded_any = True
            logger.info("🧠 Brain [%s] cargado desde Greenfield: %s", code, version)
        else:
            logger.warning(
                "⚠️ Brain [%s] no pudo cargarse desde Greenfield (%s) — "
                "se usará caché local o rebuild.",
                code, version,
            )

    if not loaded_any:
        await rag.rebuild_from_bucket()
        logger.info("🧠 Motor RAG reconstruido desde el bucket.")

        # Solo creamos versiones nuevas en INSTALACIÓN VIRGEN (sin
        # brain_pointer existente).  Si el pointer ya existe pero las
        # cargas fallaron (v1 en CREATED state porque el SP nunca selló),
        # NO subimos v2 — sería otro orphan más.  En su lugar, dejamos
        # los cerebros reconstruidos en RAM y dejamos que
        # federated_evolution suba una nueva versión solo cuando haya
        # aportes nuevos que justifiquen el gas.
        pointer_exists = any(
            v != f"{c}_v0" for c, v in brain_versions.items()
        )
        if pointer_exists:
            logger.warning(
                "⚠️ brain_pointer existe (%s) pero los índices no se "
                "pudieron descargar.  Reconstruido en RAM — NO se subirá "
                "una nueva versión hasta que haya aportes nuevos.",
                {c: v for c, v in brain_versions.items()},
            )
        else:
            # Instalación virgen — bootstrap v1
            for code in BRAIN_CODES:
                engine = rag._brains.get(code)
                if engine is None:
                    continue
                await engine._ensure_index()
                new_version = await rag.save_brain_to_greenfield(
                    code, brain_versions.get(code, f"{code}_v0")
                )
                if new_version:
                    try:
                        await update_brain_pointer_tag(code, new_version)
                        logger.info(
                            "🧠 Bootstrap brain [%s] → %s (%d docs)",
                            code, new_version, engine.total_documents,
                        )
                    except Exception as e:
                        logger.error("❌ Bootstrap pointer [%s] falló: %s", code, e)
                else:
                    logger.error("❌ Bootstrap save brain [%s] falló", code)
    else:
        logger.info("🧠 Motor RAG inicializado desde Greenfield.")

    # Bootstrap del reto semanal: si no existe challenges/current.json en
    # Greenfield, lo generamos ahora.  El cron del lunes 00:05 UTC seguirá
    # rotándolo cada semana, pero así nunca arrancamos sin un reto activo.
    existing_challenge = await load_current_challenge_from_greenfield()
    if existing_challenge is None:
        logger.info("🎯 No hay reto activo — generando reto inicial...")
        try:
            await weekly_challenge()
        except Exception as ch_exc:
            logger.error("❌ Bootstrap challenge falló: %s", ch_exc)
    else:
        logger.info(
            "🎯 Reto activo en Greenfield: %s", existing_challenge.get("id")
        )

    ai = get_ai_manager()
    health = await ai.health_check()
    logger.info("🤖 IA: Thinker=%s, Judge=%s", health["thinker"], health["judge"])

    brain_versions = await get_all_brain_pointers()
    logger.info("🔗 Conectado a Greenfield. Cerebros: %s", brain_versions)

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

    from aisynergix.ai.local_ia import get_programmer
    thinker = get_thinker()
    judge = get_judge()
    programmer = get_programmer()
    await thinker.close()
    await judge.close()
    await programmer.close()

    logger.info("👋 Synergix fuera de línea.")


def handle_signal(signum, frame):
    logger.info("Señal %s recibida. Iniciando apagado...", signum)
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
