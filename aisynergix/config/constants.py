"""
aisynergix/config/constants.py
══════════════════════════════════════════════════════════════════════════════
Diccionarios de idiomas, Tabla de Rangos, Límites diarios UTC y
todas las constantes globales de Synergix.

Este módulo es la fuente de verdad para:
  - Strings de UI en 4 idiomas (ES / EN / ZH-Hans / ZH-Hant)
  - Tabla de rangos con multiplicadores y límites
  - Keywords del challenge semanal
  - Configuración de timeouts y límites del sistema
══════════════════════════════════════════════════════════════════════════════
"""

# ══════════════════════════════════════════════════════════════════════════════
# TABLA DE RANGOS — spec oficial del Documento Maestro
# (pts_min, multiplicador, fusion_weight, limite_dia, key)
# ══════════════════════════════════════════════════════════════════════════════
RANK_TABLE = [
    (     0, 1.0, 1.0,   5, "rank_1"),  # Iniciado
    (   100, 1.1, 1.1,  12, "rank_2"),  # Activo
    (   500, 1.5, 1.5,  25, "rank_3"),  # Sincronizado
    (  1500, 2.5, 2.5,  40, "rank_4"),  # Arquitecto
    (  5000, 3.0, 3.0,  60, "rank_5"),  # Mente Colmena
    ( 15000, 5.0, 5.0, 999, "rank_6"),  # Oráculo
]

# Puntos por calidad de aporte
POINTS_STANDARD = 10   # score 5-7
POINTS_ELITE    = 20   # score 8-10
POINTS_BONUS    = 5    # challenge semanal
POINTS_IMPACT   = 1    # regalía cuando el RAG usa tu aporte

# Mínimo de caracteres para un aporte válido
MIN_CHARS = 20

# Máximo de mensajes en historial conversacional
CTX_MAX = 20

