import asyncio
import hashlib
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime, timezone

from aisynergix.ai.local_ia import (
    get_thinker,
    get_judge,
    get_duplicate_detector,
)
from aisynergix.services.rag_engine import get_rag_engine
from aisynergix.bot.identity import (
    get_identity_manager,
    UserProfile,
    RANK_TABLE,
    _hash_uid,
)
from aisynergix.services.greenfield import (
    write_aporte,
    read_user_tags,
    write_user_tags,
    get_current_challenge,
)

logger = logging.getLogger(__name__)

CHALLENGE_BONUS_POINTS = 5
MIN_CONTRIBUTION_LENGTH = 20
ELITE_THRESHOLD = 9.0
LEGENDARY_THRESHOLD = 9.5

# langdetect code → our system language code
_LANGDETECT_MAP: Dict[str, str] = {
    "es": "es", "en": "en",
    "zh-cn": "zh", "zh-tw": "zh", "zh": "zh",
    "hi": "hi", "ar": "ar", "fr": "fr",
    "bn": "bn", "pt": "pt", "id": "id", "ur": "ur",
}

_STICKER_RE = re.compile(r'\s*\[\[STICKER:([^\]]+)\]\]\s*')


def _extract_sticker(response: str) -> Tuple[str, Optional[str]]:
    """Remove [[STICKER:emoji]] token from response. Returns (clean_text, emoji_or_None)."""
    match = _STICKER_RE.search(response)
    if match:
        emoji = match.group(1).strip()
        clean = _STICKER_RE.sub(" ", response).strip()
        return clean, emoji
    return response, None


def _detect_lang(text: str) -> Optional[str]:
    """Return our language code for the dominant language in text, or None on failure."""
    if len(text) < 20:
        return None
    try:
        from langdetect import detect, LangDetectException
        code = detect(text)
        return _LANGDETECT_MAP.get(code)
    except Exception:
        return None


