"""
Motor RAG cross‑lingual para Synergix.
Usa SentenceTransformer multilingual‑e5‑small y FAISS con cuantización PQ.
Retorna contexto relevante y lista de author_uids_ofuscados para regalías.
"""

import asyncio
import json
import logging
import pickle
import tempfile
from typing import Dict, List, Optional, Tuple

import faiss
import numpy as np
import torch
from sentence_transformers import SentenceTransformer
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from aisynergix.services.greenfield import get_object, list_objects
from config import cfg

logger = logging.getLogger("synergix.rag")

# ──────────────────────────────────────────────────────────────────────────────
# MODELO MULTILINGUAL Y FAISS
# ──────────────────────────────────────────────────────────────────────────────

class MultilingualRAG:
    """
    Motor de búsqueda semántica que maneja español, inglés, chino simplificado y tradicional.
    Los vectores se almacenan en FAISS con cuantización de producto (PQ) para eficiencia RAM.
    """

    def __init__(self, model_name: str = "intfloat/multilingual-e5-small"):
        self.model_name = model_name
        self.model = None
        self.index = None
        self.metadata = []  # cada elemento: {"text": str, "author_uid": str, "lang": str, "object_path": str}
        self.dimension = 384  # dimensión de multilingual‑e5‑small
        self.similarity_threshold = 0.92
        self._initialized = False

    async def initialize(self) -> None:
        """
        Carga el modelo SentenceTransformer y el índice FAISS desde Greenfield.
        Si no existe un índice en Greenfield, crea uno vacío.
        """
        if self._initialized:
            return
        logger.info("🔄 Inicializando RAG multilingual...")
        # Cargar modelo con float16 para ahorrar RAM
        self.model = SentenceTransformer(
            self.model_name,
            model_kwargs={"torch_dtype": torch.float16},
            device="cpu",
        )
        self.model.eval()
        # Intentar cargar índice desde Greenfield
        await self._load_or_create_index()
        self._initialized = True
        logger.info("✅ RAG listo. Índice con %d vectores.", self.index.ntotal)

    async def _load_or_create_index(self) -> None:
        """Carga el índice FAISS desde aisynergix/data/brains/latest.index."""
        try:
            # Leer puntero a la última versión
            pointer_data, _ = await get_object("aisynergix/data/brain_pointer")
            pointer = json.loads(pointer_data.decode("utf-8"))
            latest_version = pointer.get("latest_v", "v1")
            index_path = f"aisynergix/data/brains/{latest_version}.index"
            meta_path = f"aisynergix/data/brains/{latest_version}.meta"
            # Descargar índice y metadatos
            index_bytes, _ = await get_object(index_path)
            meta_bytes, _ = await get_object(meta_path)
            # Cargar en memoria
            with tempfile.NamedTemporaryFile(suffix=".index", delete=False) as tmp_idx:
                tmp_idx.write(index_bytes)
                tmp_idx.flush()
                self.index = faiss.read_index(tmp_idx.name)
            self.metadata = pickle.loads(meta_bytes)
            logger.info("📥 Índice cargado desde Greenfield: %s (%d vectores)", latest_version, self.index.ntotal)
        except Exception as e:
            logger.warning("No se pudo cargar índice desde Greenfield: %s. Creando nuevo.", e)
            self._create_empty_index()

    def _create_empty_index(self) -> None:
        """Crea un índice FAISS vacío con cuantización PQ."""
        # Configurar PQ para compresión: 384 dim → 96 subvectores de 4 dim
        m = 96  # número de subvectores (384 / 4)
        self.index = faiss.IndexPQ(self.dimension, m, 8, faiss.METRIC_INNER_PRODUCT)
        self.metadata = []
        logger.info("🆕 Índice FAISS vacío creado (PQ %dx8)", m)

    async def add_document(
        self, text: str, author_uid: str, lang: str, object_path: str
    ) -> bool:
        """
        Vectoriza un nuevo documento y lo añade al índice en memoria.
        Retorna True si el documento era suficientemente único (<0.92 similitud).
        """
        await self.initialize()
        # Calcular embedding
        with torch.no_grad():
            embedding = self.model.encode(
                text,
                convert_to_tensor=True,
                normalize_embeddings=True,
                device="cpu",
            )
        embedding_np = embedding.cpu().numpy().astype(np.float32).reshape(1, -1)
        # Verificar similitud máxima
        if self.index.ntotal > 0:
            distances, _ = self.index.search(embedding_np, k=1)
            similarity = 1.0 - distances[0][0]  # asumimos distancia coseno
            if similarity >= self.similarity_threshold:
                logger.debug("📝 Documento demasiado similar (%.3f), omitido", similarity)
                return False
        # Añadir al índice
        self.index.add(embedding_np)
        self.metadata.append({
            "text": text,
            "author_uid": author_uid,
            "lang": lang,
            "object_path": object_path,
        })
        logger.debug("➕ Documento añadido al RAG: %s (autor=%s)", object_path, author_uid)
        return True

    async def search(
        self, query: str, k: int = 5, min_similarity: float = 0.5
    ) -> Tuple[List[str], List[str]]:
        """
        Busca los k documentos más similares a la consulta.
        Retorna:
          - contexts: lista de textos relevantes
          - author_uids: lista de author_uids_ofuscados correspondientes
        """
        await self.initialize()
        if self.index.ntotal == 0:
            return [], []
        # Embedding de la consulta
        with torch.no_grad():
            query_embedding = self.model.encode(
                query,
                convert_to_tensor=True,
                normalize_embeddings=True,
                device="cpu",
            )
        query_np = query_embedding.cpu().numpy().astype(np.float32).reshape(1, -1)
        # Búsqueda en FAISS
        actual_k = min(k, self.index.ntotal)
        distances, indices = self.index.search(query_np, actual_k)
        # Filtrar por similitud mínima
        contexts = []
        author_uids = []
        for dist, idx in zip(distances[0], indices[0]):
            if idx < 0 or idx >= len(self.metadata):
                continue
            similarity = 1.0 - dist
            if similarity < min_similarity:
                continue
            meta = self.metadata[idx]
            contexts.append(meta["text"])
            author_uids.append(meta["author_uid"])
        logger.debug("🔍 RAG: consulta '%s' → %d resultados (sim≥%.2f)", query[:50], len(contexts), min_similarity)
        return contexts, author_uids

    async def detect_query_language(self, query: str) -> str:
        """
        Detecta el idioma de la consulta (simple heurística).
        Retorna uno de: 'es', 'en', 'zh-hans', 'zh-hant'.
        """
        # Heurística básica basada en caracteres
        # En producción podríamos usar langdetect o similar
        query_lower = query.lower()
        if any(c in query_lower for c in ['的', '是', '在', '有', '了']):
            return 'zh-hans'
        if any(c in query_lower for c in ['的', '是', '在', '有', '了']):
            # Diferenciar simplificado/tradicional es complejo; usamos simplificado por defecto
            return 'zh-hans'
        if any(word in query_lower for word in ['the', 'and', 'is', 'are', 'you']):
            return 'en'
        if any(word in query_lower for word in ['el', 'la', 'los', 'las', 'que']):
            return 'es'
        # Por defecto español
        return 'es'

    async def get_stats(self) -> Dict[str, any]:
        """Retorna estadísticas del índice RAG."""
        await self.initialize()
        return {
            "total_documents": self.index.ntotal,
            "dimension": self.dimension,
            "is_trained": self.index.is_trained,
            "metadata_count": len(self.metadata),
        }


