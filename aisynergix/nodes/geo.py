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

# Granularidad geográfica sub-país (clave interna → icono). Etiqueta legible
# vía locales con la clave ``geo_scope_<key>``. Permite filtrar y seleccionar
# nodos por el nivel exacto del territorio.
GEO_SCOPES: Dict[str, str] = {
    "ciudad":     "🏙️",
    "barrio":     "🏘️",
    "zona_rural": "🌾",
    "region":     "🗺️",
}

# Tipos de nodo que llevan ubicación (el resto son a-geográficos).
#   * individual → el nodo personal de un creador, geolocalizado.
#   * barrio     → comunidad local (pide scope + lugar).
#   * pais       → nodo de alcance nacional (solo país).
GEO_NODE_TYPES = ("individual", "barrio", "pais")
# Tipos que además eligen granularidad (ciudad/barrio/zona rural/región) y lugar.
SCOPED_NODE_TYPES = ("individual", "barrio")


MIN_COUNTRY_LEN = 2
MAX_COUNTRY_LEN = 40


def country_label(code: str) -> str:
    """Etiqueta legible de un país ('' si no hay país).

    Códigos ISO conocidos → bandera + endónimo; ISO desconocido (2 letras) →
    el código; país libre escrito por el usuario (texto) → '🌍 Nombre'.
    """
    code = (code or "").strip()
    if not code:
        return ""
    if code == OTHER_COUNTRY:
        return "🌐"
    up = code.upper()
    if up in COUNTRIES:
        return COUNTRIES[up]
    if len(code) == 2:
        return up
    return "🌍 " + code


def is_valid_country(code: str) -> bool:
    code = (code or "").strip()
    return code == OTHER_COUNTRY or code.upper() in COUNTRIES


def normalize_country(text: str) -> Optional[str]:
    """Normaliza un país escrito libremente. None si es inválido.

    Da libertad total de selección de país cuando no está en la lista rápida.
    """
    country = " ".join((text or "").split())
    if not (MIN_COUNTRY_LEN <= len(country) <= MAX_COUNTRY_LEN):
        return None
    if country == OTHER_COUNTRY or country.upper() in COUNTRIES:
        # Un país libre que coincide con la lista: usar su código canónico.
        return country.upper() if country.upper() in COUNTRIES else country
    return country


def normalize_region(text: str) -> Optional[str]:
    """Normaliza el nombre de región/ciudad. None si es inválido."""
    region = " ".join((text or "").split())
    if not (MIN_REGION_LEN <= len(region) <= MAX_REGION_LEN):
        return None
    return region


def needs_location(node_type: str) -> bool:
    return node_type in GEO_NODE_TYPES


def needs_scope(node_type: str) -> bool:
    """Los nodos individuales y de barrio eligen granularidad y lugar; un
    nodo-país ES el país (sin sub-nivel)."""
    return node_type in SCOPED_NODE_TYPES


def needs_region(node_type: str) -> bool:
    """Piden el nombre del lugar los mismos que eligen granularidad."""
    return node_type in SCOPED_NODE_TYPES


def is_valid_scope(scope: str) -> bool:
    return (scope or "").strip() in GEO_SCOPES


def scope_icon(scope: str) -> str:
    return GEO_SCOPES.get((scope or "").strip(), "📍")


def location_label(country: str, region: str, scope: str = "") -> str:
    """'🏘️ Barrio San Juan · 🇪🇨 Ecuador' — vacío si no hay ubicación."""
    country_txt = country_label(country)
    place = (region or "").strip()
    if place and scope:
        place = f"{scope_icon(scope)} {place}"
    parts = [p for p in (place, country_txt) if p]
    return ("📍 " + " · ".join(parts)) if parts else ""


def filter_by(records: List[Dict[str, str]], country: str = "", scope: str = "") -> List[Dict[str, str]]:
    """Filtra registros de nodo por país y/o granularidad (selección geográfica)."""
    out = []
    for r in records:
        if country and (r.get("country", "") or "").strip() != country:
            continue
        if scope and (r.get("geo-scope", "") or "").strip() != scope:
            continue
        out.append(r)
    return out


def scopes_present(records: List[Dict[str, str]]) -> List[str]:
    """Granularidades presentes en un conjunto de nodos (para los filtros)."""
    present = {(r.get("geo-scope", "") or "").strip() for r in records}
    return [s for s in GEO_SCOPES if s in present]


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
    "COUNTRIES", "OTHER_COUNTRY", "GEO_NODE_TYPES", "SCOPED_NODE_TYPES",
    "GEO_SCOPES", "MAX_REGION_LEN", "MIN_REGION_LEN",
    "MIN_COUNTRY_LEN", "MAX_COUNTRY_LEN",
    "country_label", "is_valid_country", "normalize_country", "normalize_region",
    "needs_location", "needs_scope", "needs_region",
    "is_valid_scope", "scope_icon",
    "location_label", "group_by_country", "filter_by", "scopes_present",
]
