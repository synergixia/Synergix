import asyncio
from dataclasses import dataclass, field
from aisynergix.services.greenfield import get_user_metadata, update_user_metadata
from aisynergix.config.constants import RANK_TABLE

@dataclass
class UserContext:
    uid: str
    points: int = 0
    rank: str = "🌱 Iniciado"
    fsm_state: str = "IDLE"
    daily_quota: int = 5
    language: str = "es"
    impact_index: int = 0
    _original_state: dict = field(default_factory=dict)

    def get_rank_info(self) -> dict:
        """Calcula el rango actual, límite de mensajes y multiplicador según los 6 niveles."""
        current_rank = RANK_TABLE[0]
        next_rank = None
        
        for i, rank in enumerate(RANK_TABLE):
            if self.points >= rank["min_pts"]:
                current_rank = rank
                if i + 1 < len(RANK_TABLE):
                    next_rank = RANK_TABLE[i + 1]
            else:
                break
                
        return {
            "name": current_rank["name"],
            "limit": current_rank["limit"],
            "multiplier": current_rank["multiplier"],
            "benefit": current_rank["benefit"],
            "next_pts": next_rank["min_pts"] if next_rank else 0,
            "next_rank": next_rank["name"] if next_rank else None
        }

async def hydrate_user(uid: str) -> UserContext:
    """Extrae los Tags de la Web3 y los carga en RAM. Si es nuevo, lo inicializa."""
    metadata = await get_user_metadata(uid)
    
    if not metadata:
        initial = {
            "points": "0", "rank": "🌱 Iniciado", "fsm_state": "IDLE", 
            "daily_quota": "5", "language": "es", "impact_index": "0"
        }
        await update_user_metadata(uid, initial)
        metadata = initial
    
    return UserContext(
        uid=uid,
        points=int(metadata.get("points", 0)),
        rank=metadata.get("rank", "🌱 Iniciado"),
        fsm_state=metadata.get("fsm_state", "IDLE"),
        daily_quota=int(metadata.get("daily_quota", 5)),
        language=metadata.get("language", "es"),
        impact_index=int(metadata.get("impact_index", 0)),
        _original_state=metadata.copy()
    )

async def dehydrate_user(user_context: UserContext):
    """Detecta cambios en RAM durante la sesión y los sube a Greenfield al finalizar."""
    current = {
        "points": str(user_context.points),
        "rank": str(user_context.rank),
        "fsm_state": str(user_context.fsm_state),
        "daily_quota": str(user_context.daily_quota),
        "language": str(user_context.language),
        "impact_index": str(user_context.impact_index)
    }
    
    # Detección Atómica: Solo escribe en la blockchain si hubo un cambio real
    if any(current[k] != str(user_context._original_state.get(k)) for k in current):
        await update_user_metadata(user_context.uid, current)
