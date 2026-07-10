"""anti_gaming.py — "Juez 3": detección de patrones de gaming/Sybil (§5.4).

El Juez 1 (IA) evalúa la calidad y el Juez 2 (Oráculos) la revisan. El Juez 3
es un guardián automático que detecta *farmeo*: aportes generados en ráfaga o
plantillados para explotar las recompensas, más allá del detector de
duplicados exactos.

Señales (todas puras y testeables):
  * Ritmo (timing): dos aportes del mismo usuario separados por menos de
    ``MIN_HUMAN_SECONDS`` sugieren automatización.
  * Auto-plantilla: alta similitud (Jaccard de tokens) con los aportes
    recientes del propio usuario → mismo molde con retoques.
  * Relleno: baja diversidad léxica dentro del texto (muchas palabras
    repetidas) → spam.

Un aporte marcado no se rechaza necesariamente: la capa que lo consume decide
(típicamente anula las bonificaciones y baja el trust) para degradar la
economía del farmeo sin castigar de más a un humano rápido.
"""

import logging
import re
from collections import deque
from typing import Deque, Dict, Set, Tuple

logger = logging.getLogger(__name__)

# Umbrales.
MIN_HUMAN_SECONDS = 15.0   # aportes más rápidos que esto tras el anterior
BURST_SIM_THRESHOLD = 0.6  # ráfaga solo si además se parece al anterior
TEMPLATE_SIM_THRESHOLD = 0.85  # casi-duplicado de un aporte propio reciente
REPEAT_THRESHOLD = 0.6     # fracción de tokens repetidos dentro del texto
_MIN_TOKENS = 4            # textos muy cortos no se juzgan por similitud
_HISTORY = 5               # aportes recientes recordados por usuario

_WORD_RE = re.compile(r"\w+", re.UNICODE)


def token_set(text: str) -> Set[str]:
    """Conjunto de tokens normalizados (minúsculas)."""
    return {w for w in _WORD_RE.findall((text or "").lower())}


def jaccard(a: Set[str], b: Set[str]) -> float:
    """Similitud de Jaccard entre dos conjuntos de tokens ([0, 1])."""
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def repetition_ratio(text: str) -> float:
    """Fracción de tokens repetidos: 1 - (únicos / total). 0 si <2 tokens."""
    tokens = _WORD_RE.findall((text or "").lower())
    if len(tokens) < 2:
        return 0.0
    return 1.0 - (len(set(tokens)) / len(tokens))


def evaluate_signals(
    seconds_since_last: float, max_similarity: float, repeat_ratio: float
) -> Tuple[bool, str]:
    """Decide si las señales indican gaming. Devuelve (marcado, motivo).

    ``seconds_since_last`` < 0 significa "sin aporte previo" (no penaliza ritmo).
    """
    if max_similarity >= TEMPLATE_SIM_THRESHOLD:
        return True, "template"
    if repeat_ratio >= REPEAT_THRESHOLD:
        return True, "filler"
    if (
        0 <= seconds_since_last < MIN_HUMAN_SECONDS
        and max_similarity >= BURST_SIM_THRESHOLD
    ):
        return True, "burst"
    return False, "ok"


class AntiGamingJudge:
    """Estado en RAM por usuario: último timestamp y aportes recientes."""

    def __init__(self) -> None:
        self._last_ts: Dict[str, float] = {}
        self._recent: Dict[str, Deque[Set[str]]] = {}

    def check(self, uid_hash: str, text: str, now: float) -> Tuple[bool, str]:
        """Evalúa el aporte y registra su huella. (marcado, motivo)."""
        tokens = token_set(text)
        prev_ts = self._last_ts.get(uid_hash)
        seconds_since_last = (now - prev_ts) if prev_ts is not None else -1.0

        history = self._recent.get(uid_hash)
        if history and len(tokens) >= _MIN_TOKENS:
            max_sim = max((jaccard(tokens, h) for h in history), default=0.0)
        else:
            max_sim = 0.0

        flagged, reason = evaluate_signals(
            seconds_since_last, max_sim, repetition_ratio(text)
        )

        # Registrar huella (siempre, para construir historial).
        self._last_ts[uid_hash] = now
        if history is None:
            history = deque(maxlen=_HISTORY)
            self._recent[uid_hash] = history
        history.append(tokens)

        # Acotar memoria total.
        if len(self._last_ts) > 5000:
            self._last_ts.pop(next(iter(self._last_ts)), None)
            self._recent.pop(next(iter(self._recent)), None)

        if flagged:
            logger.info("🚫 Juez 3 marcó gaming (%s) para %s", reason, uid_hash)
        return flagged, reason


_judge3: AntiGamingJudge = AntiGamingJudge()


def get_anti_gaming_judge() -> AntiGamingJudge:
    return _judge3


__all__ = [
    "MIN_HUMAN_SECONDS",
    "TEMPLATE_SIM_THRESHOLD",
    "REPEAT_THRESHOLD",
    "BURST_SIM_THRESHOLD",
    "token_set",
    "jaccard",
    "repetition_ratio",
    "evaluate_signals",
    "AntiGamingJudge",
    "get_anti_gaming_judge",
]
