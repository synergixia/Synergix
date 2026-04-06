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
    for threshold, limit, mult, key in reversed(RANK_TABLE):
        if pts >= threshold:
            return {
                "key": key,
                "limit": limit,
                "multiplier": mult,
                "threshold": threshold
            }
    return {"key": "rank_1", "limit": 5, "multiplier": 1.0, "threshold": 0}

# --- TEXTOS MULTILINGÜES (LA CARA DEL BOT) ---
T = {
    "es": {
        "welcome": "¡Bienvenido, {name}! 🌟\n\nSoy Synergix, inteligencia colectiva descentralizada.\nTu conocimiento se guarda para siempre en BNB Greenfield. 🔗",
        "processing": "¡Recibido! Tu sabiduría está siendo procesada e inmortalizada. 🔗",
        "contrib_ok": "¡Gracias, {name}! 🌟\n\nTu aporte forma parte de la Memoria Inmortal Synergix 🔗\nCID: {cid}",
        "impact_notify": "🌟 El Cerebro acaba de utilizar tu conocimiento... ¡Tienes +1 punto! 📈",
        "brain_consult": "🧠 MEMORIA INMORTAL ACTIVA: ",
        "transcribing": "🎙️ Transcribiendo tu nota de voz...",
        "received": "¡Recibido! Procesando tu sabiduría... 🔗",
        "contrib_short": "🤔 Muy corto ({chars} chars). Mínimo 20 caracteres. 🔥",
        "contrib_fail": "⚠️ Error al guardar. Intenta de nuevo.",
        "await_contrib": "🎯 Modo aporte activado! Envía texto o voz. 💡",
    },
    "en": {
        "welcome": "Welcome, {name}! 🌟\n\nI'm Synergix, decentralized collective intelligence.\nYour knowledge is saved forever on BNB Greenfield. 🔗",
        "processing": "Received! Your wisdom is being processed and immortalized. 🔗",
        "contrib_ok": "Thank you, {name}! 🌟\n\nYour contribution is now part of the Immortal Synergix Memory 🔗\nCID: {cid}",
        "impact_notify": "🌟 The Brain just used your knowledge... You got +1 point! 📈",
        "brain_consult": "🧠 IMMORTAL MEMORY ACTIVE: ",
        "transcribing": "🎙️ Transcribing your voice note...",
        "received": "Received! Processing your wisdom... 🔗",
        "contrib_short": "🤔 Too short ({chars} chars). Min 20 characters. 🔥",
        "contrib_fail": "⚠️ Error saving. Try again.",
        "await_contrib": "🎯 Contribution mode active! Send text or voice. 💡",
    }
}

# --- LÍMITES Y TIEMPOS ---
RAG_REGALIA_POINTS = 1
EVOLUTION_INTERVAL_MIN = 8
FUSION_INTERVAL_MIN = 20
LOG_FLUSH_INTERVAL_MIN = 5
KEEP_ALIVE_INTERVAL_MIN = 4
