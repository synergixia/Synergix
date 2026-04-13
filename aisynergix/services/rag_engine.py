import faiss
import json
import logging
import numpy as np
from sentence_transformers import SentenceTransformer

logger = logging.getLogger("RAGEngine")

class RAGEngine:
    def __init__(self):
        # Cargamos en float16 para ahorrar RAM en ARM64
        self.model = SentenceTransformer('all-MiniLM-L6-v2', device='cpu')
        self.index = None
        self.metadata = [] # Lista de {content, author_uid}

    def hot_reload(self, index_path, meta_path):
        try:
            self.index = faiss.read_index(index_path)
            with open(meta_path, 'r', encoding='utf-8') as f:
                self.metadata = json.load(f)
            logger.info("Motor RAG actualizado y recargado en RAM.")
        except Exception as e:
            logger.error(f"Error en hot_reload RAG: {e}")

    async def get_context(self, query, top_k=3):
        if not self.index:
            return "", []
        
        query_vector = self.model.encode([query]).astype('float32')
        distances, indices = self.index.search(query_vector, top_k)
        
        context_parts = []
        uids_to_reward = []
        
        for idx in indices[0]:
            if idx != -1 and idx < len(self.metadata):
                item = self.metadata[idx]
                context_parts.append(item['content'])
                if item.get('author_uid'):
                    uids_to_reward.append(item['author_uid'])
                    
        return "\n---\n".join(context_parts), list(set(uids_to_reward))

rag_engine = RAGEngine()
