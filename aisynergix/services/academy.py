"""academy.py — Synergix Academy: aprende del conocimiento verificado y gana.

El producto learn-to-earn sobre el protocolo Proof-of-Knowledge:

  1. La IA genera una mini-lección + pregunta desde el conocimiento VERIFICADO
     de la red (RAG) sobre un tema, en el idioma del alumno.
  2. El alumno responde con sus palabras; el Juez IA califica (0-10).
  3. Al aprobar (≥ PASS_SCORE): gana SYNX, su progreso avanza y, al cruzar
     umbrales, se sella una MICRO-CREDENCIAL en Irys/Arweave ligada solo a su
     Ghost ID — verificable por cualquier empleador vía Passport/API sin
     revelar identidad.
  4. Los AUTORES cuyos aportes alimentaron la lección reciben impacto PIR
     (y sus regalías): el conocimiento que enseña paga a quien lo aportó.

Anti-farming: tope diario de lecciones y respuesta mínima; la recompensa es
pequeña y acotada (cap × reward por día como pérdida máxima).

La progresión (niveles, recompensa, tope diario) es pura y testeable sin red.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

PASS_SCORE = 5.0             # §5.1: mismo umbral que un aporte válido
LESSON_REWARD_SYNX = 2.0     # SYNX por lección aprobada
DAILY_LESSON_CAP = 5         # lecciones con recompensa por usuario y día
MIN_ANSWER_LEN = 20
MAX_LESSON_CITATIONS = 3     # autores acreditados por lección (PIR)

# Umbrales de nivel: lecciones aprobadas en un dominio → nivel.
LEVEL_THRESHOLDS: List[Tuple[int, int]] = [
    (5, 100), (4, 50), (3, 25), (2, 10), (1, 3),
]

# Contador diario en RAM: uid -> (fecha_iso, lecciones_hoy).
_daily: Dict[int, Tuple[str, int]] = {}


# ── Progresión pura (testeable) ──────────────────────────────────────────

def level_for(lessons_passed: int) -> int:
    """Nivel de credencial para N lecciones aprobadas (0 = sin nivel aún)."""
    for level, need in LEVEL_THRESHOLDS:
        if lessons_passed >= need:
            return level
    return 0


def next_level_at(lessons_passed: int) -> Optional[int]:
    """Lecciones necesarias para el siguiente nivel, o None si está al máximo."""
    pending = [need for _lvl, need in LEVEL_THRESHOLDS if need > lessons_passed]
    return min(pending) if pending else None


def is_passing(score: float) -> bool:
    try:
        s = float(score)
    except (ValueError, TypeError):
        return False
    return s == s and s >= PASS_SCORE  # NaN nunca aprueba


def lessons_today(uid: int, today: str) -> int:
    date, count = _daily.get(uid, ("", 0))
    return count if date == today else 0


def _note_lesson(uid: int, today: str) -> None:
    _daily[uid] = (today, lessons_today(uid, today) + 1)
    if len(_daily) > 10_000:
        _daily.pop(next(iter(_daily)), None)


def can_take_lesson(uid: int, today: Optional[str] = None) -> bool:
    today = today or datetime.now(timezone.utc).date().isoformat()
    return lessons_today(uid, today) < DAILY_LESSON_CAP


# ── Generación de lección (RAG + Thinker) ────────────────────────────────

async def generate_lesson(topic_key: str, topic_label: str, lang: str) -> Optional[Dict[str, Any]]:
    """Genera {lesson, citations} desde el conocimiento verificado del tema.

    ``citations`` = [(author_hash, aporte_tx), …] de los fragmentos usados.
    Devuelve None si la red aún no tiene conocimiento suficiente del tema.
    """
    from aisynergix.services.rag_engine import get_rag_engine
    from aisynergix.ai.local_ia import get_thinker

    rag = await get_rag_engine()
    context, results = await rag.query(topic_label, target_language=lang)
    if not context:
        return None

    citations: List[Tuple[str, str]] = []
    for r in (results or [])[:MAX_LESSON_CITATIONS]:
        meta = r.get("metadata", {})
        author = meta.get("author_uid", "")
        tx = meta.get("object_name", "")
        if author and tx and not tx.startswith("local:"):
            citations.append((author, tx))

    prompt = (
        f"Crea una mini-lección clara y breve (máximo 150 palabras) sobre "
        f"'{topic_label}' usando SOLO las ideas del contexto. Termina con "
        f"exactamente UNA pregunta abierta para comprobar que el alumno "
        f"entendió. No inventes datos fuera del contexto."
    )
    lesson = await get_thinker().think(
        user_message=prompt, context=context, history=[], target_language=lang,
    )
    lesson = (lesson or "").strip()
    if not lesson:
        return None
    return {"lesson": lesson, "citations": citations, "topic": topic_key}


# ── Calificación y cierre de la lección ──────────────────────────────────

async def _grade(answer: str) -> Tuple[float, str]:
    """Califica la respuesta con el Juez IA. Devuelve (score, feedback)."""
    from aisynergix.ai.local_ia import get_judge
    evaluation = await get_judge().evaluate(answer)
    score = float(evaluation.get("quality_score", 0) or 0)
    feedback = (evaluation.get("constructive_feedback") or "").strip()
    return score, feedback


async def submit_answer(
    uid: int, topic_key: str, answer: str,
    citations: Optional[List[Tuple[str, str]]] = None,
) -> Dict[str, Any]:
    """Cierra el ciclo de una lección: califica, recompensa y acredita.

    Devuelve {status, score, feedback, reward, lessons, level, leveled_up}.
    status: "too_short" | "daily_cap" | "fail" | "pass".
    """
    from aisynergix.bot.identity import get_identity_manager, _hash_uid
    from aisynergix.services.irys import (
        read_credential, write_credential, write_lesson_result,
    )

    answer = (answer or "").strip()
    if len(answer) < MIN_ANSWER_LEN:
        return {"status": "too_short"}
    today = datetime.now(timezone.utc).date().isoformat()
    if not can_take_lesson(uid, today):
        return {"status": "daily_cap", "cap": DAILY_LESSON_CAP}

    score, feedback = await _grade(answer)
    uid_hash = _hash_uid(uid)

    if not is_passing(score):
        _note_lesson(uid, today)  # los intentos también cuentan (anti-farming)
        try:
            await write_lesson_result(uid_hash, topic_key, score, passed=False)
        except Exception:
            pass
        return {"status": "fail", "score": round(score, 1), "feedback": feedback}

    _note_lesson(uid, today)
    identity = get_identity_manager()
    reward = LESSON_REWARD_SYNX
    credited = await identity.credit_synx(uid_hash, reward)
    if credited is None:
        reward = 0.0

    # Progresión de la credencial (versionada por usuario+dominio en Irys).
    lessons = 1
    old_level = 0
    try:
        cred = await read_credential(uid_hash, topic_key)
        if cred:
            lessons = int(cred.get("lessons-passed", 0) or 0) + 1
            old_level = int(cred.get("level", 0) or 0)
    except Exception:
        pass
    new_level = level_for(lessons)
    leveled_up = new_level > old_level
    try:
        await write_credential(uid_hash, topic_key, new_level, lessons)
        await write_lesson_result(uid_hash, topic_key, score, passed=True)
    except Exception as exc:
        logger.warning("academy: sellado de credencial falló para %s: %s", uid_hash, exc)

    # PIR: el conocimiento que enseñó genera impacto (y regalías) a sus
    # autores. Best-effort en segundo plano — no bloquea la respuesta.
    for author, tx in (citations or [])[:MAX_LESSON_CITATIONS]:
        if author and author != uid_hash and tx:
            try:
                from aisynergix.services.impact import get_impact_tracker
                asyncio.create_task(get_impact_tracker().record_useful(tx, author))
            except Exception:
                pass

    return {
        "status": "pass",
        "score": round(score, 1),
        "feedback": feedback,
        "reward": reward,
        "lessons": lessons,
        "level": new_level,
        "leveled_up": leveled_up,
        "next_at": next_level_at(lessons),
        "authors_credited": len(citations or []),
    }


__all__ = [
    "PASS_SCORE", "LESSON_REWARD_SYNX", "DAILY_LESSON_CAP", "MIN_ANSWER_LEN",
    "LEVEL_THRESHOLDS", "MAX_LESSON_CITATIONS",
    "level_for", "next_level_at", "is_passing",
    "lessons_today", "can_take_lesson",
    "generate_lesson", "submit_answer",
]