class AIManager:
    def __init__(self):
        self._thinker = get_thinker()
        self._judge = get_judge()
        self._duplicate_detector = get_duplicate_detector()
        self._rag = None
        self._identity = get_identity_manager()
        self._context_cache: Dict[int, List[Dict[str, str]]] = {}
        self._context_cache_lock = asyncio.Lock()

    async def _ensure_rag(self):
        if self._rag is None:
            self._rag = await get_rag_engine()

    async def process_conversation(
        self,
        uid: int,
        message: str,
    ) -> Tuple[str, Optional[str], List[Dict[str, Any]]]:
        """
        Returns (response_text, sticker_emoji_or_None, search_results).
        The sticker_emoji is extracted from [[STICKER:X]] tokens in the LLM output.
        """
        await self._ensure_rag()

        profile = await self._identity.get_profile(uid)
        target_language = profile.language

        history = await self._get_conversation_history(uid)
        context, search_results = await self._rag.query(message, target_language)

        response = await self._thinker.think(
            user_message=message,
            context=context,
            history=history,
            target_language=target_language,
        )

        # Language post-check: if the model responded in the wrong language, retry once.
        if len(response) >= 30:
            detected = _detect_lang(response)
            if detected and detected != target_language:
                logger.info(
                    "Language mismatch uid=%s expected=%s detected=%s — retrying",
                    uid, target_language, detected,
                )
                response = await self._thinker.think(
                    user_message=message,
                    context=context,
                    history=history,
                    target_language=target_language,
                    force_language=True,
                )

        # Extract optional sticker token
        clean_response, sticker_emoji = _extract_sticker(response)

        await self._append_conversation_history(uid, "user", message)
        await self._append_conversation_history(uid, "assistant", clean_response)

        if context and search_results:
            await self._process_residual_rewards(search_results)

        return clean_response, sticker_emoji, search_results

    async def process_contribution(
        self,
        uid: int,
        content: str,
    ) -> Dict[str, Any]:
        await self._ensure_rag()

        profile = await self._identity.get_profile(uid)

        if len(content.strip()) < MIN_CONTRIBUTION_LENGTH:
            return {
                "status": "too_short",
                "message_key": "contribution_too_short",
                "user_language": profile.language,
            }

        if not profile.can_contribute:
            return {
                "status": "quota_exceeded",
                "message_key": "quota_exceeded",
                "user_language": profile.language,
                "daily_limit": profile.daily_limit,
            }

        if self._duplicate_detector.check_and_add(content):
            return {
                "status": "duplicate",
                "message_key": "contribution_duplicate",
                "user_language": profile.language,
            }

        evaluation = await self._judge.evaluate(content)

        if not evaluation["approved"]:
            return {
                "status": "rejected",
                "message_key": "contribution_rejected",
                "user_language": profile.language,
                "evaluation": evaluation,
            }

        quality_score = evaluation["quality_score"]
        is_challenge_related = evaluation["related_to_challenge"]

        base_points = int(quality_score * 2)

        if quality_score >= LEGENDARY_THRESHOLD:
            points_gained = base_points + 15
            tier = "legendary"
        elif quality_score >= ELITE_THRESHOLD:
            points_gained = base_points + 8
            tier = "elite"
        else:
            points_gained = base_points
            tier = "normal"

        if is_challenge_related:
            challenge = await get_current_challenge()
            points_gained += CHALLENGE_BONUS_POINTS
        else:
            challenge = None

        profile.add_points(points_gained)
        profile.increment_contribution()

        # Persist aporte to Greenfield: aisynergix/aportes/YYYY-MM/{uid_hash}_{ts}.txt
        ts = int(datetime.now(timezone.utc).timestamp())
        aporte_tags = {
            "quality_score": str(quality_score),
            "author_uid": profile.uid_hash,
            "lang": profile.language,
            "category": evaluation.get("category", "filosofia"),
            "impact_index": str(evaluation.get("impact_index", 0.5)),
        }

        try:
            object_path = await write_aporte(
                uid_ofuscado=profile.uid_hash,
                texto=content,
                tags=aporte_tags,
                ts=ts,
            )
        except Exception as gf_err:
            # Greenfield write failed (SP approval unavailable, network issue, etc.).
            # Fall back to a local content-addressed ID so the contribution still
            # enters the RAG index and the user sees success.
            content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
            object_path = f"local:{profile.uid_hash}_{ts}_{content_hash}"
            logger.warning("Greenfield write failed, using local CID %s: %s", object_path, gf_err)

        await self._rag.add_contribution(
            text=content,
            author_uid=profile.uid_hash,
            language=profile.language,
            quality_score=quality_score,
            object_name=object_path,
        )

        new_rank = await self._identity.check_and_update_rank(uid, profile)
        await self._identity.update_profile(uid, profile)

        return {
            "status": "success",
            "message_key": (
                "contribution_success_challenge"
                if is_challenge_related
                else "contribution_success"
            ),
            "user_language": profile.language,
            "cid": object_path,
            "quality_score": quality_score,
            "points_gained": points_gained,
            "new_total_points": profile.points,
            "tier": tier,
            "rank": profile.rank,
            "new_rank": new_rank,
            "challenge": challenge,
            "evaluation": evaluation,
        }

    async def get_user_status(self, uid: int, name: str = "") -> Dict[str, Any]:
        profile = await self._identity.get_profile(uid)

        sorted_ranks = sorted(RANK_TABLE.items(), key=lambda x: x[1]["min_points"])

        next_rank = None
        points_next: Any = "MAX"
        for rank_name, config in sorted_ranks:
            if config["min_points"] > profile.points:
                next_rank = {
                    "name": rank_name,
                    "points_needed": config["min_points"] - profile.points,
                }
                points_next = config["min_points"] - profile.points
                break

        rank_config = RANK_TABLE.get(profile.rank, RANK_TABLE["🌱 Iniciado"])
        multiplier = rank_config.get("multiplier", 1.0)
        beneficio = rank_config.get("beneficio", "—")

        total_aportes: Any = 0
        try:
            await self._ensure_rag()
            rag_stats = await self._rag.get_stats()
            total_aportes = rag_stats.get("total_documents", 0)
        except Exception:
            pass

        tema_actual = "—"
        try:
            challenge = await get_current_challenge()
            if challenge and challenge.get("active"):
                desc = challenge.get("description", "")
                tema_actual = desc[:60] + ("…" if len(desc) > 60 else "")
        except Exception:
            pass

        daily_limit_raw = profile.daily_limit
        daily_limit_display: Any = "∞" if daily_limit_raw >= 9999 else daily_limit_raw

        return {
            "name": name,
            "uid_hash": profile.uid_hash,
            "rank": profile.rank,
            "points": profile.points,
            "daily_aportes_count": profile.daily_aportes_count,
            "daily_limit": daily_limit_display,
            "remaining": profile.remaining_contributions,
            "total_uses_count": profile.total_uses_count,
            "contribution_count": profile.contribution_count,
            "contribuciones": profile.contribution_count,
            "language": profile.language,
            "next_rank": next_rank,
            "points_next": points_next,
            "beneficio": beneficio,
            "multiplier": multiplier,
            "total_aportes": total_aportes,
            "tema_actual": tema_actual,
        }

    async def set_language(self, uid: int, lang_code: str) -> Tuple[bool, str]:
        success = await self._identity.set_language(uid, lang_code)
        if success:
            return True, lang_code
        return False, "es"

    async def get_language(self, uid: int) -> str:
        return await self._identity.get_language(uid)

    async def _get_conversation_history(self, uid: int) -> List[Dict[str, str]]:
        async with self._context_cache_lock:
            return self._context_cache.get(uid, [])

    async def _append_conversation_history(
        self,
        uid: int,
        role: str,
        content: str,
    ) -> None:
        async with self._context_cache_lock:
            if uid not in self._context_cache:
                self._context_cache[uid] = []

            self._context_cache[uid].append({"role": role, "content": content})

            if len(self._context_cache[uid]) > 14:
                self._context_cache[uid] = self._context_cache[uid][-14:]

    async def clear_conversation_history(self, uid: int) -> None:
        async with self._context_cache_lock:
            self._context_cache.pop(uid, None)

    async def _process_residual_rewards(
        self,
        search_results: List[Dict[str, Any]],
    ) -> None:
        rewarded_authors: set = set()

        for result in search_results:
            author_uid_hash = result.get("metadata", {}).get("author_uid", "")

            if not author_uid_hash or author_uid_hash in rewarded_authors:
                continue

            if result.get("score", 0) < 0.4:
                continue

            try:
                tags = await read_user_tags(author_uid_hash)
                if tags:
                    tags["points"] = str(int(tags.get("points", 0)) + 1)
                    tags["total_uses_count"] = str(int(tags.get("total_uses_count", 0)) + 1)
                    await write_user_tags(author_uid_hash, tags)
                    rewarded_authors.add(author_uid_hash)
            except Exception:
                continue

    async def health_check(self) -> Dict[str, bool]:
        thinker_ok = await self._thinker.health()
        judge_ok = await self._judge.health()

        return {
            "thinker": thinker_ok,
            "judge": judge_ok,
            "all_healthy": thinker_ok and judge_ok,
        }


_ai_manager: Optional[AIManager] = None


def get_ai_manager() -> AIManager:
    global _ai_manager
    if _ai_manager is None:
        _ai_manager = AIManager()
    return _ai_manager


__all__ = [
    "AIManager",
    "get_ai_manager",
    "ELITE_THRESHOLD",
    "LEGENDARY_THRESHOLD",
    "CHALLENGE_BONUS_POINTS",
]
