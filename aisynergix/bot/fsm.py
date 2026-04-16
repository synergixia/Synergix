"""
fsm.py — Máquina de estados finitos (FSM) Web3-Only para Synergix.
Basado en Aiogram FSM con estados optimizados para operaciones Greenfield.
"""

import logging
from aiogram.fsm.state import State, StatesGroup

logger = logging.getLogger(__name__)


class SynergixStates(StatesGroup):
    """
    Estados de la Mente Colmena Synergix.
    
    Todos los estados están vinculados a tags en Greenfield (fsm_state)
    para persistencia stateless y recuperación tras reinicios.
    """
    
    # ─────────────────────────────────────────────────────────────────────────────
    # ESTADOS PRINCIPALES
    # ─────────────────────────────────────────────────────────────────────────────
    
    IDLE = State()
    """Estado base - usuario inactivo o en conversación normal."""
    
    AWAITING_APORTE = State()
    """Esperando que el usuario envíe conocimiento técnico para inmortalizar."""
    
    AWAITING_SEARCH = State()
    """Esperando consulta para búsqueda manual en el RAG."""
    
    WEB3_SIGNING = State()
    """Proceso de firma Greenfield en curso (operaciones críticas)."""
    
    # ─────────────────────────────────────────────────────────────────────────────
    # ESTADOS DE CONFIGURACIÓN
    # ─────────────────────────────────────────────────────────────────────────────
    
    CONFIGURING_LANGUAGE = State()
    """Usuario seleccionando idioma preferido."""
    
    CONFIGURING_QUOTA = State()
    """Usuario configurando cuota diaria (rangos avanzados)."""
    
    # ─────────────────────────────────────────────────────────────────────────────
    # ESTADOS DE CHALLENGE (RETOS SEMANALES)
    # ─────────────────────────────────────────────────────────────────────────────
    
    SOLVING_CHALLENGE = State()
    """Usuario resolviendo un reto técnico semanal."""
    
    SUBMITTING_SOLUTION = State()
    """Usuario enviando solución a un reto."""
    
    # ─────────────────────────────────────────────────────────────────────────────
    # ESTADOS DE ADMINISTRACIÓN (RANGO ARQUITECTO+)
    # ─────────────────────────────────────────────────────────────────────────────
    
    ADMIN_REVIEW = State()
    """Revisión de aportes por administradores (calidad dudosa)."""
    
    ADMIN_CONFIG = State()
    """Configuración del sistema por administradores."""


# Mapa de estados a strings para almacenamiento en Greenfield
STATE_TO_STRING = {
    SynergixStates.IDLE: "IDLE",
    SynergixStates.AWAITING_APORTE: "AWAITING_APORTE",
    SynergixStates.AWAITING_SEARCH: "AWAITING_SEARCH",
    SynergixStates.WEB3_SIGNING: "WEB3_SIGNING",
    SynergixStates.CONFIGURING_LANGUAGE: "CONFIGURING_LANGUAGE",
    SynergixStates.CONFIGURING_QUOTA: "CONFIGURING_QUOTA",
    SynergixStates.SOLVING_CHALLENGE: "SOLVING_CHALLENGE",
    SynergixStates.SUBMITTING_SOLUTION: "SUBMITTING_SOLUTION",
    SynergixStates.ADMIN_REVIEW: "ADMIN_REVIEW",
    SynergixStates.ADMIN_CONFIG: "ADMIN_CONFIG",
}

STRING_TO_STATE = {v: k for k, v in STATE_TO_STRING.items()}


def state_to_string(state: State) -> str:
    """
    Convierte un estado FSM a string para almacenamiento en Greenfield.
    
    Args:
        state: Estado Aiogram
    
    Returns:
        str: Representación string del estado
    
    Raises:
        ValueError: Si el estado no está mapeado
    """
    if state in STATE_TO_STRING:
        return STATE_TO_STRING[state]
    else:
        logger.warning(f"Estado no mapeado: {state}. Usando IDLE como fallback.")
        return "IDLE"


def string_to_state(state_str: str) -> State:
    """
    Convierte un string a estado FSM desde Greenfield.
    
    Args:
        state_str: String del estado almacenado
    
    Returns:
        State: Estado Aiogram correspondiente
    
    Raises:
        ValueError: Si el string no está mapeado
    """
    if state_str in STRING_TO_STATE:
        return STRING_TO_STATE[state_str]
    else:
        logger.warning(f"String de estado no reconocido: {state_str}. Usando IDLE.")
        return SynergixStates.IDLE


async def update_user_fsm_state(uid_ofuscado: str, new_state: State, context):
    """
    Actualiza el estado FSM de un usuario en Greenfield y localmente.
    
    Args:
        uid_ofuscado: UID ofuscado del usuario
        new_state: Nuevo estado FSM
        context: UserContext del usuario
    """
    try:
        # Actualizar en contexto local
        context.fsm_state = state_to_string(new_state)
        
        # Sincronizar a Greenfield (lazy)
        from aisynergix.services.greenfield import update_user_metadata
        
        async def _sync_state():
            try:
                tags = context.to_tags()
                success = await update_user_metadata(uid_ofuscado, tags)
                if success:
                    logger.debug(f"Estado FSM actualizado para {uid_ofuscado[:8]}: "
                               f"{context.fsm_state}")
                else:
                    logger.warning(f"Fallo actualizando estado FSM para {uid_ofuscado[:8]}")
            except Exception as e:
                logger.error(f"Error sincronizando estado FSM: {e}")
        
        # Ejecutar en segundo plano
        import asyncio
        asyncio.create_task(_sync_state())
        
    except Exception as e:
        logger.error(f"Error actualizando estado FSM para {uid_ofuscado[:8]}: {e}")


async def restore_user_fsm_state(context) -> State:
    """
    Restaura el estado FSM de un usuario desde Greenfield.
    
    Args:
        context: UserContext del usuario
    
    Returns:
        State: Estado FSM restaurado
    """
    try:
        state_str = context.fsm_state
        state = string_to_state(state_str)
        
        logger.debug(f"Estado FSM restaurado para {context.uid_ofuscado[:8]}: {state_str}")
        return state
        
    except Exception as e:
        logger.error(f"Error restaurando estado FSM para {context.uid_ofuscado[:8]}: {e}")
        return SynergixStates.IDLE


def get_available_states() -> dict:
    """
    Obtiene todos los estados disponibles con descripciones.
    
    Returns:
        dict: Mapa de estados a descripciones
    """
    return {
        "IDLE": "Estado normal de conversación",
        "AWAITING_APORTE": "Esperando contribución de conocimiento",
        "AWAITING_SEARCH": "Esperando consulta de búsqueda",
        "WEB3_SIGNING": "Procesando firma Greenfield",
        "CONFIGURING_LANGUAGE": "Configurando idioma preferido",
        "CONFIGURING_QUOTA": "Configurando cuota diaria",
        "SOLVING_CHALLENGE": "Resolviendo reto técnico",
        "SUBMITTING_SOLUTION": "Enviando solución a reto",
        "ADMIN_REVIEW": "Revisando aportes como administrador",
        "ADMIN_CONFIG": "Configurando sistema"
    }
