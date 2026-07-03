"""agent.py — el Agente Synergix: de responder a ACTUAR (Módulo 7, §9).

Niveles implementados en esta fase:

  * Nivel 2 — Conecta: "necesito un electricista de confianza" → detecta la
    intención y responde con los proveedores verificados del nodo.
  * Nivel 3 — Actúa: "dona 10 SYNX al proyecto de la escuela" → localiza el
    proyecto y ejecuta la transferencia TRAS confirmación explícita (nunca
    mueve SYNX sin un botón de confirmación).
  * Nivel 4 — Anticipa: cuando el Thinker no encuentra respuesta ni en la
    memoria inmortal ni en la web, registra un Vacío de Conocimiento público
    en el nodo (IEC §9.3) para que la comunidad lo llene.

Los detectores son deterministas (regex multilingüe es/en) y puros — sin
LLM extra por mensaje y 100 % testeables.  Un clasificador LLM puede
sustituirlos más adelante sin tocar a los callers.
"""

import re
from typing import Dict, List, Optional, Tuple

# ── Nivel 2: búsqueda de proveedores ─────────────────────────────────────

# Palabra de profesión → categoría de proveedor.
_PROFESSION_MAP: Dict[str, str] = {
    r"electricistas?|electricians?":                        "electricidad",
    r"plomeros?|fontaneros?|plumbers?":                     "plomeria",
    r"mec[aá]nicos?|mechanics?":                            "mecanica",
    r"alba[ñn]il(?:es)?|constructor(?:es)?|builders?":      "construccion",
    r"m[eé]dic[oa]s?|doctor(?:es|a)?s?|enfermer[oa]s?|nurses?": "salud",
    r"profesor(?:es|a)?s?|maestr[oa]s?|tutor(?:es)?|teachers?": "educacion",
    r"programador(?:es|a)?s?|t[eé]cnic[oa]s?|developers?":  "tecnologia",
}
_PROFESSION_RES = [(re.compile(p, re.IGNORECASE), cat) for p, cat in _PROFESSION_MAP.items()]

# Verbo/expresión de búsqueda — exigido para evitar falsos positivos
# ("mi tío es electricista" no debe disparar al agente).
_SEEKING_RE = re.compile(
    r"\b(necesito|busco|buscando|consig[ao]|conoce[sn]?|recomienda[sn]?|"
    r"hay alg[uú]n|d[oó]nde hay|qui[eé]n|"
    r"need|looking for|searching for|recommend|know (?:a|any)|where can i find|is there a)\b",
    re.IGNORECASE,
)


def detect_provider_intent(text: str) -> Optional[str]:
    """Si el mensaje busca un profesional, retorna la categoría; si no, None."""
    if not text or not _SEEKING_RE.search(text):
        return None
    for regex, category in _PROFESSION_RES:
        if regex.search(text):
            return category
    return None


# ── Nivel 3: donación a proyectos ────────────────────────────────────────

_DONATE_RE = re.compile(
    r"\b(don[ao](?:r)?|dona[rn]?|aport[ao](?:r)?|contribuy[eo]|"
    r"donate|contribute|fund|give)\b"
    r".{0,40}?(\d+(?:[.,]\d+)?)\s*synx",
    re.IGNORECASE | re.DOTALL,
)


def detect_donation_intent(text: str) -> Optional[float]:
    """Si el mensaje pide donar N SYNX, retorna el monto; si no, None."""
    if not text:
        return None
    m = _DONATE_RE.search(text)
    if not m:
        return None
    from aisynergix.services.rewards import parse_amount
    return parse_amount(m.group(2))


def _tokens(s: str) -> set:
    return {w for w in re.findall(r"\w+", (s or "").lower()) if len(w) > 3}


def match_project(projects: List[Dict[str, str]], text: str) -> Optional[Dict[str, str]]:
    """Elige el proyecto ACTIVO al que se refiere el mensaje.

    Coincidencia por solapamiento de palabras del título (>3 letras).  Si el
    nodo tiene exactamente un proyecto activo, se asume ese aunque el
    mensaje no lo nombre.  Con ambigüedad (empate) retorna None: el caller
    debe pedir aclaración, nunca adivinar con dinero de por medio.
    """
    active = [p for p in projects if p.get("project-status", "active") == "active"]
    if not active:
        return None
    if len(active) == 1:
        return active[0]
    text_tokens = _tokens(text)
    scored: List[Tuple[int, Dict[str, str]]] = []
    for p in active:
        overlap = len(_tokens(p.get("title", "")) & text_tokens)
        if overlap > 0:
            scored.append((overlap, p))
    if not scored:
        return None
    scored.sort(key=lambda x: -x[0])
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        return None  # empate → ambiguo
    return scored[0][1]


# ── Nivel 4: vacíos de conocimiento (IEC §9.3) ───────────────────────────

MIN_GAP_QUESTION_LEN = 15


def is_gap_worthy(question: str) -> bool:
    """Filtra qué preguntas sin respuesta merecen registrarse como vacío."""
    q = (question or "").strip()
    return len(q) >= MIN_GAP_QUESTION_LEN


async def record_knowledge_gap(node_id: str, question: str, lang: str) -> None:
    """Registra el vacío en Irys (dedupe por pregunta en la capa de storage)."""
    from aisynergix.services.irys import write_knowledge_gap
    if not node_id or not is_gap_worthy(question):
        return
    await write_knowledge_gap(node_id, question.strip(), lang)


__all__ = [
    "detect_provider_intent",
    "detect_donation_intent",
    "match_project",
    "is_gap_worthy",
    "record_knowledge_gap",
    "MIN_GAP_QUESTION_LEN",
]
