import re
import time
import json
import asyncio
from typing import Dict, Any, Optional, List, Tuple

from aisynergix.ai.local_ia import pensador, juez, CHALLENGE_CONTEXT_TEMPLATE
from aisynergix.services.rag_engine import rag_engine
from aisynergix.services.greenfield import greenfield
from aisynergix.bot.identity import UserProfile, hash_uid
                                                                                                     HISTORY_MAX_MESSAGES = 7
MIN_APORTE_LENGTH = 20
EMOJI_TONE_MAP = {
    "🔥": "enérgico y positivo",
    "🌟": "celebratorio y triunfante",
    "❤️": "cálido y cercano",
    "😢": "empático y comprensivo",
    "🚀": "motivador y ambicioso",
    "💡": "iluminador y reflexivo",                                                                      "🎉": "festivo y alegre",
    "🤔": "analítico y curioso",
    "💪": "alentador y fuerte",                                                                          "🙏": "agradecido y humilde",
}                                                                                                    
                                                                                                     def detect_emotional_tone(text: str) -> Optional[str]:
    if not text:                                                                                             return None
    detected: List[str] = []                                                                             for emoji, tone in EMOJI_TONE_MAP.items():
        if emoji in text:                                                                                        detected.append(tone)
    if detected:                                                                                             return detected[0]
    return None                                                                                      
                                                                                                     def is_sticker_or_emoji_only(text: str) -> bool:
    if not text or not text.strip():                                                                         return True
    emoji_pattern = re.compile(                                                                              "["
        "\U0001F600-\U0001F64F"                                                                              "\U0001F300-\U0001F5FF"
        "\U0001F680-\U0001F6FF"                                                                              "\U0001F1E0-\U0001F1FF"
        "\U00002702-\U000027B0"                                                                              "\U000024C2-\U0001F251"
        "]+",                                                                                                flags=re.UNICODE,
    )                                                                                                    cleaned = emoji_pattern.sub("", text).strip()
    return len(cleaned) < 3
                                                                                                     
class ConversationHistory:                                                                               def __init__(self, max_messages: int = HISTORY_MAX_MESSAGES):
        self._histories: Dict[str, List[Dict[str, str]]] = {}                                                self._lock = asyncio.Lock()
        self._max = max_messages

    async def add(self, uid: str, role: str, content: str) -> None:                                          async with self._lock:
            if uid not in self._histories:                                                                           self._histories[uid] = []
            self._histories[uid].append({"role": role, "content": content})                                      if len(self._histories[uid]) > self._max:
                self._histories[uid] = self._histories[uid][-self._max:]                             
    async def get(self, uid: str) -> List[Dict[str, str]]:                                                   async with self._lock:
            return list(self._histories.get(uid, []))                                                
    async def clear(self, uid: str) -> None:                                                                 async with self._lock:
            self._histories.pop(uid, None)                                                           

conversation_history = ConversationHistory()                                                         

