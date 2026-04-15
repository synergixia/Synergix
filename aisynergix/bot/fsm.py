"""
fsm.py — Estados Web3-Only para Synergix.
Basado en Aiogram FSM.
"""

from aiogram.fsm.state import State, StatesGroup

class SynergixStates(StatesGroup):
    """Estados de la Mente Colmena Synergix."""
    IDLE = State()               # Estado base
    AWAITING_APORTE = State()    # Esperando conocimiento del usuario
    AWAITING_SEARCH = State()    # Búsqueda manual en el RAG
    WEB3_SIGNING = State()       # Proceso de firma Greenfield en curso