# ──────────────────────────────────────────────────────────────────────────────
# INSTANCIA GLOBAL
# ──────────────────────────────────────────────────────────────────────────────

_rag_instance: Optional[MultilingualRAG] = None

async def get_rag() -> MultilingualRAG:
    """Devuelve la instancia única del motor RAG."""
    global _rag_instance
    if _rag_instance is None:
        _rag_instance = MultilingualRAG()
        await _rag_instance.initialize()
    return _rag_instance


# ──────────────────────────────────────────────────────────────────────────────
# FUNCIONES DE CONVENIENCIA
# ──────────────────────────────────────────────────────────────────────────────

async def rag_search(
    query: str, k: int = 5, min_similarity: float = 0.5
) -> Tuple[List[str], List[str]]:
    """
    Busca en el RAG y retorna (contextos, author_uids).
    Esta es la función principal que usará el manager.py.
    """
    rag = await get_rag()
    return await rag.search(query, k, min_similarity)


async def rag_add_document(
    text: str, author_uid: str, lang: str, object_path: str
) -> bool:
    """Añade un documento al RAG (usado por fusion_brain.py)."""
    rag = await get_rag()
    return await rag.add_document(text, author_uid, lang, object_path)


async def detect_language(query: str) -> str:
    """Detecta el idioma de una consulta."""
    rag = await get_rag()
    return await rag.detect_query_language(query)
