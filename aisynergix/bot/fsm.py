from aiogram.fsm.state import State, StatesGroup
from typing import Dict, Optional, Any
import time
import asyncio


class SynergixStates(StatesGroup):
    idle = State()
    awaiting_contribution = State()
    selecting_language = State()
    # Trading states
    awaiting_buy_amount = State()
    awaiting_sell_amount = State()
    # Wallet verification (2-step EIP-4361 flow)
    awaiting_wallet_address = State()
    awaiting_wallet_signature = State()
    # Community node creation
    awaiting_node_name = State()
    # Providers / crowdfunding (Fase 2)
    awaiting_provider_desc = State()
    awaiting_provider_payment = State()
    awaiting_project_name = State()
    awaiting_project_goal = State()
    awaiting_project_fund = State()
    # Bounties de conocimiento (Proof-of-Knowledge)
    awaiting_bounty_pool = State()
    # Synergix Academy (learn-to-earn)
    awaiting_lesson_answer = State()
    # Governance (Fase 3)
    awaiting_proposal_text = State()
    # Custodia on-chain: retiro de BNB / SYNERGIX
    awaiting_withdraw_address = State()
    awaiting_withdraw_amount = State()


class L1StateCache:
    def __init__(self, max_size: int = 500, ttl_seconds: int = 3600):
        self._storage: Dict[int, Dict[str, Any]] = {}
        self._max_size = max_size
        self._ttl = ttl_seconds
        self._lock = asyncio.Lock()

    async def get_state(self, uid: int) -> Optional[str]:
        async with self._lock:
            entry = self._storage.get(uid)
            if entry is None:
                return None

            if time.time() - entry.get("timestamp", 0) > self._ttl:
                del self._storage[uid]
                return None

            return entry.get("state", "idle")

    async def set_state(self, uid: int, state: str) -> None:
        async with self._lock:
            self._storage[uid] = {
                "state": state,
                "timestamp": time.time(),
            }

            if len(self._storage) > self._max_size:
                sorted_items = sorted(
                    self._storage.items(),
                    key=lambda x: x[1].get("timestamp", 0),
                )
                to_remove = len(self._storage) - self._max_size
                for key, _ in sorted_items[:to_remove]:
                    del self._storage[key]

    async def delete_state(self, uid: int) -> None:
        async with self._lock:
            self._storage.pop(uid, None)

    async def get_state_data(self, uid: int) -> Dict[str, Any]:
        async with self._lock:
            entry = self._storage.get(uid)
            if entry is None:
                return {}

            if time.time() - entry.get("timestamp", 0) > self._ttl:
                del self._storage[uid]
                return {}

            return entry.get("data", {})

    async def set_state_data(self, uid: int, data: Dict[str, Any]) -> None:
        async with self._lock:
            if uid in self._storage:
                self._storage[uid]["data"] = data
                self._storage[uid]["timestamp"] = time.time()

    async def clear(self) -> None:
        async with self._lock:
            self._storage.clear()


_l1_cache: Optional[L1StateCache] = None


def get_l1_cache() -> L1StateCache:
    global _l1_cache
    if _l1_cache is None:
        _l1_cache = L1StateCache()
    return _l1_cache


class GhostStateManager:
    def __init__(self):
        self._cache = get_l1_cache()

    async def get_state(self, uid: int) -> str:
        cached = await self._cache.get_state(uid)
        if cached is not None:
            return cached

        from aisynergix.bot.identity import get_identity_manager
        identity = get_identity_manager()
        profile = await identity.get_profile(uid)

        state = profile.fsm_state if profile.fsm_state else "idle"
        await self._cache.set_state(uid, state)
        return state

    async def set_state(self, uid: int, state: str) -> None:
        # Siempre actualizar el cache L1 en RAM
        await self._cache.set_state(uid, state)

        # Solo persistir a Irys si el usuario ya existe allí
        # (tiene puntos o aportes reales). Evita MsgCreateObject con
        # primary_sp_approval=None para usuarios nuevos.
        from aisynergix.bot.identity import get_identity_manager
        identity = get_identity_manager()
        profile = await identity.get_profile(uid)

        if profile.points > 0 or profile.contribution_count > 0:
            profile.fsm_state = state
            await identity.update_profile(uid, profile)
        else:
            # Usuario nuevo: solo actualizar en cache RAM
            profile.fsm_state = state
            identity._cache.set(uid, profile)

    async def reset_state(self, uid: int) -> None:
        await self.set_state(uid, "idle")

    async def enter_contribution_mode(self, uid: int) -> None:
        await self.set_state(uid, "awaiting_contribution")

    async def exit_contribution_mode(self, uid: int) -> None:
        await self.set_state(uid, "idle")

    async def enter_buy_mode(self, uid: int) -> None:
        await self.set_state(uid, "awaiting_buy_amount")

    async def enter_sell_mode(self, uid: int) -> None:
        await self.set_state(uid, "awaiting_sell_amount")

    async def enter_wallet_verify_mode(self, uid: int) -> None:
        await self.set_state(uid, "awaiting_wallet_address")

    async def enter_wallet_signature_mode(self, uid: int) -> None:
        await self.set_state(uid, "awaiting_wallet_signature")

    async def enter_node_name_mode(self, uid: int) -> None:
        await self.set_state(uid, "awaiting_node_name")


_ghost_state_manager: Optional[GhostStateManager] = None


def get_ghost_state_manager() -> GhostStateManager:
    global _ghost_state_manager
    if _ghost_state_manager is None:
        _ghost_state_manager = GhostStateManager()
    return _ghost_state_manager


__all__ = [
    "SynergixStates",
    "L1StateCache",
    "GhostStateManager",
    "get_ghost_state_manager",
    "get_l1_cache",
]
