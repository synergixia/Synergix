"""
agent_reach_synergix.py
═══════════════════════════════════════════════════════════════════════════════
Motor de búsqueda en redes sociales e internet para Synergix.
Inspirado en github.com/Panniantong/Agent-Reach

Plataformas sin proxy (funcionan en Hetzner):
  Web, YouTube (yt-dlp), GitHub API, Twitter (Nitter), Reddit API JSON

Plataformas con cookies opcionales:
  Telegram (canales públicos), TikTok (búsqueda pública)

Se activa automáticamente en MODO B cuando la memoria inmortal
no tiene datos suficientes para responder la pregunta del usuario.

Instalación en Hetzner:
  pip install yt-dlp --break-system-packages
  # No se necesita agent-reach pip — implementación nativa

Variables de entorno opcionales (.env):
  GITHUB_TOKEN=ghp_xxx   # Sube rate limit de 60 a 5000 req/hora
  EXA_API_KEY=exa_xxx    # Búsqueda semántica de alta calidad (plan gratis)
═══════════════════════════════════════════════════════════════════════════════
"""

import asyncio
import json
import os
import time
import logging
import urllib.request
import urllib.parse
import html as _html_mod

logger = logging.getLogger("synergix.reach")

# ── Cache (evitar re-buscar lo mismo en 30 min) ───────────────────────────────
_reach_cache: dict = {}
_REACH_CACHE_TTL   = 1800  # 30 minutos


def _cache_key(platform: str, query: str) -> str:
    import hashlib
    return hashlib.md5(f"{platform}:{query}".encode()).hexdigest()[:12]


def _get_cached(platform: str, query: str) -> str:
    key   = _cache_key(platform, query)
    entry = _reach_cache.get(key)
    if entry and (time.time() - entry["ts"]) < _REACH_CACHE_TTL:
        return entry["result"]
    return ""


def _set_cache(platform: str, query: str, result: str) -> None:
    key = _cache_key(platform, query)
    _reach_cache[key] = {"result": result[:3000], "ts": time.time()}


# ── Helpers ───────────────────────────────────────────────────────────────────
def _jina_fetch(url: str, timeout: int = 15) -> str:
    """Lee cualquier URL via Jina Reader — convierte HTML a texto limpio."""
    jina_url = "https://r.jina.ai/" + url
    req = urllib.request.Request(
        jina_url,
        headers={
            "User-Agent":     "Mozilla/5.0 Synergix/2.0",
            "X-Return-Format":"text",
            "Accept":         "text/plain",
        }
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="ignore")[:3000]


# ═══════════════════════════════════════════════════════════════════════════════
# PLATAFORMAS
# ═══════════════════════════════════════════════════════════════════════════════

async def reach_web(query: str, lang: str = "es") -> str:
    """Lee páginas web via Jina Reader. Si no es URL busca via DuckDuckGo."""
    cached = _get_cached("web", query)
    if cached:
        return cached

    loop = asyncio.get_running_loop()
    try:
        if query.startswith("http"):
            url = query
        else:
            url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)

        result = await loop.run_in_executor(None, lambda: _jina_fetch(url))
        result = result.strip()
        if result:
            _set_cache("web", query, result)
        return result
    except Exception as e:
        logger.debug("reach_web: %s", e)
        return ""


