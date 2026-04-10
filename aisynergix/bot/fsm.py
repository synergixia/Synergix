from aisynergix.bot.identity import UserContext

async def set_state(user_context: UserContext, state: str):
    user_context.fsm_state = state

async def get_state(user_context: UserContext) -> str:
    return user_context.fsm_state
