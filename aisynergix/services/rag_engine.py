"""
rag_engine.py — Motor de recuperación (RAG) de Synergix.
Usa faiss-cpu y all-MiniLM-L6-v2 en float16 para búsqueda semántica.
"""

import os
import json
import logging
import faiss
import numpy as np
from sentence_transformers import SentenceTransformer
from typing import List, Tuple, Dict, Any

from aisynergix.config.constants import (
    LOCAL_BRAIN_DIR,
    LOCAL_INDEX_FILE,
    LOCAL_INDEX_META,
    EMBEDDING_MODEL,
    RAG_TOP_K
)

logger = logging.getLogger(__name__)

class RAGEngine:
    """Motor de búsqueda vectorial y gestión del Cerebro."""

    def __init__(self):
        self.model = SentenceTransformer(EMBEDDING_MODEL)
        self.index = None
        self.metadata = [] # Lista de {text, author_uid, score}
        self.load_index()

    def load_index(self):
        """Carga el índice FAISS y metadatos desde disco local."""
        idx_path = os.path.join(LOCAL_BRAIN_DIR, LOCAL_INDEX_FILE)
        meta_path = os.path.join(LOCAL_BRAIN_DIR, LOCAL_INDEX_META)

        if os.path.exists(idx_path) and os.path.exists(meta_path):
            try:
                self.index = faiss.read_index(idx_path)
                with open(meta_path, 'r', encoding='utf-8') as f:
                    self.metadata = json.load(f)
                logger.info(f"[RAG] Cerebro cargado: {len(self.metadata)} aportes.")
            except Exception as e:
                logger.error(f"[RAG] Error cargando índice: {e}")
                self._init_empty()
        else:
            self._init_empty()

    def _init_empty(self):
        """Inicializa un índice vacío si no existe ninguno."""
        logger.info("[RAG] Inicializando cerebro vacío.")
        self.index = faiss.IndexFlatL2(384) # Dimensión de MiniLM-L6-v2
        self.metadata = []

    def get_context(self, query: str, top_k: int = RAG_TOP_K) -> Tuple[str, List[str]]:
        """Busca fragmentos relevantes y devuelve el contexto unido y los autores."""
        if not self.metadata:
            return "", []

        query_vec = self.model.encode([query]).astype('float32')
        distances, indices = self.index.search(query_vec, min(top_k, len(self.metadata)))

        context_parts = []
        authors = []
        
        for i, idx in enumerate(indices[0]):
            if idx != -1 and idx < len(self.metadata):
                item = self.metadata[idx]
                context_parts.append(f"({item['author_uid']}): {item['text']}")
                authors.append(item['author_uid'])

        return "\n---\n".join(context_parts), authors

    def rebuild_index(self, new_data: List[Dict[str, Any]]):
        """
        Reconstruye el índice completo a partir de una lista de aportes filtrados.
        Formato data: [{'text': str, 'author_uid': str, 'score': float}]
        """
        if not new_data:
            return

        texts = [item['text'] for item in new_data]
        vectors = self.model.encode(texts).astype('float32')

        new_index = faiss.IndexFlatL2(384)
        new_index.add(vectors)

        # Guardar en disco
        os.makedirs(LOCAL_BRAIN_DIR, exist_ok=True)
        idx_path = os.path.join(LOCAL_BRAIN_DIR, LOCAL_INDEX_FILE)
        meta_path = os.path.join(LOCAL_BRAIN_DIR, LOCAL_INDEX_META)

        faiss.write_index(new_index, idx_path)
        with open(meta_path, 'w', encoding='utf-8') as f:
            json.dump(new_data, f, ensure_ascii=False)

        self.index = new_index
        self.metadata = new_data
        logger.info(f"[RAG] Cerebro reconstruido con {len(new_data)} neuronas.")

# Instancia global
rag_engine = RAGEngine()
