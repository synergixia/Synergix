from dataclasses import dataclass
from aisynergix.services.greenfield import greenfield

@dataclass
class UserContext:
    uid: str
    points: int = 0
    rank: str = "🌱 Iniciado"
    fsm_state: str = "IDLE"
    daily_quota: int = 5
    language: str = "es"

async def hydrate_user(uid: str) -> UserContext:
    meta = await greenfield.get_user_metadata(uid)
    if not meta:
        # Si no existe, crear identidad fantasma en Greenfield
        initial_tags = {
            "points": 0, "rank": "🌱 Iniciado", 
            "fsm": "IDLE", "quota": 5, "lang": "es"
        }
        await greenfield.put_object(f"aisynergix/users/{uid}", b"", initial_tags)
        meta = initial_tags
    
    return UserContext(
        uid=uid,
        points=int(meta.get("points", 0)),
        rank=meta.get("rank", "🌱 Iniciado"),
        fsm_state=meta.get("fsm", "IDLE"),
        daily_quota=int(meta.get("quota", 5)),
        language=meta.get("lang", "es")
    )