async def reach_youtube(query: str, lang: str = "es") -> str:
    """Busca en YouTube y extrae info de los top 3 videos via yt-dlp."""
    cached = _get_cached("youtube", query)
    if cached:
        return cached

    loop = asyncio.get_running_loop()

    def _fetch():
        import subprocess
        cmd = [
            "yt-dlp",
            "ytsearch3:" + query,
            "--skip-download",
            "--print", "%(title)s|||%(webpage_url)s|||%(description)s",
            "--no-playlist",
            "--quiet",
            "--no-warnings",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        if not res.stdout.strip():
            return ""
        lines   = [l for l in res.stdout.strip().splitlines() if l.strip()][:3]
        summary = []
        for line in lines:
            parts = line.split("|||")
            title = parts[0][:80] if len(parts) > 0 else ""
            url   = parts[1]      if len(parts) > 1 else ""
            desc  = parts[2][:150] if len(parts) > 2 else ""
            summary.append("📺 " + title + "\n🔗 " + url + "\n" + desc)
        return "\n\n".join(summary)

    try:
        result = await loop.run_in_executor(None, _fetch)
        if result:
            _set_cache("youtube", query, result)
        return result
    except Exception as e:
        logger.debug("reach_youtube: %s", e)
        return ""


async def reach_github(query: str, lang: str = "es") -> str:
    """Busca repositorios en GitHub via API pública JSON."""
    cached = _get_cached("github", query)
    if cached:
        return cached

    loop = asyncio.get_running_loop()
    gh_token = os.environ.get("GITHUB_TOKEN", "")

    def _fetch():
        headers = {
            "Accept":     "application/vnd.github.v3+json",
            "User-Agent": "Synergix-Bot/2.0",
        }
        if gh_token:
            headers["Authorization"] = "token " + gh_token

        search_url = (
            "https://api.github.com/search/repositories"
            "?q=" + urllib.parse.quote(query) +
            "&sort=stars&per_page=5"
        )
        req = urllib.request.Request(search_url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as r:
            data  = json.loads(r.read())
        items   = data.get("items", [])[:5]
        results = []
        for item in items:
            name  = item.get("full_name", "")
            stars = item.get("stargazers_count", 0)
            forks = item.get("forks_count", 0)
            lang2 = item.get("language", "")
            desc  = item.get("description", "")[:150]
            url   = item.get("html_url", "")
            line  = (
                "📦 " + name + "\n"
                "⭐ " + str(stars) + " | 🍴 " + str(forks) + " | " + str(lang2) + "\n"
                "📝 " + desc + "\n"
                "🔗 " + url
            )
            results.append(line)
        return "\n\n".join(results)

    try:
        result = await loop.run_in_executor(None, _fetch)
        if result:
            _set_cache("github", query, result)
        return result
    except Exception as e:
        logger.debug("reach_github: %s", e)
        return ""


async def reach_twitter(query: str, lang: str = "es") -> str:
    """Busca tweets via instancias Nitter (sin API key, sin cookies)."""
    cached = _get_cached("twitter", query)
    if cached:
        return cached

    NITTER = [
        "https://nitter.privacydev.net",
        "https://nitter.poast.org",
        "https://nitter.1d4.us",
        "https://nitter.net",
    ]
    loop = asyncio.get_running_loop()

    def _fetch():
        encoded = urllib.parse.quote(query)
        for instance in NITTER:
            try:
                search_url = instance + "/search?q=" + encoded + "&f=tweets"
                text = _jina_fetch(search_url, timeout=12)
                if text and len(text) > 100:
                    lines = [l.strip() for l in text.splitlines() if l.strip()]
                    return "\n".join(lines[:35])[:2000]
            except Exception:
                continue
        return ""

    try:
        result = await loop.run_in_executor(None, _fetch)
        if result:
            _set_cache("twitter", query, result)
        return result
    except Exception as e:
        logger.debug("reach_twitter: %s", e)
        return ""


async def reach_reddit(query: str, lang: str = "es") -> str:
    """Busca en Reddit via API JSON pública (sin auth, sin proxy en Hetzner)."""
    cached = _get_cached("reddit", query)
    if cached:
        return cached

    loop = asyncio.get_running_loop()

    def _fetch():
        encoded = urllib.parse.quote(query)
        url = (
            "https://www.reddit.com/search.json"
            "?q=" + encoded +
            "&sort=relevance&limit=5&t=month"
        )
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Synergix:bot:2.0"}
        )
        with urllib.request.urlopen(req, timeout=12) as r:
            data  = json.loads(r.read())
        posts   = data.get("data", {}).get("children", [])[:5]
        results = []
        for post in posts:
            p    = post.get("data", {})
            sub  = p.get("subreddit", "")
            title = p.get("title", "")[:100]
            score = p.get("score", 0)
            comments = p.get("num_comments", 0)
            permalink = p.get("permalink", "")
            text = p.get("selftext", "")[:200]
            line = (
                "🔴 r/" + sub + ": " + title + "\n"
                "⬆️ " + str(score) + " | 💬 " + str(comments) +
                " | 🔗 https://reddit.com" + permalink + "\n" + text
            )
            results.append(line)
        return "\n\n".join(results)

    try:
        result = await loop.run_in_executor(None, _fetch)
        if result:
            _set_cache("reddit", query, result)
        return result
    except Exception as e:
        logger.debug("reach_reddit: %s", e)
        return ""


async def reach_telegram(query: str, lang: str = "es") -> str:
    """Lee canales públicos de Telegram via Jina Reader."""
    cached = _get_cached("telegram", query)
    if cached:
        return cached

    loop = asyncio.get_running_loop()

    def _fetch():
        # Buscar canales públicos relacionados con la query
        encoded    = urllib.parse.quote(query)
        search_url = "https://tte.legra.ph/search?query=" + encoded
        try:
            text = _jina_fetch(search_url, timeout=12)
            if text and len(text) > 50:
                lines = [l.strip() for l in text.splitlines() if l.strip()]
                return "\n".join(lines[:25])[:1500]
        except Exception:
            pass
        # Fallback: buscar via DuckDuckGo + site:t.me
        ddg_url = (
            "https://html.duckduckgo.com/html/"
            "?q=site:t.me+" + encoded
        )
        text = _jina_fetch(ddg_url, timeout=12)
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        return "\n".join(lines[:25])[:1500]

    try:
        result = await loop.run_in_executor(None, _fetch)
        if result:
            _set_cache("telegram", query, result)
        return result
    except Exception as e:
        logger.debug("reach_telegram: %s", e)
        return ""


async def reach_tiktok(query: str, lang: str = "es") -> str:
    """Busca videos en TikTok via Jina Reader (sin login)."""
    cached = _get_cached("tiktok", query)
    if cached:
        return cached

    loop = asyncio.get_running_loop()

    def _fetch():
        encoded  = urllib.parse.quote(query)
        tt_url   = "https://www.tiktok.com/search?q=" + encoded
        try:
            text = _jina_fetch(tt_url, timeout=15)
            if text and len(text) > 50:
                lines = [l.strip() for l in text.splitlines()
                         if l.strip() and len(l) > 10]
                return "\n".join(lines[:30])[:1500]
        except Exception:
            pass
        # Fallback via DuckDuckGo
        ddg_url = (
            "https://html.duckduckgo.com/html/"
            "?q=site:tiktok.com+" + encoded
        )
        text = _jina_fetch(ddg_url, timeout=12)
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        return "\n".join(lines[:25])[:1500]

    try:
        result = await loop.run_in_executor(None, _fetch)
        if result:
            _set_cache("tiktok", query, result)
        return result
    except Exception as e:
        logger.debug("reach_tiktok: %s", e)
        return ""


async def reach_rss(url: str, lang: str = "es") -> str:
    """Lee cualquier feed RSS/Atom directamente."""
    cached = _get_cached("rss", url)
    if cached:
        return cached

    loop = asyncio.get_running_loop()

    def _fetch():
        import re
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Synergix-Bot/2.0"}
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read().decode("utf-8", errors="ignore")
        titles = re.findall(
            r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", raw
        )
        titles = [_html_mod.unescape(t.strip()) for t in titles if t.strip()][:8]
        return "\n".join("📰 " + t for t in titles)

    try:
        result = await loop.run_in_executor(None, _fetch)
        if result:
            _set_cache("rss", url, result)
        return result
    except Exception as e:
        logger.debug("reach_rss: %s", e)
        return ""


# ═══════════════════════════════════════════════════════════════════════════════
# ORCHESTRADOR PRINCIPAL
# ═══════════════════════════════════════════════════════════════════════════════

# Plataformas activas por defecto (sin proxy en Hetzner)
DEFAULT_PLATFORMS = ["web", "youtube", "github", "twitter", "reddit"]
# Plataformas con cookies opcionales (añadir según disponibilidad)
EXTRA_PLATFORMS   = ["telegram", "tiktok"]


async def reach_internet(
    query:     str,
    lang:      str  = "es",
    platforms: list = None,
    max_time:  float = 20.0,
) -> str:
    """
    Busca en múltiples plataformas en paralelo y combina los resultados.
    Se activa en MODO B (sin datos en memoria inmortal de Greenfield).

    Args:
        query:     Consulta a buscar
        lang:      Idioma del usuario (es/en/zh/zht)
        platforms: Lista de plataformas. Default: web+youtube+github+twitter+reddit
        max_time:  Timeout global en segundos

    Returns:
        String con resultados combinados, vacío si todos fallaron
    """
    if platforms is None:
        platforms = DEFAULT_PLATFORMS + EXTRA_PLATFORMS

    platform_map = {
        "web":      reach_web,
        "youtube":  reach_youtube,
        "github":   reach_github,
        "twitter":  reach_twitter,
        "reddit":   reach_reddit,
        "telegram": reach_telegram,
        "tiktok":   reach_tiktok,
    }

    platform_icons = {
        "web":      "🌐 Web",
        "youtube":  "📺 YouTube",
        "github":   "📦 GitHub",
        "twitter":  "🐦 Twitter/X",
        "reddit":   "🔴 Reddit",
        "telegram": "✈️ Telegram",
        "tiktok":   "🎵 TikTok",
    }

    tasks = []
    names = []
    for p in platforms:
        if p in platform_map:
            tasks.append(platform_map[p](query, lang))
            names.append(platform_icons.get(p, p))

    if not tasks:
        return ""

    # Ejecutar en paralelo con timeout global
    try:
        results = await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True),
            timeout=max_time,
        )
    except asyncio.TimeoutError:
        results = [""] * len(tasks)

    # Combinar resultados no vacíos
    combined = []
    for name, result in zip(names, results):
        if isinstance(result, str) and result.strip() and len(result) > 30:
            combined.append(
                "--- " + name + " ---\n" + result.strip()[:800]
            )

    if not combined:
        return ""

    logger.info(
        "🌐 Agent-Reach: %d/%d plataformas respondieron para '%s...'",
        len(combined), len(tasks), query[:30]
    )
    return "\n\n".join(combined)


