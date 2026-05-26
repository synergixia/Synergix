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
from aisynergix.services.rag_engine import (
    get_rag_engine,
    BRAIN_CODES,
    CATEGORY_TO_BRAIN,
    pick_indexable_text,
)
from aisynergix.services.irys import (
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
    diagnose_payment_stream,
    cleanup_orphaned_created_objects,
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
                            # Brains index the Judge-distilled content_summary
                            # for new aportes; falls back to a truncated raw
                            # aporte for pre-PR2 entries that lack the tag.
                            "text": pick_indexable_text(tags, texto),
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
    logger.info("🎯 Generando reto semanal multilingüe...")

    try:
        thinker = get_thinker()

        # Generate in Spanish first (best prompt language for this model)
        challenge_prompt = (
            "Genera un reto semanal inspirador para la comunidad de Synergix "
            "(inteligencia colectiva descentralizada). "
            "El reto debe ser concreto y accionable (máx. 140 caracteres), "
            "relacionado con tecnología, blockchain, IA, filosofía o sociedad futura. "
            "Responde SOLO con el texto del reto. Sin comillas ni formato adicional."
        )
        challenge_text_es = await thinker.think(user_message=challenge_prompt, context="")
        challenge_text_es = challenge_text_es.strip().replace('"', '').replace("'", "")[:200]
        logger.info("🎯 Reto base [es]: %s", challenge_text_es)

        # Translate to all other supported languages sequentially
        # (thinker runs with --parallel 1, sequential avoids 503s)
        translations: Dict[str, str] = {"es": challenge_text_es}
        for lang_code, lang_name in LANG_NAMES.items():
            if lang_code == "es":
                continue
            try:
                tr = await thinker.think(
                    user_message=(
                        f"Translate the following text to {lang_name}. "
                        f"Keep it under 140 characters. "
                        f"Return only the translated text, no quotes, no explanation:\n"
                        f"{challenge_text_es}"
                    ),
                    context="",
                    target_language=lang_code,
                )
                translations[lang_code] = tr.strip()[:200]
                logger.info("🌐 [%s] %s", lang_code, translations[lang_code][:70])
            except Exception as exc:
                logger.warning("⚠️ Traducción [%s] falló (%s) — usando español", lang_code, exc)
                translations[lang_code] = challenge_text_es

        from datetime import datetime, timezone
        challenge_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
        challenge_data = {
            "id": challenge_id,
            "description": translations,          # dict {lang: text}
            "description_default": challenge_text_es,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "bonus_points": 5,
            "active": True,
        }
        await save_challenge(challenge_data)
        logger.info("✅ Reto multilingüe guardado en Irys [id=%s]", challenge_id)

        # Telegram notifications are sent by bot.py's background polling loop
        # which detects the new challenge ID and broadcasts to all known UIDs
        # in each user's language.  sync_brain.py cannot send Telegram messages
        # directly because it only stores uid_hashes, not real Telegram UIDs.

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

    # Diagnóstico temprano: verificar stream de pago antes de cualquier put_object
    await diagnose_payment_stream()

    # Cancelar objetos huérfanos (CREATED sin contenido en SP) usando
    # MsgCancelCreateObject — libera lock_balance y limpia el bucket.
    # MsgDeleteObject (usado por DCellar) solo funciona para objetos SEALED.
    await cleanup_orphaned_created_objects()

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

    # ── Fase 1: cargar cerebros desde Greenfield ────────────────────────
    brain_versions = await get_all_brain_pointers()
    loaded_codes: List[str] = []
    failed_codes: List[str] = []
    virgin_codes: List[str] = []   # codes con version=v0 (sin Greenfield aún)

    for code, version in brain_versions.items():
        if version == f"{code}_v0":
            virgin_codes.append(code)
            continue
        loaded = await rag.load_brain_from_greenfield(code, version)
        if loaded:
            loaded_codes.append(code)
            logger.info("🧠 Brain [%s] cargado desde Greenfield: %s", code, version)
        else:
            failed_codes.append(code)
            logger.warning(
                "⚠️ Brain [%s] no pudo cargarse desde Greenfield (%s).",
                code, version,
            )

    # ── Fase 2: detectar cerebros vacíos y determinar qué rebuildar ─────
    # Un cerebro cargado con total_documents==0 significa que su versión
    # en Greenfield fue guardada vacía (bootstrap inicial sin aportes).
    # En ese caso, tratarlo igual que failed → rebuild selectivo.
    empty_loaded: List[str] = [
        c for c in loaded_codes
        if rag._brains[c].total_documents == 0
    ]
    codes_to_rebuild = failed_codes + virgin_codes + empty_loaded

    if codes_to_rebuild:
        logger.info("🔄 Rebuild selectivo para: %s", codes_to_rebuild)
        added = await rag.rebuild_from_bucket(only_codes=codes_to_rebuild)
        logger.info("🧠 Rebuild completado: %s", added)
    else:
        logger.info("🧠 Todos los cerebros cargados desde Greenfield correctamente.")

    # ── Fase 3: persistir nueva versión solo para los codes que cambiaron
    # Casos donde guardamos nueva versión:
    # a) virgin_codes (instalación virgen) → siempre guardamos v1
    # b) failed_codes cuyo rebuild añadió aportes → guardamos v_new
    # c) empty_loaded cuyo rebuild añadió aportes → actualizamos a v_new
    # NO guardamos si pointer existe y el rebuild no encontró aportes
    # (evita orphans vacíos que se acumulan en cada restart).
    for code in codes_to_rebuild:
        engine = rag._brains.get(code)
        if engine is None:
            continue
        if engine.total_documents == 0 and code not in virgin_codes:
            # Pointer existe pero aún sin aportes para esta categoría
            logger.info("Brain [%s] sin aportes aún — no se sube versión.", code)
            continue
        new_version = await rag.save_brain_to_greenfield(
            code, brain_versions.get(code, f"{code}_v0")
        )
        if new_version:
            try:
                await update_brain_pointer_tag(code, new_version)
                logger.info(
                    "🧠 Brain [%s] → %s (%d docs)", code, new_version, engine.total_documents
                )
            except Exception as e:
                logger.error("❌ Pointer [%s] falló: %s", code, e)
        else:
            logger.error("❌ Save brain [%s] falló", code)

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

    thinker = get_thinker()
    judge = get_judge()
    await thinker.close()
    await judge.close()

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
