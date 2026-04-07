"""
aisynergix/bot/handlers/emotional.py
══════════════════════════════════════════════════════════════════════════════
Espejo Emocional y Clasificación de Mensajes de Synergix.

"Lee la intención del usuario. Si recibe un mensaje con 🔥, responde con
energía arrolladora. Si es una duda técnica, adopta un tono analítico."
— Documento Maestro Synergix

Implementa:
  · detect_tone(text)     — HIGH_ENERGY / THOUGHTFUL / NEUTRAL
  · classify_message(text)— sticker / simple / normal / complex
  · EmotionalMirror       — genera el contexto emocional para el prompt

El Espejo garantiza:
  1. Respuestas emocionales apropiadas sin instrucciones explícitas del usuario
  2. Adaptación automática de longitud de respuesta
  3. Emojis estratégicos — nunca spam, siempre con propósito
══════════════════════════════════════════════════════════════════════════════
"""

import re
from dataclasses import dataclass
from typing import Literal

from aisynergix.config.constants import EMOJIS_HIGH_ENERGY, EMOJIS_THOUGHTFUL

# ── Tipos ─────────────────────────────────────────────────────────────────────
ToneType    = Literal["high_energy", "thoughtful", "neutral"]
MsgType     = Literal["sticker", "simple", "normal", "complex"]

# ── Keywords para clasificación de complejidad ────────────────────────────────
_COMPLEX_KEYWORDS = {
    "es": {
        "cómo", "como", "por qué", "porqué", "explica", "diferencia",
        "comparar", "analiza", "cuál", "cuales", "ventajas", "desventajas",
        "estrategia", "implementar", "funciona", "arquitectura", "protocolo",
        "blockchain", "greenfield", "defi", "smart contract", "tokenomics",
        "descentraliz", "consenso", "validador", "staking", "liquidity",
    },
    "en": {
        "how", "why", "explain", "difference", "compare", "analyze",
        "strategy", "implement", "architecture", "protocol", "what is",
        "blockchain", "greenfield", "defi", "smart contract", "tokenomics",
        "decentraliz", "consensus", "validator", "staking", "liquidity",
    },
    "zh": {
        "什么", "怎么", "为什么", "解释", "比较", "分析",
        "策略", "架构", "协议", "区块链", "去中心化",
    },
}

_GREET_WORDS = {
    "hola","hi","hey","hello","buenas","buenos","buen","bye","adiós",
    "adios","ok","okey","sip","jaja","lol","gracias","thanks","thank",
    "bien","mal","genial","cool","wow","si","no","yes","nope","dale",
    "claro","perfecto","venga","salut","merci","danke","ciao",
    "欢迎","谢谢","好","嗯","哈哈","是","不","再见","你好",
    "歡迎","謝謝","哈哈","對","再見","妳好",
}


# ══════════════════════════════════════════════════════════════════════════════
# FUNCIONES DE DETECCIÓN
# ══════════════════════════════════════════════════════════════════════════════
def detect_tone(text: str) -> ToneType:
    """
    Detecta el tono emocional del mensaje analizando emojis.

    Returns:
        "high_energy" — emojis de energía/celebración (🔥🚀💪)
        "thoughtful"  — emojis reflexivos/melancólicos (🤔💭🌙)
        "neutral"     — sin emojis o emojis neutros
    """
    chars = set(text)
    if any(e in chars for e in EMOJIS_HIGH_ENERGY):
        return "high_energy"
    if any(e in chars for e in EMOJIS_THOUGHTFUL):
        return "thoughtful"
    return "neutral"


def classify_message(text: str) -> MsgType:
    """
    Clasifica el tipo de mensaje para adaptar la longitud de respuesta.

    Returns:
        "sticker"  — emoji solo o 1 carácter (respuesta 1 línea)
        "simple"   — saludo o mensaje corto (1-2 oraciones)
        "normal"   — pregunta o conversación media (2-4 oraciones)
        "complex"  — pregunta técnica o larga (párrafos completos)
    """
    t  = text.strip()
    wc = len(t.split())

    # Mensajes tipo sticker: 1 palabra muy corta o solo emojis
    if wc <= 1 and len(t) <= 4:
        return "sticker"

    # Saludos y respuestas cortas
    first_word = t.lower().split()[0] if t else ""
    if wc <= 3 and first_word in _GREET_WORDS:
        return "simple"

    # Detección de complejidad
    tl = t.lower()
    has_complex = any(
        kw in tl
        for keywords in _COMPLEX_KEYWORDS.values()
        for kw in keywords
    )

    # Preguntas múltiples o texto largo = complejo
    if wc > 12 or (has_complex and wc > 5) or tl.count("?") > 1:
        return "complex"

    return "normal"


# ══════════════════════════════════════════════════════════════════════════════
# EMOTIONAL MIRROR — contexto emocional para el system prompt
# ══════════════════════════════════════════════════════════════════════════════
@dataclass
class EmotionalContext:
    tone:     ToneType
    msg_type: MsgType
    tone_instruction:   str
    length_instruction: str
    emoji_set:          str