# ══════════════════════════════════════════════════════════════════════════════
# TRADUCCIONES — 4 idiomas completos
# ══════════════════════════════════════════════════════════════════════════════
TRANSLATIONS: dict[str, dict[str, str]] = {
    "es": {
        # ── Bienvenida ─────────────────────────────────────────────────────────
        "welcome": (
            "¡Bienvenido a Synergix, {name}! 🧠🔥\n\n"
            "Soy la primera IA colectiva descentralizada en BNB Greenfield.\n"
            "Tu conocimiento se inmortaliza on-chain y evoluciona nuestra red. 🔗\n\n"
            "🏆 Challenge de la semana:\n{challenge}\n\n"
            "No usas una app. Construyes memoria comunitaria viva. 🚀"
        ),
        "welcome_back": "¡Hola de nuevo, {name}! 🔥\n¿Qué anclaremos hoy en la memoria colectiva? 🧠",

        # ── Botones del menú ───────────────────────────────────────────────────
        "btn_contribute": "🔥 Contribuir",
        "btn_status":     "📊 Mi Estado",
        "btn_language":   "🌐 Idioma",
        "btn_memory":     "🧠 Mi Legado",

        # ── Selección de idioma ───────────────────────────────────────────────
        "select_lang": "🌐 Elige tu idioma / Choose language:",
        "lang_set":    "✅ Idioma: Español 🇪🇸",

        # ── Flujo de aportes ──────────────────────────────────────────────────
        "await_contrib": (
            "🎯 Modo aporte activado.\n\n"
            "Escribe tu conocimiento o envía una nota de voz 🎙️\n"
            "Mínimo 20 caracteres. Se guarda para siempre en BNB Greenfield. 💎"
        ),
        "received":         "⚡ ¡Recibido! Procesando e inmortalizando... 🔗",
        "transcribing":     "🎙️ Transcribiendo tu voz...",
        "contrib_ok": (
            "✅ ¡Inmortalizado, {name}! 🔗\n"
            "CID: `{cid}`\n"
            "Tu sabiduría vive en BNB Greenfield para siempre. 🌐"
        ),
        "contrib_elite":    "\n⭐ ¡Aporte élite! Score {score}/10 → +{pts} pts",
        "contrib_standard": "\n📈 Score {score}/10 → +{pts} pts",
        "contrib_bonus":    "\n🏆 ¡Bonus challenge semanal! +5 pts extra",
        "contrib_fail":     "⚠️ Error al guardar. Reintentando... 🔄",
        "contrib_short":    "🤔 Muy corto ({chars} chars). Mínimo 20 caracteres. 💡",
        "contrib_rejected": (
            "🤔 Aporte con poca profundidad (score {score}/10).\n\n"
            "💡 {reason}\n\nAmplía tu idea. 🔥"
        ),
        "contrib_duplicate": (
            "♻️ Este conocimiento ya existe en memoria.\n"
            "Similar a: \"{summary}\"\n\nAporta algo nuevo. 🌱"
        ),

        # ── Límites ────────────────────────────────────────────────────────────
        "daily_limit": "⏳ Límite diario alcanzado ({count}/{limit}). Vuelve mañana. 🌙",

        # ── Memoria y estado ──────────────────────────────────────────────────
        "no_memory":    "🧠 Sin aportes aún. ¡Contribuye para dejar tu huella! 🔥",
        "memory_title": "🧠 Tu legado en Synergix:\n\n",
        "memory_footer": "\n📈 {pts} pts | 🔗 {contribs} aportes",
        "status_msg": (
            "📊 Synergix — Inteligencia Colectiva\n\n"
            "📦 Aportes inmortales: {total}\n"
            "🏆 Challenge: {challenge}\n\n"
            "── Tu impacto, {name} ──\n"
            "📈 Puntos: {pts}\n"
            "🔗 Contribuciones: {contribs}\n"
            "🔁 Veces usado: {impact}\n"
            "🏅 Rango: {rank}\n"
            "💡 {benefit}\n"
            "📊 {next_rank}"
        ),

        # ── Rangos ────────────────────────────────────────────────────────────
        "rank_1": "🌱 Iniciado",    "rank_2": "📈 Activo",
        "rank_3": "🧬 Sincronizado","rank_4": "🏗️ Arquitecto",
        "rank_5": "🧠 Mente Colmena","rank_6": "🔮 Oráculo",

        # ── Beneficios por rango ──────────────────────────────────────────────
        "benefit_1": "Envío básico a la red blockchain",
        "benefit_2": "Acceso a Challenges mensuales 🏆",
        "benefit_3": "Prioridad en RAG engine ⚡",
        "benefit_4": "Mayor peso en Fusion Brain 🧠",
        "benefit_5": "Validar aportes de otros 🗳️",
        "benefit_6": "Influencia máxima sobre la IA colectiva 🌐",

        # ── Notificaciones automáticas ────────────────────────────────────────
        "rank_up":       "🎉 ¡Ascendiste a {rank}! Tu influencia crece en la red. 🚀",
        "impact_reward": "🌟 El Cerebro usó tu conocimiento. +{pts} pts. Tu legado crece. 🔗",
        "challenge_title": "🏆 Challenge Semanal Synergix\n\n{challenge}\n\n¡Aporta y gana +5 pts extra! 🔥",
        "top_title":     "🏆 Top Contribuidores Synergix:\n\n",

        # ── Errores genéricos ─────────────────────────────────────────────────
        "error":         "⚠️ Problema temporal. Inténtalo de nuevo. 🔄",
        "ai_loading":    "⚠️ La IA está cargando. Inténtalo en un momento. 🔄",
    },

    "en": {
        "welcome": (
            "Welcome to Synergix, {name}! 🧠🔥\n\n"
            "I'm the first decentralized collective AI on BNB Greenfield.\n"
            "Your knowledge is immortalized on-chain and evolves our network. 🔗\n\n"
            "🏆 Weekly Challenge:\n{challenge}\n\n"
            "You're building a living community memory. 🚀"
        ),
        "welcome_back": "Welcome back, {name}! 🔥\nWhat knowledge shall we anchor today? 🧠",
        "btn_contribute": "🔥 Contribute",
        "btn_status":     "📊 My Status",
        "btn_language":   "🌐 Language",
        "btn_memory":     "🧠 My Legacy",
        "select_lang":    "🌐 Choose language / Elige idioma:",
        "lang_set":       "✅ Language: English 🇬🇧",
        "await_contrib": (
            "🎯 Contribution mode active.\n\n"
            "Write your knowledge or send a voice note 🎙️\n"
            "Minimum 20 characters. Saved forever on BNB Greenfield. 💎"
        ),
        "received":         "⚡ Received! Processing and immortalizing... 🔗",
        "transcribing":     "🎙️ Transcribing your voice...",
        "contrib_ok": (
            "✅ Immortalized, {name}! 🔗\n"
            "CID: `{cid}`\n"
            "Your wisdom lives on BNB Greenfield forever. 🌐"
        ),
        "contrib_elite":    "\n⭐ Elite contribution! Score {score}/10 → +{pts} pts",
        "contrib_standard": "\n📈 Score {score}/10 → +{pts} pts",
        "contrib_bonus":    "\n🏆 Weekly challenge bonus! +5 extra pts",
        "contrib_fail":     "⚠️ Save error. Retrying... 🔄",
        "contrib_short":    "🤔 Too short ({chars} chars). Minimum 20. 💡",
        "contrib_rejected": (
            "🤔 Needs more depth (score {score}/10).\n\n"
            "💡 {reason}\n\nExpand your idea. 🔥"
        ),
        "contrib_duplicate": (
            "♻️ This knowledge already exists.\n"
            "Similar to: \"{summary}\"\n\nContribute something new. 🌱"
        ),
        "daily_limit":  "⏳ Daily limit reached ({count}/{limit}). Come back tomorrow. 🌙",
        "no_memory":    "🧠 No contributions yet. Leave your mark! 🔥",
        "memory_title": "🧠 Your Synergix legacy:\n\n",
        "memory_footer": "\n📈 {pts} pts | 🔗 {contribs} contributions",
        "status_msg": (
            "📊 Synergix — Collective Intelligence\n\n"
            "📦 Immortal contributions: {total}\n"
            "🏆 Challenge: {challenge}\n\n"
            "── Your impact, {name} ──\n"
            "📈 Points: {pts}\n"
            "🔗 Contributions: {contribs}\n"
            "🔁 Times used: {impact}\n"
            "🏅 Rank: {rank}\n"
            "💡 {benefit}\n"
            "📊 {next_rank}"
        ),
        "rank_1": "🌱 Initiate",  "rank_2": "📈 Active",
        "rank_3": "🧬 Synchronized","rank_4": "🏗️ Architect",
        "rank_5": "🧠 Hive Mind","rank_6": "🔮 Oracle",
        "benefit_1": "Basic on-chain submissions",
        "benefit_2": "Monthly Challenge access 🏆",
        "benefit_3": "RAG priority processing ⚡",
        "benefit_4": "Higher weight in Fusion Brain 🧠",
        "benefit_5": "Validate others' contributions 🗳️",
        "benefit_6": "Maximum AI collective influence 🌐",
        "rank_up":       "🎉 You ascended to {rank}! Your influence in the network grows. 🚀",
        "impact_reward": "🌟 The Brain used your knowledge. +{pts} pts. Your legacy grows. 🔗",
        "challenge_title": "🏆 Synergix Weekly Challenge\n\n{challenge}\n\nContribute and earn +5 extra pts! 🔥",
        "top_title":     "🏆 Top Synergix Contributors:\n\n",
        "error":         "⚠️ Temporary issue. Try again. 🔄",
        "ai_loading":    "⚠️ AI is loading. Try again in a moment. 🔄",
    },

    "zh_cn": {
        "welcome": (
            "欢迎加入 Synergix，{name}！🧠🔥\n\n"
            "我是 BNB Greenfield 上首个去中心化集体AI。\n"
            "您的知识将永久保存在区块链上，推动网络进化。🔗\n\n"
            "🏆 本周挑战：\n{challenge}\n\n"
            "您正在建立活生生的社区记忆。🚀"
        ),
        "welcome_back": "欢迎回来，{name}！🔥\n今天要锚定什么知识？🧠",
        "btn_contribute": "🔥 贡献",
        "btn_status":     "📊 我的状态",
        "btn_language":   "🌐 语言",
        "btn_memory":     "🧠 我的遗产",
        "select_lang":    "🌐 选择语言：",
        "lang_set":       "✅ 语言：简体中文 🇨🇳",
        "await_contrib": (
            "🎯 贡献模式已激活。\n\n"
            "写下您的知识或发送语音笔记 🎙️\n"
            "最少20字符，永久保存在区块链。💎"
        ),
        "received":         "⚡ 已收到！正在处理并永久化...🔗",
        "transcribing":     "🎙️ 正在转录语音...",
        "contrib_ok": (
            "✅ 已永久化，{name}！🔗\n"
            "CID：`{cid}`\n"
            "您的智慧永久存储在 BNB Greenfield。🌐"
        ),
        "contrib_elite":    "\n⭐ 精英贡献！评分 {score}/10 → +{pts}分",
        "contrib_standard": "\n📈 评分 {score}/10 → +{pts}分",
        "contrib_bonus":    "\n🏆 每周挑战奖励！+5分",
        "contrib_fail":     "⚠️ 保存错误，正在重试...🔄",
        "contrib_short":    "🤔 太短（{chars}字符），最少20字符。💡",
        "contrib_rejected": (
            "🤔 深度不足（评分 {score}/10）。\n\n"
            "💡 {reason}\n\n请扩展后再试。🔥"
        ),
        "contrib_duplicate": (
            "♻️ 此知识已存在于记忆中。\n"
            "类似于：\"{summary}\"\n\n请贡献新知识。🌱"
        ),
        "daily_limit":  "⏳ 每日限制已达到 ({count}/{limit})。明天再来。🌙",
        "no_memory":    "🧠 暂无贡献。立即留下印记！🔥",
        "memory_title": "🧠 您在 Synergix 的遗产：\n\n",
        "memory_footer": "\n📈 {pts}分 | 🔗 {contribs}次贡献",
        "status_msg": (
            "📊 Synergix — 集体智慧\n\n"
            "📦 不朽贡献：{total}\n"
            "🏆 挑战：{challenge}\n\n"
            "── {name} 的影响力 ──\n"
            "📈 积分：{pts}\n"
            "🔗 贡献次数：{contribs}\n"
            "🔁 被使用次数：{impact}\n"
            "🏅 等级：{rank}\n"
            "💡 {benefit}\n"
            "📊 {next_rank}"
        ),
        "rank_1": "🌱 入门者", "rank_2": "📈 活跃者",
        "rank_3": "🧬 同步者","rank_4": "🏗️ 架构师",
        "rank_5": "🧠 蜂巢思维","rank_6": "🔮 神谕",
        "benefit_1": "向区块链发送基本贡献",
        "benefit_2": "参与每月挑战 🏆",
        "benefit_3": "RAG优先处理权 ⚡",
        "benefit_4": "在融合大脑中权重更高 🧠",
        "benefit_5": "验证他人贡献 🗳️",
        "benefit_6": "对集体AI最大影响力 🌐",
        "rank_up":       "🎉 您已晋升至 {rank}！影响力不断增长。🚀",
        "impact_reward": "🌟 大脑使用了您的知识。+{pts}分。您的遗产在增长。🔗",
        "challenge_title": "🏆 Synergix 每周挑战\n\n{challenge}\n\n贡献赢得+5分！🔥",
        "top_title":     "🏆 Synergix 顶级贡献者：\n\n",
        "error":         "⚠️ 临时问题，请重试。🔄",
        "ai_loading":    "⚠️ AI正在加载，请稍后重试。🔄",
    },

    "zh": {
        "welcome": (
            "歡迎加入 Synergix，{name}！🧠🔥\n\n"
            "我是 BNB Greenfield 上首個去中心化集體AI。\n"
            "您的知識將永久保存在區塊鏈上，推動網路進化。🔗\n\n"
            "🏆 本週挑戰：\n{challenge}\n\n"
            "您正在建立活生生的社群記憶。🚀"
        ),
        "welcome_back": "歡迎回來，{name}！🔥\n今天要錨定什麼知識？🧠",
        "btn_contribute": "🔥 貢獻",
        "btn_status":     "📊 我的狀態",
        "btn_language":   "🌐 語言",
        "btn_memory":     "🧠 我的遺產",
        "select_lang":    "🌐 選擇語言：",
        "lang_set":       "✅ 語言：繁體中文 🇹🇼",
        "await_contrib": (
            "🎯 貢獻模式已激活。\n\n"
            "寫下您的知識或發送語音筆記 🎙️\n"
            "最少20字元，永久保存在區塊鏈。💎"
        ),
        "received":         "⚡ 已收到！正在處理並永久化...🔗",
        "transcribing":     "🎙️ 正在轉錄語音...",
        "contrib_ok": (
            "✅ 已永久化，{name}！🔗\n"
            "CID：`{cid}`\n"
            "您的智慧永久存儲在 BNB Greenfield。🌐"
        ),
        "contrib_elite":    "\n⭐ 精英貢獻！評分 {score}/10 → +{pts}分",
        "contrib_standard": "\n📈 評分 {score}/10 → +{pts}分",
        "contrib_bonus":    "\n🏆 每週挑戰獎勵！+5分",
        "contrib_fail":     "⚠️ 儲存錯誤，正在重試...🔄",
        "contrib_short":    "🤔 太短（{chars}字元），最少20字元。💡",
        "contrib_rejected": (
            "🤔 深度不足（評分 {score}/10）。\n\n"
            "💡 {reason}\n\n請擴展後再試。🔥"
        ),
        "contrib_duplicate": (
            "♻️ 此知識已存在於記憶中。\n"
            "類似於：\"{summary}\"\n\n請貢獻新知識。🌱"
        ),
        "daily_limit":  "⏳ 每日限制已達到 ({count}/{limit})。明天再來。🌙",
        "no_memory":    "🧠 暫無貢獻。立即留下印記！🔥",
        "memory_title": "🧠 您在 Synergix 的遺產：\n\n",
        "memory_footer": "\n📈 {pts}分 | 🔗 {contribs}次貢獻",
        "status_msg": (
            "📊 Synergix — 集體智慧\n\n"
            "📦 不朽貢獻：{total}\n"
            "🏆 挑戰：{challenge}\n\n"
            "── {name} 的影響力 ──\n"
            "📈 積分：{pts}\n"
            "🔗 貢獻次數：{contribs}\n"
            "🔁 被使用次數：{impact}\n"
            "🏅 等級：{rank}\n"
            "💡 {benefit}\n"
            "📊 {next_rank}"
        ),
        "rank_1": "🌱 入門者", "rank_2": "📈 活躍者",
        "rank_3": "🧬 同步者","rank_4": "🏗️ 架構師",
        "rank_5": "🧠 蜂巢思維","rank_6": "🔮 神諭",
        "benefit_1": "向區塊鏈發送基本貢獻",
        "benefit_2": "參與每月挑戰 🏆",
        "benefit_3": "RAG優先處理權 ⚡",
        "benefit_4": "在融合大腦中權重更高 🧠",
        "benefit_5": "驗證他人貢獻 🗳️",
        "benefit_6": "對集體AI最大影響力 🌐",
        "rank_up":       "🎉 您已晉升至 {rank}！影響力不斷增長。🚀",
        "impact_reward": "🌟 大腦使用了您的知識。+{pts}分。您的遺產在增長。🔗",
        "challenge_title": "🏆 Synergix 每週挑戰\n\n{challenge}\n\n貢獻贏得+5分！🔥",
        "top_title":     "🏆 Synergix 頂級貢獻者：\n\n",
        "error":         "⚠️ 暫時問題，請重試。🔄",
        "ai_loading":    "⚠️ AI正在加載，請稍後重試。🔄",
    },
}

