import os
import faiss
import numpy as np
from sentence_transformers import SentenceTransformer

EMBEDDING_MODEL = 'all-MiniLM-L6-v2'
try:
    embedder = SentenceTransformer(EMBEDDING_MODEL)
except Exception as e:
    print(f"Error cargando modelo de embedding: {e}")
    embedder = None

BRAIN_DIR = "aisynergix/data/brains"
INDEX_PATH = f"{BRAIN_DIR}/Synergix_ia.index"
TEXTS_PATH = f"{BRAIN_DIR}/Synergix_ia.txt"

index = None
corpus = []

def load_index():
    global index, corpus
    os.makedirs(BRAIN_DIR, exist_ok=True)
    if os.path.exists(INDEX_PATH) and os.path.exists(TEXTS_PATH):
        try:
            index = faiss.read_index(INDEX_PATH)
            with open(TEXTS_PATH, "r", encoding="utf-8") as f:
                corpus = [line.strip() for line in f.readlines() if line.strip()]
            print(f"[RAG] Memoria Inmortal cargada: {len(corpus)} fragmentos en RAM.")
        except Exception as e:
            print(f"[RAG] Error leyendo FAISS: {e}")
            _init_empty()
    else:
        _init_empty()

def _init_empty():
    global index, corpus
    index = faiss.IndexFlatL2(384)
    corpus = []
    print("[RAG] Memoria Inmortal inicializada en blanco.")

load_index()

async def get_related_context(query: str, top_k: int = 3) -> str:
    if not index or index.ntotal == 0 or not corpus or not embedder:
        return ""
    
    query_vector = embedder.encode([query]).astype(np.float32)
    distances, indices = index.search(query_vector, top_k)
    
    results = []
    for i in indices[0]:
        if i != -1 and i < len(corpus):
            results.append(corpus[i])
            
    if not results: return ""
        
    context_str = "\n".join([f"<legado>\n{res}\n</legado>" for res in results])
    return f"Contexto del Legado Extraído:\n{context_str}"

def reload_index():
    load_index()
