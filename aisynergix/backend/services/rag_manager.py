# aisynergix/backend/services/rag_manager.py
"""
RAG Manager: Búsqueda inteligente de aportes en BNB Greenfield.
Flujo: list aportes/ → HEAD metadata filter (quality >= 5) →
Groq relevance scoring → download top-k → inyectar en system prompt.
Cache local hot_cache.json para performance.
"""

import asyncio
import json
import logging
import os
import time
from typing import Any, Optional

from aisynergix.backend.services.greenfield import greenfield_client


logger = logging.getLogger("synergix.rag_manager")

HOT_CACHE_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "..", "aisynergix", "data", "hot_cache.json")
CACHE_TTL_SECONDS = 480          # 8 minutos (sincronizado con federation loop)
MAX_RAG_CONTEXT_CHARS = 1500     # máx chars para inyectar en prompt
TOP_K = 5                        # top aportes a inyectar


class RAGManager:
    """
    Gestor de Retrieval-Augmented Generation para Synergix.
    Cache en hot_cache.json. Actualización automática.
    """

    def __init__(self) -> None:
        self._cache: dict[str, Any] = {}
        self._cache_ts: float = 0.0
        self._lock = asyncio.Lock()
        self._load_hot_cache()
        logger.info("🔍 RAGManager inicializado")

    # ── Cache Management ──────────────────────────────────────────────────────

    def _load_hot_cache(self) -> None:
        """Carga el hot_cache.json al iniciar."""
        try:
            os.makedirs(os.path.dirname(HOT_CACHE_PATH), exist_ok=True)
            if os.path.exists(HOT_CACHE_PATH):
                with open(HOT_CACHE_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._cache = data.get("metadata_cache", {})
                    self._cache_ts = data.get("last_update_ts", 0.0)
                    logger.info("📂 hot_cache cargado: %d aportes", len(self._cache))
        except Exception as exc:
            logger.warning("⚠️ No se pudo cargar hot_cache: %s", exc)
            self._cache = {}

    def _save_hot_cache(self, metadata_list: list[dict]) -> None:
        """Persiste el cache actualizado."""
        try:
            os.makedirs(os.path.dirname(HOT_CACHE_PATH), exist_ok=True)
            data = {
                "last_update_ts": self._cache_ts,
                "last_update_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "count": len(metadata_list),
                "metadata_cache": {m["object_name"]: m for m in metadata_list if "object_name" in m},
                "wisdom_snippets": [
                    {"name": m["object_name"], "summary": m.get("summary", ""), "score": m.get("quality_score", 0)}
                    for m in metadata_list[:20]
                ]
            }
            with open(HOT_CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info("💾 hot_cache guardado: %d aportes", len(metadata_list))
        except Exception as exc:
            logger.error("❌ Error guardando hot_cache: %s", exc)

    def _is_cache_stale(self) -> bool:
        return (time.monotonic() - self._cache_ts) > CACHE_TTL_SECONDS

    # ── Refresh Metadata Cache ────────────────────────────────────────────────

    async def refresh_cache(self, force: bool = False) -> None:
        """
        Actualiza el cache de metadatos desde Greenfield.
        Solo si el cache está expirado o force=True.
        """
        if not force and not self._is_cache_stale():
            return

        async with self._lock:
            # Double-check dentro del lock
            if not force and not self._is_cache_stale():
                return

            logger.info("🔄 Actualizando cache RAG desde Greenfield...")
            try:
                metadata_list = await greenfield_client.get_aportes_metadata(
                    prefix="aisynergix/aportes/",
                    min_quality=5,
                    max_items=50,
                )
                self._cache = {m["object_name"]: m for m in metadata_list if "object_name" in m}
                self._cache_ts = time.monotonic()
                self._save_hot_cache(metadata_list)
                logger.info("✅ Cache RAG actualizado: %d aportes de calidad", len(self._cache))
            except Exception as exc:
                logger.error("❌ Error actualizando cache RAG: %s", exc)

    # ── Búsqueda Inteligente ──────────────────────────────────────────────────

    async def search_relevant(
        self,
        question: str,
        lang: str = "es",
        top_k: int = TOP_K,
    ) -> list[dict[str, Any]]:
        """
        Busca aportes relevantes para una pregunta.
        1. Refresca cache si expirado.
        2. Filtrado por keywords (fast path).
        3. Groq scoring para ranking final.

        Args:
            question: Pregunta o texto del usuario.
            lang: Idioma para contexto.
            top_k: Número de resultados a retornar.

        Returns:
            Lista de metadatos de los aportes más relevantes.
        """
        await self.refresh_cache()

        if not self._cache:
            logger.info("📭 RAG cache vacío, sin contexto adicional")
            return []

        candidates = list(self._cache.values())

        # ── Fast Path: keyword filter ─────────────────────────────────────────
        keywords = self._extract_keywords(question)
        if keywords:
            scored = []
            for meta in candidates:
                summary = (meta.get("summary", "") + " " + meta.get("knowledge_tag", "")).lower()
                keyword_hits = sum(1 for kw in keywords if kw in summary)
                if keyword_hits > 0:
                    scored.append((keyword_hits, meta))
            scored.sort(key=lambda x: (-x[0], -x[1].get("quality_score", 0)))
            pre_filtered = [m for _, m in scored[:min(15, len(scored))]]
        else:
            # Si no hay keywords, tomar los de mayor calidad
            pre_filtered = sorted(candidates, key=lambda x: -x.get("quality_score", 0))[:15]

        if not pre_filtered:
            return []

        # Qwen 1.5B: usar solo keyword rank (más rápido, sin scoring extra)
        result = pre_filtered[:top_k]

        logger.info("🔍 RAG encontró %d aportes relevantes para: '%s...'", len(result), question[:40])
        return result

    # ── Download y Build Context ──────────────────────────────────────────────

    async def build_rag_context(
        self,
        question: str,
        lang: str = "es",
    ) -> str:
        """
        Construye el contexto RAG completo para inyectar en el system prompt.
        Descarga contenido de los aportes más relevantes.

        Args:
            question: Pregunta del usuario.
            lang: Idioma activo.

        Returns:
            String de contexto o "" si no hay aportes relevantes.
        """
        relevant = await self.search_relevant(question, lang=lang, top_k=TOP_K)
        if not relevant:
            return ""

        context_parts: list[str] = []
        total_chars = 0

        # Descargar contenido de los aportes más relevantes (máx 3 downloads para velocidad)
        download_limit = 3
        for i, meta in enumerate(relevant):
            if total_chars >= MAX_RAG_CONTEXT_CHARS:
                break

            object_name = meta.get("object_name", "")
            summary = meta.get("summary", "")

            if not summary and not object_name:
                continue

            # Para los primeros 3, descargamos el contenido completo
            if i < download_limit and object_name:
                try:
                    content = await greenfield_client.get_object(object_name)
                    if content:
                        snippet = content[:400]
                        context_parts.append(f"[Aporte #{i+1}] {snippet}")
                        total_chars += len(snippet)
                        # Registrar impacto
                        asyncio.create_task(greenfield_client.increment_impact(object_name))
                        continue
                except Exception as exc:
                    logger.warning("⚠️ No se pudo descargar %s: %s", object_name, exc)

            # Fallback: usar solo el summary del metadata
            if summary:
                context_parts.append(f"[Aporte #{i+1}] {summary}")
                total_chars += len(summary)

        if not context_parts:
            return ""

        return "\n".join(context_parts)

    # ── Inject RAG into System Prompt ─────────────────────────────────────────

    async def inject_into_prompt(
        self,
        base_system: str,
        question: str,
        lang: str = "es",
        rag_template: str = "",
    ) -> str:
        """
        Enriquece el system prompt con contexto RAG.

        Args:
            base_system: System prompt base de Synergix.
            question: Pregunta del usuario.
            lang: Idioma activo.
            rag_template: Template de inyección desde system_prompts.json.

        Returns:
            System prompt enriquecido con contexto RAG.
        """
        rag_context = await self.build_rag_context(question, lang=lang)

        if not rag_context:
            return base_system

        if rag_template:
            rag_section = rag_template.format(rag_context=rag_context)
        else:
            rag_section = f"\nCONOCIMIENTO COMUNIDAD SYNERGIX:\n{rag_context}\n"

        return f"{base_system}\n\n{rag_section}"

    # ── Add to Cache (on upload) ──────────────────────────────────────────────

    def add_to_cache(self, object_name: str, metadata: dict) -> None:
        """
        Agrega un nuevo aporte al cache inmediatamente tras subida exitosa.
        Evita esperar el próximo ciclo de refresh.
        """
        self._cache[object_name] = {
            "object_name": object_name,
            **metadata,
        }
        logger.info("📌 Aporte añadido al cache RAG: %s", object_name)

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_keywords(text: str) -> list[str]:
        """Extrae keywords básicas del texto (stop-word removal simple)."""
        stop_words = {
            "el", "la", "los", "las", "un", "una", "de", "del", "en", "que",
            "es", "y", "a", "con", "por", "para", "como", "su", "se", "me",
            "the", "a", "an", "is", "are", "in", "on", "at", "to", "for",
            "what", "how", "why", "when", "where", "who",
        }
        words = text.lower().split()
        keywords = [w for w in words if len(w) > 3 and w not in stop_words]
        return list(set(keywords))[:10]

    def get_cache_stats(self) -> dict[str, Any]:
        """Retorna estadísticas del cache para monitoreo."""
        return {
            "cached_aportes": len(self._cache),
            "cache_age_seconds": int(time.monotonic() - self._cache_ts),
            "cache_stale": self._is_cache_stale(),
            "ttl_seconds": CACHE_TTL_SECONDS,
        }


# ── Instancia global singleton ─────────────────────────────────────────────────
rag_manager = RAGManager()