# ── Detector de intención de búsqueda en internet ─────────────────────────────
def needs_internet_search(query: str, has_rag_data: bool) -> bool:
    """
    Determina si la query necesita búsqueda en internet.
    Se activa cuando:
    1. La memoria inmortal no tiene datos (MODO B), O
    2. El usuario pide explícitamente buscar online
    """
    if not has_rag_data:
        return True  # MODO B: siempre buscar si no hay memoria

    # Palabras clave que indican búsqueda explícita
    SEARCH_TRIGGERS = {
        "es": ["busca", "buscar", "busco", "googlea", "internet",
               "twitter", "reddit", "youtube", "noticias", "tendencia",
               "últimas", "reciente", "hoy", "ahora", "tiktok", "redes"],
        "en": ["search", "find online", "look up", "google", "internet",
               "twitter", "reddit", "youtube", "news", "trending",
               "latest", "recent", "today", "now", "tiktok", "social"],
        "zh": ["搜索", "查找", "谷歌", "推特", "油管", "最新", "今天"],
        "zht":["搜索", "查找", "谷歌", "推特", "油管", "最新", "今天"],
    }
    q_lower = query.lower()
    for lang_triggers in SEARCH_TRIGGERS.values():
        if any(t in q_lower for t in lang_triggers):
            return True

    return False


