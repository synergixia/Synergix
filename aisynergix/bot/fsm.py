import asyncio
import time
from typing import Dict, Optional

from aisynergix.services.greenfield import greenfield
from aisynergix.bot.identity import profile_cache

FSM_STATES = {
    "idle": "idle",
    "awaiting_contribution": "awaiting_contribution",
}


class FSMManager:
    def __init__(self):
        self._local_state: Dict[str, str] = {}
        self._lock = asyncio.Lock()
        self._state_ttl: Dict[str, float] = {}

    async def get_state(self, uid: str) -> str:
        async with self._lock:
            now = time.time()
            ttl = self._state_ttl.get(uid, 0)
            if uid in self._local_state and (now - ttl) < 900:
                return self._local_state[uid]
        profile = await profile_cache.get(uid)
        state = profile.fsm_state
        async with self._lock:
            self._local_state[uid] = state
            self._state_ttl[uid] = time.time()
        return state

    async def set_state(self, uid: str, state: str) -> None:
        if state not in FSM_STATES and state not in FSM_STATES.values():
            state = FSM_STATES.get(state, "idle")
        async with self._lock:
            self._local_state[uid] = state
            self._state_ttl[uid] = time.time()
        await greenfield.set_user_tag(uid, "fsm_state", state)
        await profile_cache.invalidate(uid)

    async def enter_contribution_mode(self, uid: str) -> None:
        await self.set_state(uid, "awaiting_contribution")

    async def exit_contribution_mode(self, uid: str) -> None:
        await self.set_state(uid, "idle")

    async def is_awaiting_contribution(self, uid: str) -> bool:
        state = await self.get_state(uid)
        return state == "awaiting_contribution"

    async def clear(self, uid: str) -> None:
        async with self._lock:
            self._local_state.pop(uid, None)
            self._state_ttl.pop(uid, None)


fsm_manager = FSMManager()
