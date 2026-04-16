"""
fsm.py — Máquina de Estados Web3-Only para Synergix.
Gestiona el flujo interactivo basado en Aiogram.
"""

from aiogram.fsm.state import State, StatesGroup

class SynergixStates(StatesGroup):
    """Estados de la Mente Colmena Synergix."""
    IDLE = State()               # Estado base y procesamiento general RAG
    AWAITING_APORTE = State()    # Esperando conocimiento técnico del usuario
    AWAITING_SEARCH = State()    # Búsqueda manual específica
    WEB3_SIGNING = State()       # Proceso de firma en curso (para futuras integraciones on-chain)