class AIManager:
    def __init__(self):                                                                                      self._challenge_cache: Optional[Dict[str, Any]] = None
        self._challenge_ts: float = 0                                                                        self._challenge_ttl: float = 600
                                                                                                         async def _get_active_challenge(self) -> Optional[Dict[str, Any]]:
        now = time.time()                                                                                    if self._challenge_cache and (now - self._challenge_ts) < self._challenge_ttl:
            return self._challenge_cache                                                                     challenges = await greenfield.get_challenges()
        if challenges:                                                                                           challenges.sort(key=lambda x: x.get("created", 0), reverse=True)
            self._challenge_cache = challenges[0]                                                                self._challenge_ts = now
            return self._challenge_cache                                                                     return None
                                                                                                         async def _build_challenge_context(self) -> str:
        challenge = await self._get_active_challenge()                                                       if not challenge:
            return ""                                                                                        return CHALLENGE_CONTEXT_TEMPLATE.format(
            challenge_title=challenge.get("title", "Sin título"),                                                challenge_description=challenge.get("description", "Sin descripción"),
        )                                                                                            
    async def process_free_chat(                                                                             self, profile: UserProfile, message: str,
    ) -> Tuple[str, List[Dict[str, Any]]]:                                                                   tone = detect_emotional_tone(message)
        tone_instruction = ""                                                                                if tone:
            tone_instruction = f"\n\n[Nota interna: El usuario muestra un tono {tone}. Adapta tu respuesta para que sea {tone}.]"
        lang = profile.language                                                                              history = await conversation_history.get(profile.uid)
        context, used_aportes = await rag_engine.get_context_for_query(                                          message, top_k=5, lang_filter=lang
        )
        full_message = message                                                                               if tone_instruction:
            full_message = message + tone_instruction
        if context:
            response = await pensador.chat_with_context(                                                             user_message=full_message,
                context=context,
                history=history,
            )                                                                                                else:
            response = await pensador.chat_free(
                user_message=full_message,
                history=history,                                                                                 )
        await conversation_history.add(profile.uid, "user", message)
        await conversation_history.add(profile.uid, "assistant", response)
        return (response, used_aportes)                                                              
    async def evaluate_contribution(
        self, profile: UserProfile, aporte_text: str,
    ) -> Dict[str, Any]:                                                                                     challenge_context = await self._build_challenge_context()
        recent_aportes = await greenfield.get_user_aportes(profile.uid, limit=5)
        recent_texts: List[str] = []
        for ap in recent_aportes:                                                                                name = ap.get("name", ap.get("Name", ""))
            if name:
                try:
                    txt = await greenfield.get_object_text(name)                                                         recent_texts.append(txt[:300])
                except Exception:
                    pass
        evaluation = await juez.evaluate(                                                                        aporte_text=aporte_text,
            challenge_context=challenge_context,
            existing_aportes=recent_texts,
        )                                                                                                    return evaluation

    async def process_contribution(
        self, profile: UserProfile, aporte_text: str,                                                    ) -> Dict[str, Any]:
        if len(aporte_text.strip()) < MIN_APORTE_LENGTH:
            return {
                "accepted": False,                                                                                   "reason": "too_short",
                "quality_score": 0,
                "points_gained": 0,
            }                                                                                                can_contribute, limit_reason = profile.can_contribute()
        if not can_contribute:
            return {
                "accepted": False,
                "reason": limit_reason,
                "quality_score": 0,
                "points_gained": 0,
            }
        evaluation = await self.evaluate_contribution(profile, aporte_text)
        quality = evaluation.get("quality_score", 0)
        is_dup = evaluation.get("is_duplicate", False)
        related_challenge = evaluation.get("related_to_challenge", False)
        if is_dup:
            return {
                "accepted": False,
                "reason": "duplicate",
                "quality_score": quality,
                "points_gained": 0,
                "evaluation": evaluation,
            }
        if quality < 3.0:                                                                                        return {
                "accepted": False,
                "reason": "rejected",
                "quality_score": quality,                                                                            "points_gained": 0,
                "evaluation": evaluation,
            }
        ts = time.time()                                                                                     aporte_tags = {
            "quality_score": str(quality),
            "author_uid": hash_uid(profile.uid),
            "lang": profile.language,                                                                            "category": evaluation.get("category", "general"),
            "impact_index": str(evaluation.get("impact_index", 5)),
        }
        try:                                                                                                     result = await greenfield.create_aporte(
                uid=profile.uid,
                content=aporte_text,
                tags=aporte_tags,
                ts=ts,
            )
        except Exception as e:                                                                                   return {
                "accepted": False,
                "reason": "error",                                                                                   "quality_score": quality,
                "points_gained": 0,
                "error": str(e),
            }
        base_points = 5
        if quality >= 9.5:
            base_points += 15                                                                                elif quality >= 9.0:
            base_points += 10
        elif quality >= 7.5:
            base_points += 5                                                                                 elif quality >= 6.0:
            base_points += 2
        if related_challenge:
            base_points += 5                                                                                 await profile.add_points(base_points)
        await profile.increment_daily()
        await profile.increment_uses(1)                                                                      suffix = ""
        if quality >= 9.5:
            suffix = "legendary"
        elif quality >= 9.0:                                                                                     suffix = "elite"
        try:
            await rag_engine.add_texts(
                [aporte_text],                                                                                       [{
                    "ts": int(ts),
                    "lang": profile.language,
                    "author_uid": hash_uid(profile.uid),                                                                 "quality_score": str(quality),
                    "path": result.get("path", ""),
                }],                                                                                              )
        except Exception:
            pass
        return {
            "accepted": True,
            "reason": "success",
            "quality_score": quality,
            "points_gained": base_points,
            "total_points": profile.points,
            "rank": profile.rank,
            "related_to_challenge": related_challenge,
            "suffix": suffix,
            "evaluation": evaluation,
            "result": result,
        }

    async def award_residual_points(
        self, used_aportes: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        awards: List[Dict[str, Any]] = []
        seen: set = set()
        for ap in used_aportes:
            author = ap.get("metadata", {}).get("author_uid", "")
            if not author or author in seen:
                continue
            seen.add(author)
            try:
                new_points = await greenfield.add_residual_points(author, 1)
                awards.append({
                    "author_uid": author,
                    "points_added": 1,
                    "new_total": new_points,
                })
            except Exception:
                continue
        return awards


ai_manager = AIManager()
