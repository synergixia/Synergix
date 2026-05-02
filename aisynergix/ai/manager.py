import asyncio
import time
from typing import Optional, Dict, Any, List, Tuple
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
)
from aisynergix.services.greenfield import get_greenfield_client


CHALLENGE_BONUS_POINTS = 5
MIN_CONTRIBUTION_LENGTH = 20
ELITE_THRESHOLD = 9.0
LEGENDARY_THRESHOLD = 9.5


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
    ) -> Tuple[str, List[Dict[str, Any]]]:
        await self._ensure_rag()
        
        profile = await self._identity.get_profile(uid)
        target_language = profile.language
        
        history = await self._get_conversation_history(uid)
        
        context, search_results = await self._rag.query(message, target_language)
        
        response = await self._thinker.think(
            user_message=message,
            context=context,
            history=history,
        )
        
        await self._append_conversation_history(uid, "user", message)
        await self._append_conversation_history(uid, "assistant", response)
        
        if context and search_results:
            await self._process_residual_rewards(search_results)
        
        return response, search_results

    async def process_contribution(
        self,
        uid: int,
        content: str,
    ) -> Dict[str, Any]:
        await self._ensure_rag()
        
        profile = await self._identity.get_profile(uid)
        gf = await get_greenfield_client()
        
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
            challenge = await gf.get_current_challenge()
            points_gained += CHALLENGE_BONUS_POINTS
        else:
            challenge = None
        
        profile.add_points(points_gained)
        profile.increment_contribution()
        
        tags = {
            "quality_score": str(quality_score),
            "author_uid": profile.uid_hash,
            "lang": profile.language,
            "category": evaluation.get("category", "filosofia"),
            "impact_index": str(evaluation.get("impact_index", 0.5)),
        }
        
        object_name, cid, uid_hash_result = await gf.upload_contribution(
            uid=uid,
            content=content,
            tags=tags,
        )
        
        await self._rag.add_contribution(
            text=content,
            author_uid=profile.uid_hash,
            language=profile.language,
            quality_score=quality_score,
            object_name=object_name,
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
            "cid": cid,
            "quality_score": quality_score,
            "points_gained": points_gained,
            "new_total_points": profile.points,
            "tier": tier,
            "rank": profile.rank,
            "new_rank": new_rank,
            "challenge": challenge,
            "evaluation": evaluation,
        }

    async def get_user_status(self, uid: int) -> Dict[str, Any]:
        profile = await self._identity.get_profile(uid)
        gf = await get_greenfield_client()
        stats = await gf.get_user_stats(uid)
        
        rank_config = RANK_TABLE.get(profile.rank, RANK_TABLE["🌱 Iniciado"])
        sorted_ranks = sorted(RANK_TABLE.items(), key=lambda x: x[1]["min_points"])
        
        next_rank = None
        for rank_name, config in sorted_ranks:
            if config["min_points"] > profile.points:
                next_rank = {"name": rank_name, "points_needed": config["min_points"] - profile.points}
                break
        
        return {
            "uid_hash": profile.uid_hash,
            "rank": profile.rank,
            "points": profile.points,
            "daily_aportes_count": profile.daily_aportes_count,
            "daily_limit": profile.daily_limit,
            "remaining": profile.remaining_contributions,
            "total_uses_count": profile.total_uses_count,
            "contribution_count": stats["contribution_count"],
            "language": profile.language,
            "next_rank": next_rank,
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
        gf = await get_greenfield_client()
        
        rewarded_authors: set = set()
        
        for result in search_results:
            author_uid = result.get("metadata", {}).get("author_uid", "")
            
            if not author_uid or author_uid in rewarded_authors:
                continue
            
            if result.get("score", 0) < 0.4:
                continue
            
            try:
                new_points = await gf.add_residual_points(author_uid)
                rewarded_authors.add(author_uid)
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
