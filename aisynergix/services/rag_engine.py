import os
import faiss
import numpy as np
import logging
from typing import List, Dict, Tuple
from sentence_transformers import SentenceTransformer

logger = logging.getLogger("Synergix.RAG")

class RAGEngine:
    """
    Motor RAG para el Nodo Fantasma.
    Usa MiniLM-L6-v2 para embeddings y FAISS para búsqueda.
    """
    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self.model = SentenceTransformer(model_name)
        self.index = None
        self.documents = [] # Lista de (uid, content)
        self.index_path = None
        self.txt_path = None

    def load_brain(self, index_path: str, txt_path: str):
        """Carga el conocimiento del .index y el .txt desde el disco."""
        try:
            if os.path.exists(index_path):
                self.index = faiss.read_index(index_path)
                self.index_path = index_path
            
            if os.path.exists(txt_path):
                self.documents = []
                with open(txt_path, "r", encoding="utf-8") as f:
                    for line in f:
                        if "|" in line:
                            uid, content = line.split("|", 1)
                            self.documents.append({"uid": uid.strip(), "content": content.strip()})
                self.txt_path = txt_path
                
            logger.info(f"Cerebro cargado: {len(self.documents)} fragmentos inyectados.")
        except Exception as e:
            logger.error(f"Error cargando el cerebro RAG: {e}")

    def hot_reload(self):
        """Recarga la RAM sin apagar el bot."""
        if self.index_path and self.txt_path:
            self.load_brain(self.index_path, self.txt_path)
            logger.info("Hot Reload del cerebro completado.")

    def get_context(self, query: str, top_k: int = 3) -> Tuple[str, List[int]]:
        """
        Busca similitudes y devuelve contexto + lista de UIDs de autores 
        para el pago de puntos residuales.
        """
        if self.index is None or not self.documents:
            return "No hay conocimiento previo disponible.", []

        query_vector = self.model.encode([query])
        distances, indices = self.index.search(query_vector.astype("float32"), top_k)
        
        context_parts = []
        author_uids = []
        
        for i, idx in enumerate(indices[0]):
            if idx != -1 and idx < len(self.documents):
                doc = self.documents[idx]
                context_parts.append(doc["content"])
                try:
                    uid = int(doc["uid"])
                    if uid not in author_uids:
                        author_uids.append(uid)
                except ValueError:
                    continue
                    
        return "\n---\n".join(context_parts), author_uids
