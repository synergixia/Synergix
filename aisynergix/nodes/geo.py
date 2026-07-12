"""geo.py — geografía de los nodos territoriales (país → región → barrio).

Los nodos de tipo ``barrio`` y ``pais`` llevan una ubicación sellada en Irys
(tags ``country`` y ``region``), lo que permite explorar la red por territorio
y construir el Atlas de Problemas y Soluciones por lugar.

Los nombres de país usan su endónimo + bandera (neutros entre idiomas), así no
hay que traducir la lista a las 10 lenguas.
"""

from typing import Dict, List, Optional, Tuple

# Código ISO-3166 alpha-2 → etiqueta (bandera + endónimo).
COUNTRIES: Dict[str, str] = {
    "EC": "🇪🇨 Ecuador",
    "PE": "🇵🇪 Perú",
    "CO": "🇨🇴 Colombia",
    "MX": "🇲🇽 México",
    "AR": "🇦🇷 Argentina",
    "BO": "🇧🇴 Bolivia",
    "CL": "🇨🇱 Chile",
    "VE": "🇻🇪 Venezuela",
    "ES": "🇪🇸 España",
    "BR": "🇧🇷 Brasil",
    "PY": "🇵🇾 Paraguay",
    "UY": "🇺🇾 Uruguay",
    "GT": "🇬🇹 Guatemala",
    "HN": "🇭🇳 Honduras",
    "SV": "🇸🇻 El Salvador",
    "CR": "🇨🇷 Costa Rica",
    "PA": "🇵🇦 Panamá",
    "CU": "🇨🇺 Cuba",
    "DO": "🇩🇴 Rep. Dominicana",
    "NI": "🇳🇮 Nicaragua",
    "US": "🇺🇸 USA",
    "IN": "🇮🇳 India",
    "PK": "🇵🇰 پاکستان",
    "BD": "🇧🇩 বাংলাদেশ",
    "ID": "🇮🇩 Indonesia",
    "CN": "🇨🇳 中国",
    "FR": "🇫🇷 France",
    "EG": "🇪🇬 مصر",
    "NG": "🇳🇬 Nigeria",
}

OTHER_COUNTRY = "other"          # cubeta para países fuera de la lista
MAX_REGION_LEN = 60
MIN_REGION_LEN = 2

# Tipos de nodo que llevan ubicación (el resto son a-geográficos).
GEO_NODE_TYPES = ("barrio", "pais")


def country_label(code: str) -> str:
    """Etiqueta legible de un país ('' si no hay país)."""
    code = (code or "").strip()
    if not code:
        return ""
    if code == OTHER_COUNTRY:
        return "🌐"
    return COUNTRIES.get(code.upper(), code.upper())


def is_valid_country(code: str) -> bool:
    code = (code or "").strip()
    return code == OTHER_COUNTRY or code.upper() in COUNTRIES


def normalize_region(text: str) -> Optional[str]:
    """Normaliza el nombre de región/ciudad. None si es inválido."""
    region = " ".join((text or "").split())
    if not (MIN_REGION_LEN <= len(region) <= MAX_REGION_LEN):
        return None
    return region


def needs_location(node_type: str) -> bool:
    return node_type in GEO_NODE_TYPES


def needs_region(node_type: str) -> bool:
    """Solo los barrios piden región/ciudad; un nodo-país ES el país."""
    return node_type == "barrio"


def location_label(country: str, region: str) -> str:
    """'📍 Región · 🇪🇨 Ecuador' — vacío si el nodo no tiene ubicación."""
    country_txt = country_label(country)
    parts = [p for p in ((region or "").strip(), country_txt) if p]
    return ("📍 " + " · ".join(parts)) if parts else ""


def group_by_country(records: List[Dict[str, str]]) -> List[Tuple[str, int]]:
    """Agrupa registros de nodo por país → [(código, nº nodos)] ordenado desc.

    Los nodos sin país no aparecen (son a-geográficos, no 'otros').
    """
    counts: Dict[str, int] = {}
    for r in records:
        code = (r.get("country", "") or "").strip()
        if code:
            counts[code] = counts.get(code, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


__all__ = [
    "COUNTRIES", "OTHER_COUNTRY", "GEO_NODE_TYPES",
    "MAX_REGION_LEN", "MIN_REGION_LEN",
    "country_label", "is_valid_country", "normalize_region",
    "needs_location", "needs_region", "location_label", "group_by_country",
]
