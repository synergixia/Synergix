"""Pruebas de la geografía de nodos territoriales — lógica pura."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aisynergix.nodes import geo


def test_country_label():
    assert geo.country_label("EC") == "🇪🇨 Ecuador"
    assert geo.country_label("ec") == "🇪🇨 Ecuador"     # case-insensitive
    assert geo.country_label("") == ""
    assert geo.country_label(geo.OTHER_COUNTRY) == "🌐"
    assert geo.country_label("ZZ") == "ZZ"              # desconocido → código


def test_is_valid_country():
    assert geo.is_valid_country("EC") is True
    assert geo.is_valid_country("pe") is True
    assert geo.is_valid_country(geo.OTHER_COUNTRY) is True
    assert geo.is_valid_country("ZZ") is False
    assert geo.is_valid_country("") is False


def test_normalize_region():
    assert geo.normalize_region("  Quito   Norte ") == "Quito Norte"
    assert geo.normalize_region("Q") is None            # muy corta
    assert geo.normalize_region("x" * 61) is None       # muy larga
    assert geo.normalize_region("") is None


def test_needs_location_and_region():
    assert geo.needs_location("barrio") is True
    assert geo.needs_location("pais") is True
    assert geo.needs_location("tematico") is False
    assert geo.needs_location("global") is False
    assert geo.needs_region("barrio") is True
    assert geo.needs_region("pais") is False


def test_location_label():
    assert geo.location_label("EC", "Quito") == "📍 Quito · 🇪🇨 Ecuador"
    assert geo.location_label("EC", "") == "📍 🇪🇨 Ecuador"
    assert geo.location_label("", "") == ""


def test_group_by_country_sorted_and_skips_empty():
    records = [
        {"country": "EC"}, {"country": "EC"}, {"country": "PE"},
        {"country": ""}, {},                       # sin país → fuera
    ]
    grouped = geo.group_by_country(records)
    assert grouped == [("EC", 2), ("PE", 1)]


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn(); print(f"PASS {fn.__name__}")
        except Exception:
            failed += 1; print(f"FAIL {fn.__name__}"); traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
