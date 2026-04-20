"""
Módulo 2 (2/2): RAG Motor Vectorial Multilingüe (rag_engine.py)
---------------------------------------------------------
Integra FAISS con Quantización (PQ) y SentenceTransformer E5 (Cross-lingual)
para retornar recuerdos pasados relevantes en milissegundos.
"""

import logging
import pickle
import tempfile
import json
from typing import Dict, List, Tuple, Optional

import faiss
import numpy as np
import torch
from sentence_transformers import SentenceTransformer

from aisynergix.services.greenfield import get_object

logger = logging.getLogger("synergix.rag")

class MultilingualRAG:
    """Implementa el RAG comprimido que sobrevive en los 8GB RAM de la arquitectura."""
    
    def __init__(self, model_name: str = "intfloat/multilingual-e5-small"):
        self.model_name = model_name
        self.model = None
        self.index = None
        self.metadata = [] 
        self.dimension = 384
        self.similarity_threshold = 0.92
        self._initialized = False

    async def initialize(self) -> None:
        """Invoca a los dioses de C++ para cargar Faiss y model transformers local."""
        if self._initialized:
            return
            
        logger.info("🔄 Inicializando RAG cross-lingual...")
        
        # Float16 es mandatorio en ARM64 para el E5-small, previniendo devoramiento de memoria.
        self.model = SentenceTransformer(
            self.model_name,
            model_kwargs={"torch_dtype": torch.float16},
            device="cpu", # No usamos CUDA en Hetzner Cloud puro
        )
        self.model.eval()
        
        await self._load_or_create_index()
        self._initialized = True
        logger.info(f"✅ RAG Listo. Memoria Faiss PQ cargada: {self.index.ntotal} recuerdos vivos.")

    async def _load_or_create_index(self) -> None:
        """Contacta Greenfield, descifrar brains/ y cargar RAM cruda."""
        try:
            pointer_data, _ = await get_object("aisynergix/data/brain_pointer")
            pointer = json.loads(pointer_data.decode("utf-8"))
            latest_version = pointer.get("latest_v", "v1")
            
            index_bytes, _ = await get_object(f"aisynergix/data/brains/{latest_version}.index")
            meta_bytes, _ = await get_object(f"aisynergix/data/brains/{latest_version}.pkl")
            
            # Carga temporal en buffer (Faiss Python api limits resueltos de forma astuta)
            with tempfile.NamedTemporaryFile(suffix=".index", delete=False) as tmp_idx:
                tmp_idx.write(index_bytes)
                tmp_idx.flush()
                self.index = faiss.read_index(tmp_idx.name)
                
            self.metadata = pickle.loads(meta_bytes)
            
        except Exception as e:
            logger.warning(f"Amnesia inicial detectada o DCellar corrupto ({e}). Construyendo córtex desde cero.")
            self._create_empty_index()

    def _create_empty_index(self) -> None:
        """PQ compresion (Product Quantization): divide 384 dimensiones en 96 sub-vectores"""
        m = 96
        self.index = faiss.IndexPQ(self.dimension, m, 8, faiss.METRIC_INNER_PRODUCT)
        self.metadata = []

    async def add_document(self, text: str, author_uid: str, lang: str, object_path: str) -> bool:
        """Crea nuevos recuerdos en RAM. La fusión de FAISS la hará fusion_brain."""
        await self.initialize()
        with torch.no_grad():
            embedding = self.model.encode(text, convert_to_tensor=True, normalize_embeddings=True, device="cpu")
            
        embedding_np = embedding.cpu().numpy().astype(np.float32).reshape(1, -1)
        
        # Filtro Absoluto Anti-Plagio
        if self.index.ntotal > 0:
            distances, _ = self.index.search(embedding_np, k=1)
            similarity = 1.0 - distances[0][0]
            if similarity >= self.similarity_threshold:
                logger.debug(f"🛑 Plagio cognitivo rechazado por Faiss (sim> {similarity})")
                return False
                
        self.index.add(embedding_np)
        self.metadata.append({
            "text": text,
            "author_uid": author_uid,
            "lang": lang,
            "object_path": object_path,
        })
        return True

    async def search(self, query: str, k: int = 5, min_similarity: float = 0.5) -> Tuple[List[str], List[str]]:
        """Inmersión a FAISS que devuelve (Contextos, UID_de_autores) como dice la orden maestra."""
        await self.initialize()
        if self.index.ntotal == 0:
            return [], []
            
        with torch.no_grad():
            query_embedding = self.model.encode(query, convert_to_tensor=True, normalize_embeddings=True, device="cpu")
            
        query_np = query_embedding.cpu().numpy().astype(np.float32).reshape(1, -1)
        actual_k = min(k, self.index.ntotal)
        distances, indices = self.index.search(query_np, actual_k)
        
        contexts, author_uids = [], []
        
        for dist, idx in zip(distances[0], indices[0]):
            if idx < 0 or idx >= len(self.metadata):
                continue
                
            similarity = 1.0 - dist
            if similarity >= min_similarity:
                meta = self.metadata[idx]
                contexts.append(meta["text"])
                author_uids.append(meta["author_uid"])
                
        return contexts, author_uids

# ──────────────────────────────────────────────────────────────────────────────
# SINGLETON DE RAG
# ──────────────────────────────────────────────────────────────────────────────

_rag_instance: Optional[MultilingualRAG] = None

async def get_rag() -> MultilingualRAG:
    """Implementa el singleton a prueba de asincronicidad"""
    global _rag_instance
    if _rag_instance is None:
        _rag_instance = MultilingualRAG()
        await _rag_instance.initialize()
    return _rag_instance

async def rag_search(query: str, k: int = 5, min_similarity: float = 0.5) -> Tuple[List[str], List[str]]:
    rag = await get_rag()
    return await rag.search(query, k, min_similarity)
