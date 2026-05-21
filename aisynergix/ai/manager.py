import asyncio
import hashlib
import logging
import re
import time
from typing import Any, AsyncGenerator, Dict, List, Optional, Set, Tuple
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
from aisynergix.services.irys import (
    write_aporte,
    read_user_tags,
    write_user_tags,
    get_current_challenge,
    get_system_config,
    check_ai_guard,
    is_emergency_locked,
)

logger = logging.getLogger(__name__)

CHALLENGE_BONUS_POINTS = 5
MIN_CONTRIBUTION_LENGTH = 20
ELITE_THRESHOLD = 9.0
LEGENDARY_THRESHOLD = 9.5

# Cap concurrent thinker calls to match llama.cpp --parallel 1.
# Extras queue here in asyncio — no HTTP connection, no timeout risk.
_THINKER_SEM = asyncio.Semaphore(1)

# UIDs whose conversation request is currently being processed.
# Prevents the same user from stacking duplicate in-flight requests.
_in_flight: Set[int] = set()

# langdetect code → our system language code
_LANGDETECT_MAP: Dict[str, str] = {
    "es": "es", "en": "en",
    "zh-cn": "zh", "zh-tw": "zh", "zh": "zh",
    "hi": "hi", "ar": "ar", "fr": "fr",
    "bn": "bn", "pt": "pt", "id": "id", "ur": "ur",
}

_STICKER_RE = re.compile(r'\s*\[\[STICKER:([^\]]+)\]\]\s*')

# Strip leading "Name: " prefixes the model emits despite being told not to.
_NAME_PREFIX_RE = re.compile(r'^(?:Synergix|Or[áa]culo|Oracle|Asistente|Assistant)\s*:\s*', re.IGNORECASE)


def _strip_name_prefix(text: str) -> str:
    """Remove leading 'Name: ' that the model emits despite system-prompt prohibitions."""
    return _NAME_PREFIX_RE.sub('', text).lstrip()


def _extract_sticker(response: str) -> Tuple[str, Optional[str]]:
    """Remove [[STICKER:emoji]] token from response. Returns (clean_text, emoji_or_None)."""
    match = _STICKER_RE.search(response)
    if match:
        # Take only the first whitespace-delimited token so that if the model
        # writes [[STICKER:❤️ conexión]] we still return just "❤️".
        emoji = match.group(1).strip().split()[0]
        clean = _STICKER_RE.sub(" ", response).strip()
        return clean, emoji
    return response, None


# Customer-service filler patterns the model produces despite system-prompt prohibitions.
# _FILLER_START: greeting at the beginning of a response.
# _FILLER_END: closing offer-to-help anchored to the end of the response.
_FILLER_START = re.compile(
    r'^(?:'
    r'[¡]?\s*Hola[,!]?\s+'           # es
    r'|Hi[,!]?\s+'                    # en
    r'|Hey[,!]?\s+'                   # en
    r'|Hello[,!]?\s+'                 # en
    r'|Bonjour[,!]?\s+'               # fr
    r'|Ol[aá][,!]?\s+'                # pt
    r'|你好[,，！。]?\s*'              # zh
    r'|مرحبا[،,!]?\s+'                # ar
    r'|नमस्ते[,!]?\s+'                # hi
    r'|হ্যালো[,!]?\s+'               # bn
    r'|Halo[,!]?\s+'                  # id
    r'|ہیلو[,!]?\s+'                  # ur
    r')',
    re.IGNORECASE,
)

_FILLER_END = re.compile(
    r'(?:'
    # Spanish
    r'[¿]?[Ee]n qu[eé](?: m[aá]s)? puedo (?:ayudarte|asistirte|servirte)[^.!?]*[.!?]?'
    r'|[¿]?[Hh]ay algo m[aá]s(?: en lo que)? que pueda (?:ayudarte|asistirte)[^.!?]*[.!?]?'
    r'|[¿]?[Tt]ienes alguna (?:otra )?pregunta[^.!?]*[.!?]?'
    r'|[¿]?[Nn]ecesitas m[aá]s (?:informaci[oó]n|ayuda)[^.!?]*[.!?]?'
    r'|[¿]?[Pp]uedo ayudarte en algo m[aá]s[^.!?]*[.!?]?'
    # English
    r'|[Hh]ow (?:else )?(?:can|may) I (?:help|assist) you[^.!?]*[.!?]?'
    r'|[Ii]s there anything else[^.!?]*[.!?]?'
    r'|[Ff]eel free to ask[^.!?]*[.!?]?'
    r'|[Dd]o you have any (?:other )?questions[^.!?]*[.!?]?'
    # French
    r'|[Cc]omment puis-je (?:vous|te) aider[^.!?]*[.!?]?'
    r'|[Yy] a-t-il autre chose[^.!?]*[.!?]?'
    # Portuguese
    r'|[Cc]omo posso (?:te |lhe )?ajudar[^.!?]*[.!?]?'
    r'|[Hh][aá] algo mais[^.!?]*[.!?]?'
    # Indonesian
    r'|[Aa]da yang bisa saya bantu[^.!?]*[.!?]?'
    r'|[Bb]agaimana saya bisa membantu[^.!?]*[.!?]?'
    # Arabic
    r'|كيف يمكنني مساعدتك[^.!?؟]*[.!?؟]?'
    r'|هل هناك شيء آخر[^.!?؟]*[.!?؟]?'
    # Hindi
    r'|मैं (?:और )?कैसे (?:आपकी )?मदद कर सकता[^.!?]*[.!?]?'
    # Bengali
    r'|আমি কীভাবে সাহায্য করতে পারি[^.!?]*[.!?]?'
    # Urdu
    r'|میں آپ کی کیسے مدد کر سکتا[^.!?]*[.!?]?'
    r')\s*$',
    re.IGNORECASE,
)