# ── Detector de intención mejorado ────────────────────────────────────────────
def detect_reach_intent(text: str, has_rag_data: bool, msg_type: str) -> tuple:
    """
    Detecta si la query necesita búsqueda en redes sociales en tiempo real.
    Retorna (should_search: bool, platforms: list)

    Lógica:
    - Siempre busca si no hay datos en memoria inmortal (MODO B)
    - Busca si la query menciona redes sociales, noticias, tendencias
    - Busca si pregunta por algo que cambia en tiempo real
    - NO busca en saludos, emojis, preguntas muy simples
    """
    t = text.lower().strip()

    if msg_type in ("sticker", "simple"):
        return False, []

    platform_triggers = {
        "twitter":  ["twitter", "x.com", "tweet", "tuit", "trending x", "tendencia twitter",
                     "𝕏", "elon", "twit"],
        "youtube":  ["youtube", "youtu.be", "video", "vídeo", "canal youtube", "yt",
                     "shorts", "youtuber"],
        "reddit":   ["reddit", "subreddit", "r/", "post reddit"],
        "github":   ["github", "repositorio", "repo", "código fuente", "open source",
                     "pull request", "issue", "commit"],
        "telegram": ["telegram", "canal telegram", "t.me", "grupo telegram", "tg",
                     "channel telegram"],
        "tiktok":   ["tiktok", "tik tok", "viral tiktok", "tt", "tok"],
        "web":      ["noticia", "noticias", "news", "artículo", "blog", "web", "página",
                     "busca en", "search for", "find online", "precio", "price",
                     "google", "wikipedia", "sitio", "site"],
    }

    realtime_triggers = [
        "ahora", "hoy", "último", "últimas", "reciente", "recientes",
        "tendencia", "tendencias", "viral", "trending", "now", "today",
        "latest", "recent", "news", "noticias", "2024", "2025", "2026",
        "esta semana", "este mes", "this week", "this month", "live", "en vivo",
        "最新", "今天", "现在", "趋势", "最近", "direct", "directo",
        "en directo", "breaking", "tiempo real", "real time",
    ]

    internet_topics = [
        "precio de", "price of", "cotización", "rate", "valor actual",
        "quién es", "who is", "qué pasó", "what happened",
        "cuándo fue", "cuando fue", "when was", "dónde está", "where is",
        "review", "opinión sobre", "opinion about", "comparar", "compare",
        "vs", "mejor que", "better than", "cuánto vale", "how much",
        "cuánto cuesta", "how much does", "dónde comprar", "where to buy",
    ]

    # Detectar plataformas explícitas
    platforms_to_search = []
    for platform, triggers in platform_triggers.items():
        if any(tr in t for tr in triggers):
            platforms_to_search.append(platform)

    if platforms_to_search:
        return True, platforms_to_search

    if any(tr in t for tr in realtime_triggers):
        return True, ["web", "twitter", "youtube", "reddit", "telegram", "tiktok"]

    if any(tr in t for tr in internet_topics):
        return True, ["web", "twitter", "reddit", "youtube"]

    if not has_rag_data:
        return True, ["web", "twitter", "youtube", "github", "reddit", "telegram", "tiktok"]

    return False, []
