"""Pruebas de la lógica pura de nodos (sin red ni dependencias pesadas)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aisynergix.nodes import node_manager as nm
from aisynergix.nodes import knowledge_map as km


def test_normalize_topics_filters_dedupes_caps():
    out = nm.normalize_topics(
        ["salud", "SALUD", "educacion", "inexistente", "empleo",
         "servicios", "seguridad", "economia"]
    )
    # 'salud' dedupado (case-insensitive), 'inexistente' descartado, cap a 5.
    assert out == ["salud", "educacion", "empleo", "servicios", "seguridad"]
    assert len(out) == nm.MAX_TOPICS_PER_NODE


def test_generate_node_id_shape():
    nid = nm.generate_node_id("abc123def456", "Barrio Sur")
    assert nid.startswith("node_")
    assert len(nid) == len("node_") + 12


def test_coverage_levels_and_multipliers():
    topics = ["salud", "empleo", "servicios"]
    # objetivo por tema = 20. salud: 18→90% covered; empleo: 10→50% activo ×3;
    # servicios: 4→20% crítico ×5.
    aportes = (
        [{"topic": "salud"}] * 18
        + [{"category": "empleo"}] * 10   # usa fallback a 'category'
        + [{"topic": "servicios"}] * 4
        + [{"topic": "ignorado_no_es_tema"}] * 99
    )
    cov = km.coverage_from_aportes(topics, aportes)

    assert cov["salud"]["count"] == 18
    assert cov["salud"]["level"] == "covered"
    assert cov["salud"]["multiplier"] == 1.0

    assert cov["empleo"]["count"] == 10
    assert cov["empleo"]["percent"] == 50.0
    assert cov["empleo"]["level"] == "active"
    assert cov["empleo"]["multiplier"] == 3.0

    assert cov["servicios"]["level"] == "critical"
    assert cov["servicios"]["multiplier"] == 5.0


def test_coverage_empty_is_critical():
    cov = km.coverage_from_aportes(["salud"], [])
    assert cov["salud"]["count"] == 0
    assert cov["salud"]["percent"] == 0.0
    assert cov["salud"]["level"] == "critical"
    assert cov["salud"]["multiplier"] == 5.0


def test_coverage_caps_at_100():
    cov = km.coverage_from_aportes(["salud"], [{"topic": "salud"}] * 999)
    assert cov["salud"]["percent"] == 100.0
    assert cov["salud"]["level"] == "covered"


def test_render_map_shape():
    cov = km.coverage_from_aportes(["salud", "empleo"], [{"topic": "salud"}] * 20)
    out = km.render_map(cov, lambda k: k.upper())
    lines = out.split("\n")
    assert len(lines) == 2
    assert "SALUD" in out and "100%" in out
    assert "CRÍTICO" in out  # empleo at 0% is critical


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
