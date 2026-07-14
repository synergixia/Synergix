"""Pruebas de la tabla de rangos v1.0 (§5.5) y la migración de rangos legacy."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aisynergix.bot.identity import RANK_TABLE, UserProfile


def _profile(points: int) -> UserProfile:
    p = UserProfile(uid=12345)
    p.points = points
    p.calculate_rank()
    return p


def test_table_matches_design():
    expected = {
        "🌱 Iniciado":     (0,     10, 1.0),
        "⚡ Explorador":   (500,   15, 1.2),
        "🔥 Contribuidor": (2000,  20, 1.5),
        "💎 Experto":      (5000,  25, 1.8),
        "🌌 Arquitecto":   (10000, 30, 2.5),
        "🔮 Oráculo":      (15000, float("inf"), 3.0),
    }
    assert set(RANK_TABLE) == set(expected)
    for rank, (pts, limit, mult) in expected.items():
        cfg = RANK_TABLE[rank]
        assert cfg["min_points"] == pts, rank
        assert cfg["daily_limit"] == limit, rank
        assert cfg["multiplier"] == mult, rank


def test_rank_thresholds():
    cases = [
        (0, "🌱 Iniciado"), (499, "🌱 Iniciado"),
        (500, "⚡ Explorador"), (1999, "⚡ Explorador"),
        (2000, "🔥 Contribuidor"), (4999, "🔥 Contribuidor"),
        (5000, "💎 Experto"), (9999, "💎 Experto"),
        (10000, "🌌 Arquitecto"), (14999, "🌌 Arquitecto"),
        (15000, "🔮 Oráculo"), (999999, "🔮 Oráculo"),
    ]
    for points, expected in cases:
        assert _profile(points).rank == expected, f"{points} pts"


def test_daily_limits():
    assert _profile(0).daily_limit == 10
    assert _profile(500).daily_limit == 15
    assert _profile(2000).daily_limit == 20
    assert _profile(5000).daily_limit == 25
    assert _profile(10000).daily_limit == 30
    assert _profile(15000).daily_limit == 9999  # ∞ interno


def test_legacy_rank_migrates_by_points():
    # Perfiles sellados con la tabla anterior: el rango se recalcula por
    # puntos en vez de degradar al usuario a Iniciado.
    cases = [
        ("📈 Activo", 150, "🌱 Iniciado"),          # 150 < 500 en tabla nueva
        ("🧬 Sincronizado", 700, "⚡ Explorador"),
        ("🏗️ Arquitecto", 3000, "🔥 Contribuidor"),
        ("🧠 Mente Colmena", 6000, "💎 Experto"),
        ("🧠 Mente Colmena", 12000, "🌌 Arquitecto"),
        ("🔮 Oráculo", 20000, "🔮 Oráculo"),        # nombre vigente: intacto
    ]
    for legacy_rank, points, expected in cases:
        p = UserProfile.from_tags(12345, {"rank": legacy_rank, "points": str(points)})
        assert p.rank == expected, f"{legacy_rank} @ {points} pts → {p.rank}"
        assert p.points == points  # los puntos nunca se tocan


def test_bot_rank_map_covers_current_and_legacy():
    # El mapa de traducción del bot debe cubrir la tabla vigente Y los
    # nombres legacy que aún viven en Irys.  Se lee del fuente para no
    # importar bot.py (requiere aiogram y TELEGRAM_TOKEN).
    src = (Path(__file__).resolve().parent.parent / "aisynergix/bot/bot.py").read_text()
    for rank in RANK_TABLE:
        assert f'"{rank}"' in src, f"falta {rank} en _RANK_KEY_MAP"
    for legacy in ("📈 Activo", "🧬 Sincronizado", "🧠 Mente Colmena"):
        assert f'"{legacy}"' in src, f"falta legacy {legacy} en _RANK_KEY_MAP"


def test_locales_have_new_rank_keys():
    import json
    base = Path(__file__).resolve().parent.parent / "aisynergix/bot/locales"
    for lang in ["es", "en", "zh", "hi", "ar", "fr", "bn", "pt", "id", "ur"]:
        d = json.loads((base / f"{lang}.json").read_text(encoding="utf-8"))
        for key in ("rank_explorador", "rank_contribuidor", "rank_experto",
                    "benefit_explorador", "benefit_contribuidor", "benefit_experto"):
            assert key in d, f"{lang}: falta {key}"
        assert d["rank_arquitecto"].startswith("🌌"), f"{lang}: icono Arquitecto"


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
