import logging
from typing import Dict, Any, List
# Nota: Stateless absoluto. Mantenemos el estado de FSM en memoria local (RAM de Docker).
# Cuando el bot reinicia, los FSM se limpian (esto es aceptable para FSM volátiles de Telegram). 
# El estado fsm_state del usuario en Greenfield se usa para recuperar resiliencia a largo plazo si fuera necesario.

logger = logging.getLogger(__name__)

class StatelessFSM:
    """
    Caché L1 en memoria (RAM) para el FSM y las ventanas temporales conversacionales.
    Si el contenedor Docker muere, esta caché se pierde. Esto es por diseño Stateless.
    """
    def __init__(self):
        # fsm_cache[uid_ofuscado] = "idle" | "awaiting_contribution" 
        self.fsm_cache: Dict[str, str] = {}
        
        # history_cache[uid_ofuscado] = List[Dict[str, str]] (Max 10 elementos)
        self.history_cache: Dict[str, List[Dict[str, str]]] = {}

    def get_state(self, uid_ofuscado: str) -> str:
        """Devuelve el estado FSM del generador."""
        return self.fsm_cache.get(uid_ofuscado, "idle")

    def set_state(self, uid_ofuscado: str, state: str):
        """Asigna el estado volátil al usuario."""
        self.fsm_cache[uid_ofuscado] = state

    def append_history(self, uid_ofuscado: str, role: str, content: str):
        """Mantiene los últimos turnos para que el pensador tenga contexto continuo."""
        if uid_ofuscado not in self.history_cache:
            self.history_cache[uid_ofuscado] = []
            
        self.history_cache[uid_ofuscado].append({"role": role, "content": content})
        
        # Limitar a máximo 10 mensajes (5 turnos de diálogo ida y vuelta)
        if len(self.history_cache[uid_ofuscado]) > 10:
            self.history_cache[uid_ofuscado] = self.history_cache[uid_ofuscado][-10:]

    def get_history(self, uid_ofuscado: str) -> List[Dict[str, str]]:
        """Extrae la ráfaga de contexto histórico in-memory."""
        return self.history_cache.get(uid_ofuscado, [])
        
fsm_cache = StatelessFSM()
