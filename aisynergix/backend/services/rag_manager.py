"""
aisynergix/backend/services/rag_manager.py
══════════════════════════════════════════════════════════════════════════════
RAG Manager — Motor de Recuperación y Generación Aumentada.

"Ejecuta FAISS y otorga regalías (+1 punto al autor cuando su conocimiento
es usado para responder una pregunta)."
— Documento Maestro Synergix

Flujo:
  1. refresh_cache() — carga metadatos de aportes desde Greenfield (cada 8 min)
  2. search_relevant() — búsqueda por keywords multilingüe
  3. build_context() — inyecta los fragmentos más relevantes en el prompt
  4. award_royalties() — +1 pto al autor + notificación Telegram

Cache local (hot_cache.json) para velocidad extrema:
  - Metadatos de aportes en RAM (sin descargar contenido)
  - TTL: 8 min (sincronizado con federation_loop)
  - Rebuild automático si stale
══════════════════════════════════════════════════════════════════════════════
"""

import asyncio
import json
import logging
import math
import os
import time
from typing import Any, Optional

logger = logging.getLogger("synergix.rag")

# Importación lazy para evitar circular imports
_greenfield_client = None

def _get_gf_client():
    global _greenfield_client
    if _greenfield_client is None:
        from aisynergix.backend.services.greenfield import greenfield_client
        _greenfield_client = greenfield_client
    return _greenfield_client

# ── Config ────────────────────────────────────────────────────────────────────
_HERE           = os.path.dirname(os.path.abspath(__file__))
HOT_CACHE_PATH  = os.path.join(_HERE, "..", "..", "data", "hot_cache.json")
CACHE_TTL       = 480    # 8 min
MAX_CONTEXT     = 1800   # chars máx para inyectar en prompt
TOP_K           = 5      # top aportes a retornar

# Keywords de Synergix para boost cross-language en RAG
_SYNERGIX_TERMS = frozenset({
    "synergix","greenfield","bnb","dcellar","blockchain","defi","web3",
    "ia","ai","colmena","hive","memoria","memory","wisdom","知识","智慧",
    "区块链","去中心化","記憶","區塊鏈","人工智能","人工智慧",
})

# Stop words multilingüe
_STOP_WORDS = frozenset({
    "el","la","los","las","un","una","de","del","en","que","es","y","a",
    "con","por","para","como","se","su","al","lo","this","the","a","an",
    "is","are","in","on","to","for","of","and","or","it","有","的","是",
    "在","和","了","我","你","我","他","她","它","们",
})


