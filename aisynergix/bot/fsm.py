from aiogram.fsm.state import State, StatesGroup

class SynergixStates(StatesGroup):
    """
    Estados de Synergix. 
    Se persisten en Greenfield vía IdentityHydrator.update_state()
    """
    MAIN_MENU = State()
    CHAT_LIBRE = State()
    SINERGIZAR = State()
    RETOS = State()
    CONFIG = State()