def _strip_filler(text: str) -> str:
    """Strip customer-service filler patterns that the model adds despite prompt prohibitions."""
    # Strip greeting from start only when enough content would remain after removal.
    stripped = _FILLER_START.sub('', text)
    if len(stripped.strip()) > 15:
        text = stripped
    # Strip closing offer-to-help anchored to end of string.
    text = _FILLER_END.sub('', text).rstrip()
    return text.strip()


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
        Returns ("", None, []) when this uid already has an in-flight request.
        """
        # Per-user dedup: drop duplicate requests from the same user
        if uid in _in_flight:
            logger.info("uid=%s already in-flight — dropping duplicate request", uid)
            return "", None, []

        _in_flight.add(uid)
        try:
            # Parallelize: profile load and RAG warm-up are independent
            profile, _ = await asyncio.gather(
                self._identity.get_profile(uid),
                self._ensure_rag(),
            )
            target_language = profile.language

            # Thinker MUST always consult immortal memory (RAG) before responding
            history, (context, search_results) = await asyncio.gather(
                self._get_conversation_history(uid),
                self._rag.query(message, target_language),
            )

            # Semaphore matches --parallel 1; extras queue in asyncio (no timeout risk).
            async with _THINKER_SEM:
                response = await self._thinker.think(
                    user_message=message,
                    context=context,
                    history=history,
                    target_language=target_language,
                )

            # Language post-check OUTSIDE the semaphore so the slot is released
            # before the retry — avoids blocking other users for a double-think slot.
            if len(response) >= 30:
                detected = _detect_lang(response)
                if detected and detected != target_language:
                    logger.info(
                        "Language mismatch uid=%s expected=%s detected=%s — retrying",
                        uid, target_language, detected,
                    )
                    async with _THINKER_SEM:
                        response = await self._thinker.think(
                            user_message=message,
                            context=context,
                            history=history,
                            target_language=target_language,
                            force_language=True,
                        )

            clean_response, sticker_emoji = _extract_sticker(response)
            clean_response = _strip_name_prefix(clean_response)
            clean_response = _strip_filler(clean_response)

            await self._append_conversation_history(uid, "user", message)
            await self._append_conversation_history(uid, "assistant", clean_response)

            # Fire residual rewards in background — Irys writes must not block the response
            if context and search_results:
                asyncio.create_task(self._process_residual_rewards(search_results))

            return clean_response, sticker_emoji, search_results

        finally:
            _in_flight.discard(uid)

    async def stream_conversation(
        self,
        uid: int,
        message: str,
    ) -> AsyncGenerator[Tuple[str, str], None]:
        """
        Yield ``(kind, text)`` chunks as they arrive from the Thinker, where
        ``kind`` is ``"think"`` (live reasoning trace) or ``"answer"`` (the
        visible response).  Only ``answer`` chunks are persisted to the
        conversation history.
        Yields nothing if this uid already has an in-flight request.
        """
        if uid in _in_flight:
            logger.info("uid=%s already in-flight — dropping duplicate stream request", uid)
            return

        _in_flight.add(uid)
        try:
            profile, _ = await asyncio.gather(
                self._identity.get_profile(uid),
                self._ensure_rag(),
            )
            target_language = profile.language

            history, (context, search_results) = await asyncio.gather(
                self._get_conversation_history(uid),
                self._rag.query(message, target_language),
            )

            answer_buf = ""
            # Buffer the first answer tokens to strip any leading "Name: " prefix
            # before the user sees it.  15 chars is enough to contain "Synergix: ".
            _pfx_buf = ""
            _pfx_done = False
            _PFX_WINDOW = 15

            async with _THINKER_SEM:
                async for kind, text in self._thinker.stream_think(
                    user_message=message,
                    context=context,
                    history=history,
                    target_language=target_language,
                ):
                    if kind == "answer":
                        answer_buf += text
                        if not _pfx_done:
                            _pfx_buf += text
                            if len(_pfx_buf) >= _PFX_WINDOW:
                                _pfx_done = True
                                yield ("answer", _strip_name_prefix(_pfx_buf))
                            # else: keep buffering, don't yield yet
                        else:
                            yield ("answer", text)
                    else:
                        yield (kind, text)

            # Flush prefix buffer if the stream ended before reaching _PFX_WINDOW.
            if not _pfx_done and _pfx_buf:
                yield ("answer", _strip_name_prefix(_pfx_buf))

            # Only persist the visible answer.  Skip when the model never
            # produced one (thinking overflowed max_tokens).
            if answer_buf.strip():
                clean_response, _ = _extract_sticker(answer_buf)
                clean_response = _strip_name_prefix(clean_response)
                clean_response = _strip_filler(clean_response)
                await self._append_conversation_history(uid, "user", message)
                await self._append_conversation_history(uid, "assistant", clean_response)

            if context and search_results:
                asyncio.create_task(self._process_residual_rewards(search_results))

        except Exception as exc:
            logger.exception("stream_conversation uid=%s: %s", uid, exc)
            raise  # propagate so the bot can show error_generic to the user
        finally:
            _in_flight.discard(uid)

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

        cfg = get_system_config()
        elite_threshold = cfg.get("elite_threshold", ELITE_THRESHOLD)
        legendary_threshold = cfg.get("legendary_threshold", LEGENDARY_THRESHOLD)
        trust_decrement = cfg.get("trust_score_decrement", 0.2)
        trust_increment = cfg.get("trust_score_increment", 0.1)

        if self._duplicate_detector.check_and_add(content):
            profile.update_trust_score(-trust_decrement)
            await self._identity.update_profile(uid, profile)
            return {
                "status": "duplicate",
                "message_key": "contribution_duplicate",
                "user_language": profile.language,
            }

        evaluation = await self._judge.evaluate(content)

        if not evaluation["approved"]:
            profile.update_trust_score(-trust_decrement)
            await self._identity.update_profile(uid, profile)
            return {
                "status": "rejected",
                "message_key": "contribution_rejected",
                "user_language": profile.language,
                "evaluation": evaluation,
            }

        quality_score = evaluation["quality_score"]
        is_challenge_related = evaluation["related_to_challenge"]

        base_points = int(quality_score * 2)

        if quality_score >= legendary_threshold:
            points_gained = base_points + 15
            tier = "legendary"
        elif quality_score >= elite_threshold:
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
        profile.update_trust_score(trust_increment)

        # Persist aporte to Irys: aisynergix/aportes/YYYY-MM/{uid_hash}_{ts}.txt
        ts = int(datetime.now(timezone.utc).timestamp())
        aporte_tags = {
            "quality_score": str(quality_score),
            "author_uid": profile.uid_hash,
            "lang": profile.language,
            "category": evaluation.get("category", "filosofia"),
            "impact": str(evaluation.get("impact_index", 0.5)),
        }
        if profile.human_verified and profile.wallet_address:
            aporte_tags["signature"] = profile.wallet_address.lower()

        try:
            object_path = await write_aporte(
                uid_ofuscado=profile.uid_hash,
                texto=content,
                tags=aporte_tags,
                ts=ts,
            )
        except Exception as gf_err:
            # Greenfield write failed. Log at ERROR level with traceback so the
            # root cause is visible in logs (not hidden as WARNING).
            # Fall back to a local ID so the contribution still enters the RAG
            # index, but the caller can detect the failure via the "local:" prefix.
            content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
            object_path = f"local:{profile.uid_hash}_{ts}_{content_hash}"
            logger.error(
                "❌ Irys write_aporte falló para %s — usando local CID %s",
                profile.uid_hash, object_path, exc_info=True,
            )

        await self._rag.add_contribution(
            text=content,
            author_uid=profile.uid_hash,
            language=profile.language,
            quality_score=quality_score,
            object_name=object_path,
            category=evaluation.get("category", "filosofia"),
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
            "trust_score": profile.trust_score,
            "human_verified": profile.human_verified,
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

            if len(self._context_cache[uid]) > 10:
                self._context_cache[uid] = self._context_cache[uid][-10:]

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
    "_extract_sticker",
    "_strip_filler",
    "_strip_name_prefix",
]
