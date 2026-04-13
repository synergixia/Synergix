import logging
from aisynergix.bot.identity import UserContext
from aisynergix.services.greenfield import greenfield

logger = logging.getLogger("FSM")

async def set_state(user: UserContext, state: str):
    """Actualiza la RAM y envía el cambio a la Web3 de forma no bloqueante."""
    user.fsm_state = state
    success = await greenfield.update_user_metadata(user.uid, {"fsm": state})
    if not success:
        logger.warning(f"Error Web3 FSM: No se pudo guardar el estado {state} para {user.uid}")

async def get_state(user: UserContext) -> str:
    return user.fsm_state
