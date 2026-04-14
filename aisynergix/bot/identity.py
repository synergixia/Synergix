import time
import logging
from typing import Optional, Dict
from dataclasses import dataclass, field

from aisynergix.services.greenfield import GreenfieldClient

logger = logging.getLogger("Synergix.Identity")

@dataclass
class UserContext:
    uid: int
    puntos: int = 0
    rango: str = "🌱 Iniciado"
    cuota_diaria: int = 0
    fsm_state: str = "START"
    last_seen_ts: int = 0
    
    def can_post(self) -> bool:
        limits = {
            "🌱 Iniciado": 5,
            "📈 Activo": 12,
            "🧬 Sincronizado": 25,
            "🏗️ Arquitecto": 40,
            "🧠 Mente Colmena": 60,
            "🔮 Oráculo": float('inf')
        }
        return self.cuota_diaria < limits.get(self.rango, 5)

class IdentityHydrator:
    def __init__(self, greenfield: GreenfieldClient):
        self.greenfield = greenfield

    async def hydrate(self, uid: int) -> UserContext:
        """
        La ÚNICA fuente para resucitar al usuario es Greenfield.
        Si no existe, se crea uno nuevo (0 bytes + Tags base).
        """
        metadata = await self.greenfield.get_user_metadata(uid)
        
        if metadata is None:
            # Nuevo usuario en el Nodo
            logger.info(f"Registrando nuevo usuario Web3: {uid}")
            base_tags = {
                "puntos": "0",
                "rango": "🌱 Iniciado",
                "cuota_diaria": "0",
                "fsm_state": "MAIN_MENU",
                "last_seen_ts": str(int(time.time()))
            }
            # Crear archivo de 0 bytes en Greenfield
            await self.greenfield.put_object(f"aisynergix/usuarios/{uid}", b"", tags=base_tags)
            return UserContext(uid=uid, **{k: (int(v) if v.isdigit() else v) for k, v in base_tags.items()})

        # Rehidratar desde metadatos
        return UserContext(
            uid=uid,
            puntos=int(metadata.get("puntos", 0)),
            rango=metadata.get("rango", "🌱 Iniciado"),
            cuota_diaria=int(metadata.get("cuota_diaria", 0)),
            fsm_state=metadata.get("fsm_state", "MAIN_MENU"),
            last_seen_ts=int(metadata.get("last_seen_ts", 0))
        )

    async def update_state(self, uid: int, state: str):
        """Persistencia atómica del estado FSM en la Web3."""
        await self.greenfield.update_user_metadata(uid, {"fsm_state": state})

    def get_rango_by_puntos(self, puntos: int) -> str:
        if puntos >= 15000: return "🔮 Oráculo"
        if puntos >= 5000: return "🧠 Mente Colmena"
        if puntos >= 1500: return "🏗️ Arquitecto"
        if puntos >= 500: return "🧬 Sincronizado"
        if puntos >= 100: return "📈 Activo"
        return "🌱 Iniciado"
