import os
import io
import json
import asyncio
import logging
from typing import List, Dict, Any, Tuple
import numpy as np

try:
    import faiss
    from sentence_transformers import SentenceTransformer
    import torch
except ImportError:
    logging.warning("FAISS / SentenceTransformers no están instalados en este entorno.")
    faiss = None
    SentenceTransformer = None
    torch = None

from aisynergix.services.greenfield import greenfield

logger = logging.getLogger(__name__)

class RAGEngine:
    """
    Motor RAG Vectorial (Cross-Lingual) operando 100% Stateless.
    El índice maestro se almacena en Greenfield y se descarga a RAM en tiempo de ejecución.
    Nunca se persiste en disco local. Mantiene coherencia con el 'brain_pointer'.
    """
    def __init__(self):
        self.encoder = None
        self.index = None
        self.metadata_cache = []
        self.dimension = 384  # multilingual-e5-small
        self.current_brain_version = None

    def initialize_model(self):
        """Carga el modelo SentenceTransformer en memoria con precisión FP16 (ARM/CPU)."""
        if self.encoder is None and SentenceTransformer is not None:
            logger.info("Cargando modelo multilingual-e5-small en memoria...")
            try:
                # Se fuerza float32 si CPU pura o float16 si se desea menor peso (torch_dtype)
                self.encoder = SentenceTransformer('intfloat/multilingual-e5-small', model_kwargs={"torch_dtype": torch.float16})
            except Exception as e:
                logger.error(f"Falla al cargar embeddings RAG: {str(e)}")

    def vectorize(self, text: str) -> np.ndarray:
        """Convierte texto libre a embedding 384-dimensional. Añade prefijo 'query:' recomendado por E5."""
        if not self.encoder:
            self.initialize_model()
            
        if not self.encoder:
            return np.zeros((self.dimension,), dtype=np.float32)
            
        # e5-small requiere el prefijo "query:" para búsquedas
        formatted_text = f"query: {text}"
        try:
            vector = self.encoder.encode(formatted_text, normalize_embeddings=True)
            return np.array(vector, dtype=np.float32)
        except Exception as e:
            logger.error(f"Error vectorizando texto: {str(e)}")
            return np.zeros((self.dimension,), dtype=np.float32)

    async def sync_brain_to_ram(self):
        """
        Consulta 'brain_pointer' en Greenfield, descarga el archivo FAISS binario (.index) 
        y los metadatos (.json) directo a la memoria RAM. NADA de disco local.
        """
        try:
            latest_version = await greenfield.get_brain_pointer()
            if not latest_version:
                logger.warning("No hay brain_pointer registrado. Cerebro vacío.")
                self.index = faiss.IndexFlatIP(self.dimension) # Fallback index vacío
                self.metadata_cache = []
                # Solo para uso inicial si está vacío
                return

            if self.current_brain_version == latest_version:
                # Ya está sincronizado en RAM
                return
                
            logger.info(f"Sincronizando cerebro vectorial v: {latest_version} a RAM...")
            
            index_path = f"aisynergix/data/brains/{latest_version}.index"
            meta_path = f"aisynergix/data/brains/{latest_version}_meta.json"
            
            # Descargas en paralelo a buffers de memoria RAM
            # NOTA: httpx raw get en greenfield service para binarios
            try:
                index_response = await greenfield._execute_request("GET", index_path)
                meta_response = await greenfield._execute_request("GET", meta_path)
                
                # Cargar metadata
                self.metadata_cache = json.loads(meta_response.text)
                
                # Leer IndexIVFPQ desde binario en RAM
                # FAISS en Python no soporta leer directo de un buffer de bytes de manera trivial via API (read_index usa archivos).
                # Solución para Stateless: usar archivo temporal volatil /tmp gestionado por el SO (Docker tmpfs) o faiss.deserialize_index(np_array)
                # Vamos a usar deserialize_index.
                
                index_bytes = index_response.content
                np_bytes = np.frombuffer(index_bytes, dtype=np.uint8)
                
                # Importante: deserialize_index toma un Dtype uint8 numpy array
                self.index = faiss.deserialize_index(np_bytes)
                self.current_brain_version = latest_version
                logger.info(f"Cerebro {latest_version} ({len(self.metadata_cache)} aportes) sincronizado.")
                
            except Exception as e:
                logger.error(f"Falla crítica bajando binarios del Cerebro {latest_version}: {str(e)}")
                # Inicializar en vacío para evitar crashes
                if getattr(self, 'index', None) is None:
                    self.index = faiss.IndexFlatIP(self.dimension)
                    
        except Exception as e:
            logger.error(f"Falla sincronizando cerebro a RAM: {str(e)}")

    async def search_context(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """
        Recibe un texto en cualquier de los 10 idiomas, lo vectoriza y extrae 
        el contexto semántico relevante de la Memoria Inmortal cruzada en todos los idiomas.
        """
        if not self.index:
            await self.sync_brain_to_ram()
            
        vector = self.vectorize(query)
        if vector is None or self.index is None or self.index.ntotal == 0:
            return []
            
        # Reshape para FAISS
        v_matrix = np.expand_dims(vector, axis=0) # (1, 384)
        
        try:
            # Buscar k más cercanos (Inner Product / Cosine Similarity ya que están normalizados)
            distances, indices = self.index.search(v_matrix, top_k)
            
            results = []
            for i, idx in enumerate(indices[0]):
                if idx != -1 and idx < len(self.metadata_cache):
                    meta = self.metadata_cache[idx]
                    # Score de distancia (Cosine similarity)
                    similarity = float(distances[0][i])
                    
                    if similarity > 0.70:  # Umbral de relevancia estricto
                        results.append({
                            "content": meta.get("content", ""),
                            "similarity": similarity,
                            "cid": meta.get("cid", "N/A"),
                            "author_uid": meta.get("author_uid", "")
                        })
            return results
        except Exception as e:
            logger.error(f"Error en RAG vectorial asíncrono: {str(e)}")
            return []

# Singleton del Motor RAG Vectorial Cross-Lingual
rag = RAGEngine()