class EmotionalMirror:
    """
    Genera el contexto emocional para inyectar en el system prompt.

    Uso:
        mirror  = EmotionalMirror()
        context = mirror.analyze(text="🔥 Explícame DeFi", lang="es")
        # context.tone_instruction → instrucción para el LLM
    """

    # Instrucciones de tono por idioma
    _TONE_INSTRUCTIONS: dict[str, dict[str, str]] = {
        "es": {
            "high_energy": "Tono ENERGÉTICO y ENTUSIASTA. Usa emojis de energía: 🔥🚀⚡💥. Responde con pasión.",
            "thoughtful":  "Tono REFLEXIVO y EMPÁTICO. Analítico, calmado, profundo. Emojis: 💡🌙🤔.",
            "neutral":     "Tono NATURAL y DIRECTO. Cercano como un amigo experto. Emojis estratégicos.",
        },
        "en": {
            "high_energy": "ENERGETIC and ENTHUSIASTIC tone. Use energy emojis: 🔥🚀⚡💥. Respond with passion.",
            "thoughtful":  "THOUGHTFUL and EMPATHETIC tone. Analytical, calm, deep. Emojis: 💡🌙🤔.",
            "neutral":     "NATURAL and DIRECT tone. Friendly expert. Strategic emojis.",
        },
        "zh_cn": {
            "high_energy": "充满活力，热情洋溢。使用表情：🔥🚀⚡💥。",
            "thoughtful":  "沉思，有深度，有同理心。表情：💡🌙🤔。",
            "neutral":     "自然直接，像朋友一样。适当使用表情。",
        },
        "zh": {
            "high_energy": "充滿活力，熱情洋溢。使用表情：🔥🚀⚡💥。",
            "thoughtful":  "沉思，有深度，有同理心。表情：💡🌙🤔。",
            "neutral":     "自然直接，像朋友一樣。適當使用表情。",
        },
    }

    # Instrucciones de longitud por tipo de mensaje
    _LENGTH_INSTRUCTIONS: dict[str, dict[str, str]] = {
        "es": {
            "sticker":  "Respuesta MUY CORTA: exactamente 1 línea. Emotiva. Con emoji.",
            "simple":   "Respuesta CORTA: 1-2 oraciones naturales. Directo.",
            "normal":   "Respuesta NORMAL: 2-4 oraciones. Claro y preciso.",
            "complex":  "Respuesta DETALLADA: párrafos completos con todo el detalle necesario.",
        },
        "en": {
            "sticker":  "VERY SHORT: exactly 1 line. Emotional. With emoji.",
            "simple":   "SHORT: 1-2 natural sentences. Direct.",
            "normal":   "NORMAL: 2-4 sentences. Clear and precise.",
            "complex":  "DETAILED: full paragraphs with all necessary detail.",
        },
        "zh_cn": {
            "sticker":  "极短：1行，有情感，有表情。",
            "simple":   "简短：1-2句，自然。",
            "normal":   "正常：2-4句，清晰准确。",
            "complex":  "详细：完整段落，提供所有必要细节。",
        },
        "zh": {
            "sticker":  "極短：1行，有情感，有表情。",
            "simple":   "簡短：1-2句，自然。",
            "normal":   "正常：2-4句，清晰準確。",
            "complex":  "詳細：完整段落，提供所有必要細節。",
        },
    }

    # Emojis recomendados por tono
    _EMOJI_SETS: dict[str, str] = {
        "high_energy": "🔥🚀⚡💥🌟🏆🎯💪🤩",
        "thoughtful":  "💡🌙🤔💭🧠🔮✨📚",
        "neutral":     "🔥🧠✨🌐💡😄🚀🎯💎🔗",
    }

    def analyze(self, text: str, lang: str = "es",
                is_sticker: bool = False) -> EmotionalContext:
        """
        Analiza texto y retorna contexto emocional para el prompt.

        Args:
            text:       Texto del usuario.
            lang:       Idioma activo (es/en/zh_cn/zh).
            is_sticker: Si True, fuerza msg_type="sticker".
        """
        tone     = detect_tone(text)
        msg_type = "sticker" if is_sticker else classify_message(text)
        lang_    = lang if lang in self._TONE_INSTRUCTIONS else "es"

        return EmotionalContext(
            tone=tone,
            msg_type=msg_type,
            tone_instruction=self._TONE_INSTRUCTIONS[lang_].get(tone, ""),
            length_instruction=self._LENGTH_INSTRUCTIONS[lang_].get(msg_type, ""),
            emoji_set=self._EMOJI_SETS.get(tone, self._EMOJI_SETS["neutral"]),
        )

    def build_context_block(self, text: str, lang: str = "es",
                             is_sticker: bool = False) -> str:
        """
        Genera el bloque de instrucciones emocionales para añadir al system prompt.

        Returns:
            String con instrucciones de tono + longitud, listo para inyectar.
        """
        ctx = self.analyze(text, lang, is_sticker)
        parts = []

        if ctx.tone_instruction:
            parts.append(ctx.tone_instruction)

        if ctx.length_instruction:
            length_labels = {
                "es": "LONGITUD", "en": "LENGTH",
                "zh_cn": "长度", "zh": "長度"
            }
            lbl = length_labels.get(lang, "LENGTH")
            parts.append(f"{lbl}: {ctx.length_instruction}")

        if ctx.emoji_set:
            emoji_labels = {
                "es": "Emojis recomendados",
                "en": "Recommended emojis",
                "zh_cn": "推荐表情",
                "zh": "推薦表情",
            }
            lbl = emoji_labels.get(lang, "Emojis")
            parts.append(f"{lbl}: {ctx.emoji_set}")

        return "\n".join(parts)


# ── Instancia global reutilizable ─────────────────────────────────────────────
emotional_mirror = EmotionalMirror()
