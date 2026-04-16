"""
rag_engine.py — Motor de recuperación (RAG) de Synergix.
Usa faiss-cpu y all-MiniLM-L6-v2 en float16 para búsqueda semántica.
Devuelve texto y author_uid (ofuscado) para puntos residuales.
"""

import os
import json
import logging
import faiss
import numpy as np
from typing import List, Tuple, Dict, Any, Optional
from sentence_transformers import SentenceTransformer

from aisynergix.config.constants import (
    LOCAL_BRAIN_DIR,
    LOCAL_INDEX_FILE,
    LOCAL_INDEX_META,
    EMBEDDING_MODEL,
    RAG_TOP_K,
    RAG_MIN_QUALITY_SCORE
)

logger = logging.getLogger(__name__)


class RAGEngine:
    """
    Motor de búsqueda vectorial y gestión del Cerebro Colectivo.
    
    Características:
    - Usa all-MiniLM-L6-v2 con embeddings en float16 para eficiencia
    - Índice FAISS optimizado para CPU (ARM64 compatible)
    - Devuelve texto y author_uid ofuscado para puntos residuales
    - Hot-reload de índices sin reiniciar el servidor
    """
    
    def __init__(self):
        """Inicializa el motor RAG con modelo de embeddings en float16."""
        self.model = None
        self.index = None
        self.metadata: List[Dict[str, Any]] = []  # Lista de {text, author_uid, score, original_uid}
        self.dimension = 384  # Dimensión de all-MiniLM-L6-v2
        self._init_model()
        self.load_index()
    
    def _init_model(self):
        """Inicializa el modelo de embeddings con precisión float16."""
        try:
            # Cargar modelo con device='cpu' y habilitar half precision si está disponible
            self.model = SentenceTransformer(EMBEDDING_MODEL, device='cpu')
            
            # Configurar el modelo para usar float16 si es compatible
            if hasattr(self.model, 'half'):
                try:
                    self.model = self.model.half()
                    logger.info(f"Modelo {EMBEDDING_MODEL} configurado en float16")
                except Exception as e:
                    logger.warning(f"No se pudo convertir a float16: {e}. Usando float32")
            
            logger.info(f"Modelo de embeddings cargado: {EMBEDDING_MODEL}")
            
        except Exception as e:
            logger.error(f"Error cargando modelo de embeddings: {e}", exc_info=True)
            raise
    
    def load_index(self):
        """Carga el índice FAISS y metadatos desde disco local."""
        idx_path = os.path.join(LOCAL_BRAIN_DIR, LOCAL_INDEX_FILE)
        meta_path = os.path.join(LOCAL_BRAIN_DIR, LOCAL_INDEX_META)
        
        if os.path.exists(idx_path) and os.path.exists(meta_path):
            try:
                # Cargar índice FAISS
                self.index = faiss.read_index(idx_path)
                
                # Cargar metadatos
                with open(meta_path, 'r', encoding='utf-8') as f:
                    self.metadata = json.load(f)
                
                # Validar dimensión
                if self.index.d != self.dimension:
                    logger.warning(f"Dimensión del índice ({self.index.d}) no coincide con modelo ({self.dimension})")
                    self._init_empty()
                else:
                    logger.info(f"Cerebro cargado: {len(self.metadata)} aportes, dimensión {self.index.d}")
                    
            except Exception as e:
                logger.error(f"Error cargando índice RAG: {e}", exc_info=True)
                self._init_empty()
        else:
            self._init_empty()
            logger.info(f"Archivos de índice no encontrados en {LOCAL_BRAIN_DIR}. Cerebro vacío inicializado.")
    
    def _init_empty(self):
        """Inicializa un índice vacío si no existe ninguno."""
        self.index = faiss.IndexFlatL2(self.dimension)
        self.metadata = []
        logger.info("Índice FAISS vacío inicializado")
    
    def get_context(
        self,
        query: str,
        top_k: int = RAG_TOP_K,
        min_score: float = RAG_MIN_QUALITY_SCORE
    ) -> Tuple[str, List[str], List[Dict[str, Any]]]:
        """
        Busca fragmentos relevantes en el cerebro colectivo.
        
        Args:
            query: Consulta del usuario
            top_k: Número máximo de resultados
            min_score: Puntuación mínima de calidad para incluir
        
        Returns:
            Tuple: (contexto_unido, lista_author_uids, lista_resultados_completos)
        """
        if not self.metadata or self.index.ntotal == 0:
            logger.debug("Cerebro vacío, no hay contexto para recuperar")
            return "", [], []
        
        try:
            # Codificar consulta
            query_embedding = self.model.encode(
                [query],
                convert_to_numpy=True,
                normalize_embeddings=True,
                show_progress_bar=False
            ).astype('float32')
            
            # Búsqueda en FAISS
            distances, indices = self.index.search(
                query_embedding,
                min(top_k, len(self.metadata))
            )
            
            context_parts = []
            author_uids = []
            results = []
            
            for i, (distance, idx) in enumerate(zip(distances[0], indices[0])):
                if idx == -1 or idx >= len(self.metadata):
                    continue
                
                item = self.metadata[idx]
                
                # Filtrar por puntuación mínima
                if item.get("score", 0) < min_score:
                    continue
                
                # Preparar resultado
                result = {
                    "text": item["text"],
                    "author_uid": item["author_uid"],
                    "score": item.get("score", 0),
                    "distance": float(distance),
                    "rank": i + 1
                }
                
                # Formatear para contexto
                context_parts.append(f"[{i+1}] {item['text']}")
                author_uids.append(item["author_uid"])
                results.append(result)
                
                logger.debug(f"RAG resultado {i+1}: uid={item['author_uid']}, score={item.get('score')}, dist={distance:.4f}")
            
            if not context_parts:
                return "", [], []
            
            # Unir contexto
            contexto = "\n\n---\n\n".join(context_parts)
            logger.info(f"RAG devolvió {len(context_parts)} resultados para query: '{query[:50]}...'")
            
            return contexto, author_uids, results
            
        except Exception as e:
            logger.error(f"Error en búsqueda RAG: {e}", exc_info=True)
            return "", [], []
    
    def rebuild_index(self, new_data: List[Dict[str, Any]]):
        """
        Reconstruye el índice completo a partir de aportes filtrados.
        
        Args:
            new_data: Lista de aportes en formato:
                [
                    {
                        "text": str,
                        "author_uid": str (ofuscado),
                        "score": float,
                        "original_uid": str (opcional, para desofuscación interna)
                    },
                    ...
                ]
        """
        if not new_data:
            logger.warning("No hay datos para reconstruir índice")
            return
        
        try:
            logger.info(f"Reconstruyendo índice con {len(new_data)} aportes...")
            
            # Extraer textos
            texts = [item["text"] for item in new_data]
            
            # Generar embeddings con float16
            logger.info(f"Generando embeddings para {len(texts)} textos...")
            embeddings = self.model.encode(
                texts,
                convert_to_numpy=True,
                normalize_embeddings=True,
                show_progress_bar=True
            ).astype('float32')  # FAISS requiere float32
            
            # Crear nuevo índice FAISS
            new_index = faiss.IndexFlatL2(self.dimension)
            new_index.add(embeddings)
            
            # Guardar en disco
            os.makedirs(LOCAL_BRAIN_DIR, exist_ok=True)
            idx_path = os.path.join(LOCAL_BRAIN_DIR, LOCAL_INDEX_FILE)
            meta_path = os.path.join(LOCAL_BRAIN_DIR, LOCAL_INDEX_META)
            
            faiss.write_index(new_index, idx_path)
            with open(meta_path, 'w', encoding='utf-8') as f:
                json.dump(new_data, f, ensure_ascii=False, indent=2)
            
            # Actualizar en memoria
            self.index = new_index
            self.metadata = new_data
            
            logger.info(f"✅ Índice reconstruido: {len(new_data)} neuronas, {self.index.ntotal} vectores")
            
        except Exception as e:
            logger.error(f"Error reconstruyendo índice RAG: {e}", exc_info=True)
            raise
    
    def add_single_contribution(
        self,
        text: str,
        author_uid: str,
        score: float,
        original_uid: Optional[str] = None
    ) -> bool:
        """
        Añade una sola contribución al índice sin reconstruirlo completamente.
        
        Args:
            text: Texto del aporte
            author_uid: UID ofuscado del autor
            score: Puntuación de calidad
            original_uid: UID original (para desofuscación interna)
        
        Returns:
            bool: True si se añadió exitosamente
        """
        try:
            # Generar embedding para el nuevo texto
            embedding = self.model.encode(
                [text],
                convert_to_numpy=True,
                normalize_embeddings=True,
                show_progress_bar=False
            ).astype('float32')
            
            # Añadir al índice
            self.index.add(embedding)
            
            # Añadir a metadatos
            new_item = {
                "text": text,
                "author_uid": author_uid,
                "score": score,
                "original_uid": original_uid
            }
            self.metadata.append(new_item)
            
            # Guardar incrementalmente
            self._save_incremental(new_item, embedding)
            
            logger.info(f"Aporte añadido al RAG: uid={author_uid}, score={score}, texto={text[:50]}...")
            return True
            
        except Exception as e:
            logger.error(f"Error añadiendo contribución al RAG: {e}")
            return False
    
    def _save_incremental(self, item: Dict[str, Any], embedding: np.ndarray):
        """
        Guarda incrementalmente el nuevo aporte en disco.
        
        Args:
            item: Metadatos del nuevo aporte
            embedding: Vector de embeddings
        """
        try:
            # Cargar índice existente
            idx_path = os.path.join(LOCAL_BRAIN_DIR, LOCAL_INDEX_FILE)
            meta_path = os.path.join(LOCAL_BRAIN_DIR, LOCAL_INDEX_META)
            
            # Añadir embedding al índice en disco
            existing_index = faiss.read_index(idx_path)
            existing_index.add(embedding)
            faiss.write_index(existing_index, idx_path)
            
            # Añadir metadatos
            with open(meta_path, 'r', encoding='utf-8') as f:
                existing_meta = json.load(f)
            
            existing_meta.append(item)
            
            with open(meta_path, 'w', encoding='utf-8') as f:
                json.dump(existing_meta, f, ensure_ascii=False, indent=2)
                
        except Exception as e:
            logger.error(f"Error guardando incrementalmente en RAG: {e}")
            # En caso de error, se requerirá reconstrucción completa en la siguiente fusión
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Devuelve estadísticas del cerebro RAG.
        
        Returns:
            Dict: Estadísticas del índice
        """
        return {
            "total_contributions": len(self.metadata),
            "index_size": self.index.ntotal if self.index else 0,
            "dimension": self.dimension,
            "avg_score": np.mean([item.get("score", 0) for item in self.metadata]) if self.metadata else 0,
            "loaded": self.index is not None
        }


# Instancia global del motor RAG
rag_engine = RAGEngine()
