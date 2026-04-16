"""
rag_engine.py — Motor FAISS de Recuperación de Contexto (Float16).
Maneja la vectorización y persistencia local sincronizada con DCellar.
"""

import os
import json
import logging
import numpy as np
from typing import List, Dict, Any, Tuple
from sentence_transformers import SentenceTransformer
import faiss

from aisynergix.config.constants import (
    EMBEDDING_MODEL,
    LOCAL_BRAIN_DIR,
    RAG_TOP_K
)

logger = logging.getLogger(__name__)

class RAGEngine:
    def __init__(self):
        logger.info(f"[RAG] Inicializando modelo de embeddings: {EMBEDDING_MODEL}")
        # Cargar modelo. Usa CPU por defecto, optimizado para ARM64
        self.encoder = SentenceTransformer(EMBEDDING_MODEL)
        self.dimension = self.encoder.get_sentence_embedding_dimension()
        
        # FAISS index (IndexFlatIP para similaridad del coseno asumiendo vectores normalizados)
        self.index: faiss.Index = faiss.IndexFlatIP(self.dimension)
        self.metadata: List[Dict[str, Any]] = []
        
        self.index_path = os.path.join(LOCAL_BRAIN_DIR, "brain.index")
        self.meta_path = os.path.join(LOCAL_BRAIN_DIR, "brain_meta.json")
        
        os.makedirs(LOCAL_BRAIN_DIR, exist_ok=True)
        self.load_local_index()

    def rebuild_index(self, aportes: List[Dict[str, Any]]):
        """Reconstruye el índice FAISS desde cero con nuevos datos de calidad."""
        if not aportes:
            return

        logger.info(f"[RAG] Reconstruyendo índice con {len(aportes)} aportes.")
        texts = [a["content"] for a in aportes]
        
        # Vectorizar y normalizar para usar Inner Product como Cosine Similarity
        embeddings = self.encoder.encode(texts, convert_to_numpy=True)
        faiss.normalize_L2(embeddings)
        
        # Convertir a float16 simulado (o mantener float32 para FAISS CPU standard, 
        # FAISS cpu en ARM típicamente maneja float32 nativo de forma eficiente)
        embeddings = np.float32(embeddings) 

        self.index = faiss.IndexFlatIP(self.dimension)
        self.index.add(embeddings)
        self.metadata = aportes

        self._save_local()
        logger.info(f"[RAG] Índice reconstruido. Total vectores: {self.index.ntotal}")

    def _save_local(self):
        """Guarda el índice y metadatos en el disco local temporalmente."""
        faiss.write_index(self.index, self.index_path)
        with open(self.meta_path, 'w', encoding='utf-8') as f:
            json.dump(self.metadata, f, ensure_ascii=False)

    def load_local_index(self):
        """Carga el índice local si existe."""
        if os.path.exists(self.index_path) and os.path.exists(self.meta_path):
            self.index = faiss.read_index(self.index_path)
            with open(self.meta_path, 'r', encoding='utf-8') as f:
                self.metadata = json.load(f)
            logger.info(f"[RAG] Índice local cargado. Vectores: {self.index.ntotal}")
        else:
            logger.warning("[RAG] No se encontró índice local. Se inicializa vacío.")

    def search(self, query: str, top_k: int = RAG_TOP_K) -> List[Dict[str, Any]]:
        """Busca en el índice y devuelve texto y author_uid."""
        if self.index.ntotal == 0:
            return []

        query_vector = self.encoder.encode([query], convert_to_numpy=True)
        faiss.normalize_L2(query_vector)
        query_vector = np.float32(query_vector)

        k = min(top_k, self.index.ntotal)
        distances, indices = self.index.search(query_vector, k)

        results = []
        for i, idx in enumerate(indices[0]):
            if idx == -1:
                continue
            item_meta = self.metadata[idx]
            results.append({
                "content": item_meta["content"],
                "author_uid": item_meta.get("author_uid", "unknown"),
                "score": float(distances[0][i])
            })
            
        return results

# Instancia global (Singleton)
rag_engine = RAGEngine()