# ══════════════════════════════════════════════════════════════════════════════
class RAGManager:
    """
    Motor RAG de Synergix — keyword search + impact royalties.
    Cache en memoria (hot_cache.json) para latencia <1ms en búsquedas.
    """

    def __init__(self) -> None:
        self._cache:    dict[str, Any] = {}
        self._cache_ts: float          = 0.0
        self._lock                     = asyncio.Lock()
        self._load_hot_cache()
        logger.info("🔍 RAGManager inicializado — %d aportes en cache",
                    len(self._cache))

    # ── Cache Management ──────────────────────────────────────────────────────
    def _load_hot_cache(self) -> None:
        try:
            os.makedirs(os.path.dirname(HOT_CACHE_PATH), exist_ok=True)
            if os.path.exists(HOT_CACHE_PATH):
                with open(HOT_CACHE_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._cache    = data.get("metadata_cache", {})
                self._cache_ts = data.get("last_update_ts", 0.0)
        except Exception as e:
            logger.warning("⚠️ hot_cache load: %s", e)
            self._cache = {}

    def _save_hot_cache(self, metadata_list: list[dict]) -> None:
        try:
            os.makedirs(os.path.dirname(HOT_CACHE_PATH), exist_ok=True)
            data = {
                "last_update_ts":  self._cache_ts,
                "last_update_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "count":           len(metadata_list),
                "metadata_cache":  {m["object_name"]: m for m in metadata_list
                                    if "object_name" in m},
                "wisdom_snippets": [
                    {"name": m["object_name"],
                     "summary": m.get("ai-summary",""),
                     "score": m.get("quality-score",0)}
                    for m in metadata_list[:20]
                ],
            }
            with open(HOT_CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error("❌ hot_cache save: %s", e)

    def _is_stale(self) -> bool:
        return (time.monotonic() - self._cache_ts) > CACHE_TTL

    # ── Refresh desde Greenfield ──────────────────────────────────────────────
    async def refresh_cache(self, force: bool = False) -> None:
        """Actualiza el cache de metadatos desde Greenfield."""
        if not force and not self._is_stale():
            return
        async with self._lock:
            if not force and not self._is_stale():
                return
            logger.info("🔄 RAG cache refresh...")
            try:
                gf = _get_gf_client()
                metadata_list = await gf.get_aportes_metadata(
                    prefix=f"aisynergix/aportes/",
                    min_quality=5,
                    max_items=50,
                )
                self._cache    = {m["object_name"]: m for m in metadata_list
                                   if "object_name" in m}
                self._cache_ts = time.monotonic()
                self._save_hot_cache(metadata_list)
                logger.info("✅ RAG cache: %d aportes de calidad", len(self._cache))
            except Exception as e:
                logger.error("❌ RAG refresh: %s", e)

    # ── Búsqueda por Keywords ─────────────────────────────────────────────────
    def _kw_score(self, text: str, query: str) -> float:
        """
        Score de relevancia keyword-based.
        Multilingüe: boost para términos de Synergix en cualquier idioma.
        """
        q_lower = query.lower().replace("?","").replace("¿","").replace("？","")
        q_words = {w for w in q_lower.split() if len(w) > 1 and w not in _STOP_WORDS}
        if not q_words:
            return 0.0

        t_lower = text.lower()
        hits    = sum(1 for w in q_words if w in t_lower)
        score   = hits / len(q_words)

        # Boost cross-language para términos Synergix
        if any(t in q_lower for t in _SYNERGIX_TERMS):
            if any(t in t_lower for t in _SYNERGIX_TERMS):
                score = max(score, 0.4)

        # Boost si hay hits con pocas keywords
        if hits >= 1 and len(q_words) <= 2:
            score = max(score, 0.35)

        return min(score, 1.0)

    async def search_relevant(
        self,
        question: str,
        lang:     str = "es",
        top_k:    int = TOP_K,
    ) -> list[dict[str, Any]]:
        """
        Busca los aportes más relevantes para una pregunta.

        Returns:
            Lista de metadatos ordenados por relevancia combinada.
        """
        await self.refresh_cache()
        if not self._cache:
            return []

        scored = []
        for meta in self._cache.values():
            txt = meta.get("ai-summary","") + " " + meta.get("knowledge-tag","")
            kw  = self._kw_score(txt, question)
            if kw < 0.02:
                continue

            q_score = meta.get("quality-score", 5) / 10.0
            fw      = float(str(meta.get("fusion_weight",1.0)).split("|")[0])
            impact  = 1.0 + math.log(meta.get("impact", 0) + 1) * 0.1
            lang_b  = 1.05 if meta.get("lang","es") == lang else 1.0
            ts      = meta.get("ts", 0)
            age_d   = (time.time() - ts) / 86400 if ts else 365
            recency = max(0.8, 1.0 - (age_d / 365) * 0.2)

            relevance = kw * q_score * fw * impact * lang_b * recency
            scored.append({**meta, "_relevance": relevance})

        scored.sort(key=lambda x: -x["_relevance"])
        result = scored[:top_k]
        logger.info("🔍 RAG '%s...' → %d/%d resultados",
                    question[:25], len(result), len(scored))
        return result

    # ── Build Context para el Prompt ──────────────────────────────────────────
    async def build_context(
        self,
        question:   str,
        lang:       str = "es",
        brain_text: str = "",
    ) -> tuple[str, list[str]]:
        """
        Construye el contexto completo para inyectar en el system prompt.

        Returns:
            (context_string, [object_names_used])
        """
        results = await self.search_relevant(question, lang=lang)
        used    = []
        parts   = []

        # Sección del cerebro fusionado
        if brain_text:
            brain_sect = ""
            for marker in ["=== CONOCIMIENTO FUSIONADO", "=== FUSED KNOWLEDGE", "=== 融合知识"]:
                if marker in brain_text:
                    after = brain_text.split(marker, 1)[1]
                    end   = after.find("===")
                    brain_sect = (after[:end] if end > -1 else after)[:1200].strip()
                    break
            if not brain_sect:
                brain_sect = brain_text[:1200]

            labels = {
                "es": "Conocimiento fusionado Synergix:\n",
                "en": "Synergix fused knowledge:\n",
                "zh_cn": "Synergix融合知识：\n",
                "zh": "Synergix融合知識：\n",
            }
            parts.append(labels.get(lang, labels["en"]) + brain_sect)

        # Aportes relevantes de la comunidad
        for r in results:
            summary  = r.get("ai-summary","")
            obj_name = r.get("object_name","")
            if not summary:
                continue
            tag     = r.get("knowledge-tag","general")
            rel_pct = int(r.get("_relevance",0) * 100)
            parts.append(f"[{tag}|{rel_pct}%]\n{summary}")
            used.append(obj_name)

        context = "\n\n".join(parts)
        return context[:MAX_CONTEXT], [u for u in used if u]

    # ── Add to Cache ──────────────────────────────────────────────────────────
    def add_to_cache(self, object_name: str, metadata: dict) -> None:
        """Añade un nuevo aporte al cache inmediatamente después de subirlo."""
        self._cache[object_name] = {"object_name": object_name, **metadata}
        logger.info("📌 RAG cache: añadido %s", object_name)

    # ── Award Royalties ───────────────────────────────────────────────────────
    async def award_royalties(
        self,
        used_objects: list[str],
        db:           dict,
        bot,
        save_db_fn,
    ) -> None:
        """
        Regalías: +1 punto al autor cuando su aporte es usado por la IA.
        Notificación silenciosa por Telegram.
        """
        from aisynergix.config.constants import TRANSLATIONS as T
        for obj in used_objects:
            meta = self._cache.get(obj)
            if not meta:
                continue
            author_uid_s = meta.get("user-id","").split("|")[0]
            if not author_uid_s:
                continue

            # Actualizar impacto en cache
            self._cache[obj]["impact"] = meta.get("impact",0) + 1

            # Actualizar puntos en DB local
            if author_uid_s in db.get("reputation",{}):
                db["reputation"][author_uid_s]["impact"] = (
                    db["reputation"][author_uid_s].get("impact",0) + 1
                )
                db["reputation"][author_uid_s]["points"] = (
                    db["reputation"][author_uid_s].get("points",0) + 1
                )
                save_db_fn()

                # Notificación al autor
                if author_uid_s.isdigit():
                    uid_a = int(author_uid_s)
                    lang_a = db.get("user_settings",{}).get(author_uid_s,{}).get("lang","es")
                    try:
                        await bot.send_message(
                            uid_a,
                            T.get(lang_a, T["es"])["impact_reward"].format(pts=1)
                        )
                    except Exception:
                        pass

    def get_stats(self) -> dict:
        return {
            "cached_aportes":    len(self._cache),
            "cache_age_seconds": int(time.monotonic() - self._cache_ts),
            "cache_stale":       self._is_stale(),
            "ttl_seconds":       CACHE_TTL,
        }


# ── Singleton ─────────────────────────────────────────────────────────────────
rag_manager = RAGManager()
