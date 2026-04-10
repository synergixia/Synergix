from aisynergix.bot.identity import UserContext

# En la arquitectura Stateless de Synergix, los estados no se guardan
# en MemoryStorage local. La modificación del atributo fsm_state 
# en UserContext garantiza que `dehydrate_user` lo guarde en Web3.

async def set_state(user_context: UserContext, state: str):
    user_context.fsm_state = state

async def get_state(user_context: UserContext) -> str:
    return user_context.fsm_state
