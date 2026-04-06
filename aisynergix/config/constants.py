# aisynergix/config/constants.py
"""
AISYNERGIX / CONFIG / CONSTANTS.PY
═══════════════════════════════════════════════════════════════════════════════
Definición de Rangos, Límites y Meritocracia Web3.
Sincronizado con el Documento Maestro de Synergix.
═══════════════════════════════════════════════════════════════════════════════
"""

# --- TABLA DE RANGOS (Meritocracia) ---
# Nivel: (puntos_minimos, limite_diario, multiplicador, nombre_key)
RANK_TABLE = [
    (0,     5,   1.0, "rank_1"),   #🌱 Iniciado
    (100,   12,  1.1, "rank_2"),   #📈 Activo
    (500,   25,  1.5, "rank_3"),   #🧬 Sincronizado
    (1500,  40,  2.5, "rank_4"),   #🏗️ Arquitecto
    (5000,  60,  3.0, "rank_5"),   #🧠 Mente Colmena
    (15000, 999, 5.0, "rank_6"),   #🔮 Oráculo
]

def get_rank_info(pts: int) -> dict:
    """Retorna la info del rango basada en puntos."""
    # Buscar de mayor a menor
    for threshold, limit, mult, key in reversed(RANK_TABLE):
        if pts >= threshold:
            return {
                "key": key,
                "limit": limit,
                "multiplier": mult,
                "threshold": threshold
            }
    return {"key": "rank_1", "limit": 5, "multiplier": 1.0, "threshold": 0}

# --- LÍMITES Y TIEMPOS ---
RAG_REGALIA_POINTS = 1          # Puntos por uso de aporte en RAG
EVOLUTION_INTERVAL_MIN = 8      # federation_loop (8 min)
FUSION_INTERVAL_MIN = 20        # fusion_brain (20 min)
LOG_FLUSH_INTERVAL_MIN = 5      # log_flush_loop (5 min)
KEEP_ALIVE_INTERVAL_MIN = 4     # keep_alive_loop (4 min)

# --- RUTAS DE GREENFIELD (Soberanía) ---
GF_ROOT = "aisynergix"
GF_PATHS = {
    "brain":    f"{GF_ROOT}/SYNERGIXAI/Synergix_ia.txt",
    "users":    f"{GF_ROOT}/users",
    "aportes":  f"{GF_ROOT}/aportes",
    "discovery": f"{GF_ROOT}/discovery",
    "logs":     f"{GF_ROOT}/logs",
    "backups":  f"{GF_ROOT}/backups",
}
