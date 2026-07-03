"""rewards.py — bonificaciones especiales de las recompensas SYNX (diseño §5.3).

Fórmula completa de un aporte aprobado (§5.1 + §5.3):

    SYNX = base(score) × mult_rango × mult_vacío × mult_racha × (1 + Σ bonus%)
           [+ 100 extra si score == 10.0]

  * mult_vacío   ∈ {1, 3, 5}  — vacío de conocimiento del nodo (knowledge_map)
  * mult_racha   ∈ {1, 2}     — 7+ días consecutivos contribuyendo
  * bonus%: +20 % primer aporte del día · +50 % tema sin aporte previo en el
    nodo · +30 % idioma poco representado en el corpus

Todo lo de este módulo es lógica pura (sin red) para que sea testeable; la
recolección de datos (aportes del nodo, stats de idioma) vive en el caller.
"""

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

# Techo defensivo para cualquier monto SYNX/BNB introducido por el usuario.
# Evita overflows y montos absurdos; muy por encima de cualquier saldo real.
MAX_AMOUNT = 1e15


def parse_amount(text: str) -> Optional[float]:
    """Parsea un monto del usuario de forma SEGURA.

    ``float()`` acepta 'nan', 'inf' y '1e400': 'nan' es especialmente
    peligroso porque rompe todas las comparaciones (``x < nan`` es False),
    saltándose los controles de saldo y corrompiendo el balance.  Esta
    función rechaza no-finitos, no-positivos y montos por encima de
    ``MAX_AMOUNT``.  Retorna el float validado o None.
    """
    if text is None:
        return None
    try:
        value = float(str(text).strip().replace(",", "."))
    except (ValueError, TypeError):
        return None
    if not math.isfinite(value) or value <= 0 or value > MAX_AMOUNT:
        return None
    return value


def is_valid_amount(value: float) -> bool:
    """True si ``value`` es un monto finito, positivo y bajo el techo."""
    try:
        return math.isfinite(value) and 0 < value <= MAX_AMOUNT
    except (TypeError, ValueError):
        return False

# §5.3 — parámetros de las bonificaciones.
FIRST_OF_DAY_PCT = 0.20        # primer aporte del día (en el nodo)
VIRGIN_TOPIC_PCT = 0.50        # tema sin ningún aporte previo (en el nodo)
UNDERREP_LANG_PCT = 0.30       # idioma poco representado
STREAK_DAYS_REQUIRED = 7       # días consecutivos para activar la racha
STREAK_MULTIPLIER = 2.0        # ×2 ese día
PERFECT_SCORE_EXTRA = 100.0    # +100 SYNX extra por un 10.0 perfecto

# Un idioma está "poco representado" si su cuota en el corpus es < 10 %,
# siempre que el corpus tenga tamaño mínimo (sin corpus no hay señal).
UNDERREP_SHARE = 0.10
UNDERREP_MIN_CORPUS = 20


@dataclass
class BonusBreakdown:
    """Desglose de bonificaciones de un aporte, para el cálculo y el mensaje."""
    gap_multiplier: float = 1.0
    streak_multiplier: float = 1.0
    first_of_day: bool = False
    virgin_topic: bool = False
    underrep_lang: bool = False
    perfect_score: bool = False

    @property
    def pct_bonus(self) -> float:
        return (
            (FIRST_OF_DAY_PCT if self.first_of_day else 0.0)
            + (VIRGIN_TOPIC_PCT if self.virgin_topic else 0.0)
            + (UNDERREP_LANG_PCT if self.underrep_lang else 0.0)
        )

    def active_keys(self) -> List[str]:
        """Claves de las bonificaciones activas (para las locales del bot)."""
        keys: List[str] = []
        if self.gap_multiplier > 1.0:
            keys.append("gap")
        if self.first_of_day:
            keys.append("first_day")
        if self.virgin_topic:
            keys.append("virgin_topic")
        if self.underrep_lang:
            keys.append("underrep_lang")
        if self.streak_multiplier > 1.0:
            keys.append("streak")
        if self.perfect_score:
            keys.append("perfect")
        return keys


def compute_synx(base: float, rank_multiplier: float, bonus: BonusBreakdown) -> float:
    """Aplica la fórmula completa §5.1 + §5.3. Redondeado a 2 decimales."""
    total = (
        base
        * rank_multiplier
        * bonus.gap_multiplier
        * bonus.streak_multiplier
        * (1.0 + bonus.pct_bonus)
    )
    if bonus.perfect_score:
        total += PERFECT_SCORE_EXTRA
    return round(total, 2)


def update_streak(
    last_date: Optional[str], streak: int, today: date
) -> Tuple[int, str]:
    """Avanza la racha de días consecutivos. Retorna (racha_nueva, fecha_hoy).

    * mismo día      → la racha no cambia (mínimo 1)
    * día siguiente  → racha + 1
    * hueco o inicio → racha = 1
    """
    today_s = today.isoformat()
    if last_date == today_s:
        return max(streak, 1), today_s
    if last_date == (today - timedelta(days=1)).isoformat():
        return max(streak, 0) + 1, today_s
    return 1, today_s


def streak_multiplier_for(streak: int) -> float:
    return STREAK_MULTIPLIER if streak >= STREAK_DAYS_REQUIRED else 1.0


def _aporte_day(tags: Dict[str, str]) -> Optional[date]:
    try:
        ts = int(tags.get("timestamp", "") or 0)
        if ts <= 0:
            return None
        return datetime.fromtimestamp(ts, tz=timezone.utc).date()
    except (ValueError, TypeError, OSError, OverflowError):
        return None


def is_first_of_day(aporte_tags: List[Dict[str, str]], today: date) -> bool:
    """True si ningún aporte de la lista es de hoy (UTC)."""
    return not any(_aporte_day(t) == today for t in aporte_tags)


def is_virgin_topic(aporte_tags: List[Dict[str, str]], topic: str) -> bool:
    """True si ningún aporte de la lista trata ya ese tema/categoría."""
    topic = (topic or "").strip().lower()
    if not topic:
        return False
    for tags in aporte_tags:
        existing = (tags.get("topic") or tags.get("category") or "").strip().lower()
        if existing == topic:
            return False
    return True


def is_underrepresented(language: str, lang_counts: Dict[str, int]) -> bool:
    """True si el idioma tiene < UNDERREP_SHARE del corpus (corpus mínimo 20)."""
    total = sum(lang_counts.values())
    if total < UNDERREP_MIN_CORPUS:
        return False
    return lang_counts.get(language, 0) / total < UNDERREP_SHARE


__all__ = [
    "MAX_AMOUNT",
    "parse_amount",
    "is_valid_amount",
    "BonusBreakdown",
    "compute_synx",
    "update_streak",
    "streak_multiplier_for",
    "is_first_of_day",
    "is_virgin_topic",
    "is_underrepresented",
    "FIRST_OF_DAY_PCT",
    "VIRGIN_TOPIC_PCT",
    "UNDERREP_LANG_PCT",
    "STREAK_DAYS_REQUIRED",
    "STREAK_MULTIPLIER",
    "PERFECT_SCORE_EXTRA",
]