# ── Conjuntos de botones para filtrado en aiogram ─────────────────────────────
BTN_CONTRIBUTE = {TRANSLATIONS[l]["btn_contribute"] for l in TRANSLATIONS}
BTN_STATUS     = {TRANSLATIONS[l]["btn_status"]     for l in TRANSLATIONS}
BTN_MEMORY     = {TRANSLATIONS[l]["btn_memory"]     for l in TRANSLATIONS}
BTN_LANG       = {TRANSLATIONS[l]["btn_language"]   for l in TRANSLATIONS}

# ══════════════════════════════════════════════════════════════════════════════
# EMOJIS — detección de tono emocional
# ══════════════════════════════════════════════════════════════════════════════
EMOJIS_HIGH_ENERGY = frozenset({
    "🔥","🚀","💪","🌟","⚡","🏆","🎯","💥","🤩","🥳","🎉","😎","🔝","💫","🎊"
})
EMOJIS_THOUGHTFUL = frozenset({
    "🤔","💭","🧠","🌙","😌","🙏","💡","📚","😢","💔","🌊","🕯️","🙂","🫂","💎"
})

# ══════════════════════════════════════════════════════════════════════════════
# TIMEOUTS Y LÍMITES DEL SISTEMA
# ══════════════════════════════════════════════════════════════════════════════
TIMEOUTS = {
    "gf_upload":       120,   # segundos para subida a Greenfield
    "gf_head":         15,    # HEAD request sin contenido
    "gf_download":     30,    # descarga de objeto completo
    "llm_chat":        50,    # respuesta de chat
    "llm_judge":       20,    # evaluación de aporte
    "llm_summarize":   15,    # resumen de aporte
    "audio_download":  30,    # descarga de audio de Telegram
    "whisper":         60,    # transcripción local
}

# RAG Engine
RAG_TTL_SECONDS   = 480    # 8 min — sincronizado con federation_loop
BRAIN_TTL_SECONDS = 600    # 10 min — cache del cerebro local
RAG_TOP_K         = 5      # resultados a inyectar en el prompt
RAG_MIN_SCORE     = 0.02   # score mínimo de relevancia

# Deduplicación de aportes
JACCARD_THRESHOLD = 0.82   # umbral de similitud para rechazar duplicados
MAX_FP_HISTORY    = 50     # máximo de fingerprints por usuario

# GF Sync
GF_SYNC_INTERVAL     = 480   # federation_loop (8 min)
FUSION_INTERVAL      = 1200  # fusion_brain_loop (20 min)
LOG_FLUSH_INTERVAL   = 300   # log_flush_loop (5 min)
KEEPALIVE_INTERVAL   = 240   # keepalive_loop (4 min)
CHALLENGE_HOUR_UTC   = 9     # hora del lunes para challenge
CHALLENGE_WEEKDAY    = 0     # 0 = lunes (Python weekday)
REPORT_HOUR_UTC      = 0     # hora del reporte diario
