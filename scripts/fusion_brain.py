#!/usr/bin/env python3
"""
Módulo de Evolución Autónoma - Fusion Brain
Ejecutado cada 10 minutos por APScheduler para:
1. Vectorizar nuevos aportes (< 0.92 similitud)
2. Actualizar índice FAISS con cuantización PQ
3. Generar top10.json con total de usuarios y ranking
4. Subir todo a DCellar (synergixai bucket)
"""

import asyncio
import json
import logging
import pickle
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Tuple, Optional, Any
from collections import defaultdict

import faiss
from sentence_transformers import SentenceTransformer
from tenacity import retry, stop_after_attempt, wait_exponential

from aisynergix.services.greenfield import (
    list_objects,
    get_object,
    put_object,
    get_user_metadata,
    _hash_uid,
    update_object_metadata,
)
from aisynergix.bot.identity import RANGOS

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ==================== CONFIGURACIÓN ====================
MODEL_NAME = "intfloat/multilingual-e5-small"
EMBEDDING_DIM = 384  # Dimensión del modelo E5-small
FAISS_INDEX_TYPE = faiss.IndexPQ  # Product Quantization para eficiencia RAM
PQ_M = 12  # Número de subvectores para PQ (384/12 = 32 bits por subvector)
PQ_BITS = 8  # 8 bits = 256 centros por subvector

SIMILARITY_THRESHOLD = 0.92  # Umbral de similitud para evitar duplicados
BATCH_SIZE = 32  # Tamaño de batch para embedding
MAX_NEW_APORTES = 1000  # Límite de nuevos aportes por ciclo

class FusionBrain:
    def __init__(self):
        self.model = None
        self.index = None
        self.id_to_metadata = {}  # Mapeo ID -> metadata del aporte
        self.embeddings_cache = {}  # Cache de embeddings para similitud rápida
        
    async def initialize(self):
        """Inicializa el modelo y carga el índice existente"""
        logger.info("Inicializando FusionBrain...")
        
        # Cargar modelo SentenceTransformer
        self.model = SentenceTransformer(
            MODEL_NAME,
            model_kwargs={"torch_dtype": "float16"},
            device="cpu"
        )
        logger.info(f"Modelo {MODEL_NAME} cargado")
        
        # Cargar índice existente desde DCellar
        await self._load_existing_index()
        
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
    async def _load_existing_index(self):
        """Carga el índice FAISS existente desde DCellar"""
        try:
            # Leer brain_pointer para saber la versión actual
            pointer_content = await get_object("aisynergix/data/brain_pointer")
            pointer_data = json.loads(pointer_content.decode())
            latest_version = pointer_data.get("latest_v", "v0")
            
            # Descargar índice y mapeo
            index_data = await get_object(f"aisynergix/data/brains/{latest_version}.index")
            mapping_data = await get_object(f"aisynergix/data/brains/{latest_version}.pkl")
            
            # Cargar índice FAISS
            self.index = faiss.deserialize_index(index_data)
            
            # Cargar mapeo de metadatos
            self.id_to_metadata = pickle.loads(mapping_data)
            
            # Pre-calcular embeddings cache para similitud rápida
            self._build_embeddings_cache()
            
            logger.info(f"Índice {latest_version} cargado: {self.index.ntotal} vectores")
            
        except Exception as e:
            logger.warning(f"No se pudo cargar índice existente: {e}. Creando nuevo índice...")
            await self._create_new_index()
    
    async def _create_new_index(self):
        """Crea un nuevo índice FAISS con cuantización PQ"""
        # Crear índice PQ para máxima eficiencia RAM
        self.index = FAISS_INDEX_TYPE(EMBEDDING_DIM, PQ_M, PQ_BITS)
        self.index = faiss.IndexIDMap(self.index)
        self.id_to_metadata = {}
        logger.info("Nuevo índice PQ creado")
    
    def _build_embeddings_cache(self):
        """Construye caché de embeddings para búsqueda rápida de similitud"""
        if not self.id_to_metadata:
            return
        
        # Para similitud rápida, podemos mantener una muestra de embeddings
        # En producción, usaríamos ANN para búsqueda aproximada
        self.embeddings_cache = {}
        logger.info("Caché de embeddings construida")
    
    async def process_new_aporte_batch(self, aportes: List[Dict[str, Any]]) -> Tuple[int, int]:
        """
        Procesa un batch de nuevos aportes
        Returns: (añadidos, duplicados)
        """
        if not aportes:
            return 0, 0
        
        añadidos = 0
        duplicados = 0
        
        # Filtrar aportes por similitud
        filtered_aportes = await self._filter_by_similarity(aportes)
        
        if not filtered_aportes:
            return 0, len(aportes) - len(filtered_aportes)
        
        # Generar embeddings por batch
        textos = [aporte["texto"] for aporte in filtered_aportes]
        embeddings = self.model.encode(
            textos,
            batch_size=BATCH_SIZE,
            show_progress_bar=False,
            convert_to_numpy=True,
            normalize_embeddings=True  # Normalizar para cosine similarity
        )
        
        # Añadir al índice
        ids = []
        for i, (aporte, embedding) in enumerate(zip(filtered_aportes, embeddings)):
            idx = len(self.id_to_metadata)
            self.id_to_metadata[idx] = aporte
            ids.append(idx)
        
        embeddings_np = np.array(embeddings).astype('float32')
        ids_np = np.array(ids, dtype='int64')
        
        try:
            self.index.add_with_ids(embeddings_np, ids_np)
            añadidos = len(filtered_aportes)
            duplicados = len(aportes) - len(filtered_aportes)
            logger.info(f"Batch procesado: {añadidos} añadidos, {duplicados} duplicados")
        except Exception as e:
            logger.error(f"Error añadiendo al índice: {e}")
            # Revertir cambios en metadata
            for idx in ids:
                self.id_to_metadata.pop(idx, None)
        
        return añadidos, duplicados
    
    async def _filter_by_similarity(self, nuevos_aportes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Filtra aportes por similitud (< threshold)"""
        if self.index.ntotal == 0:
            return nuevos_aportes
        
        filtered = []
        
        # Procesar en batches para eficiencia
        for i in range(0, len(nuevos_aportes), BATCH_SIZE):
            batch = nuevos_aportes[i:i + BATCH_SIZE]
            textos = [aporte["texto"] for aporte in batch]
            
            # Embeddings de nuevos aportes
            new_embeddings = self.model.encode(
                textos,
                batch_size=BATCH_SIZE,
                show_progress_bar=False,
                convert_to_numpy=True,
                normalize_embeddings=True
            )
            
            # Buscar similares en índice existente
            for j, (aporte, embedding) in enumerate(zip(batch, new_embeddings)):
                # Búsqueda aproximada del vecino más cercano
                embedding_np = np.array([embedding]).astype('float32')
                D, I = self.index.search(embedding_np, k=1)
                
                if I[0][0] != -1:  # Encontró un vecino
                    similarity = 1 - D[0][0]  # FAISS usa L2, convertimos a cosine similarity aproximada
                    if similarity > SIMILARITY_THRESHOLD:
                        existing_id = I[0][0]
                        existing_aporte = self.id_to_metadata.get(existing_id, {})
                        logger.debug(f"Aporte duplicado: {similarity:.3f} similar a {existing_id}")
                        continue
                
                filtered.append(aporte)
        
        return filtered
    
    async def scan_new_aportes(self) -> List[Dict[str, Any]]:
        """Escanea DCellar en busca de nuevos aportes no vectorizados"""
        logger.info("Escaneando nuevos aportes en DCellar...")
        
        nuevos_aportes = []
        processed_count = 0
        
        # Listar todos los aportes en el bucket
        aportes_paths = await list_objects("aisynergix/aportes/")
        
        for aporte_path in aportes_paths:
            try:
                # Extraer metadata del path
                parts = aporte_path.split('/')
                if len(parts) < 3:
                    continue
                    
                # Verificar si ya está en nuestro índice
                aporte_id = parts[-1].replace('.txt', '')
                if any(aporte_id in str(meta.get("id", "")) for meta in self.id_to_metadata.values()):
                    continue
                
                # Descargar aporte
                content = await get_object(aporte_path)
                texto = content.decode('utf-8', errors='ignore')
                
                # Obtener metadata de tags
                # (En DCellar real, obtendríamos los tags vía GetObjectMeta)
                metadata = {
                    "id": aporte_id,
                    "texto": texto,
                    "path": aporte_path,
                    "timestamp": datetime.now().isoformat(),
                }
                
                nuevos_aportes.append(metadata)
                processed_count += 1
                
                if processed_count >= MAX_NEW_APORTES:
                    logger.warning(f"Límite de {MAX_NEW_APORTES} nuevos aportes alcanzado")
                    break
                    
            except Exception as e:
                logger.error(f"Error procesando {aporte_path}: {e}")
                continue
        
        logger.info(f"Encontrados {len(nuevos_aportes)} nuevos aportes para procesar")
        return nuevos_aportes
    
    async def generate_top10_json(self) -> Dict[str, Any]:
        """Genera el JSON del leaderboard con total de usuarios y top 10"""
        logger.info("Generando top10.json...")
        
        try:
            # Listar todos los usuarios
            users = await list_objects("aisynergix/users/")
            total_users = len(users)
            
            # Obtener metadata de cada usuario
            user_stats = []
            for user_path in users:
                try:
                    uid_hash = user_path.split('/')[-1]
                    metadata = await get_user_metadata(uid_hash)
                    
                    if metadata:
                        user_stats.append({
                            "uid_hash": uid_hash,
                            "points": int(metadata.get("points", 0)),
                            "rank": int(metadata.get("rank", 0)),
                            "total_uses_count": int(metadata.get("total_uses_count", 0)),
                            "daily_aportes_count": int(metadata.get("daily_aportes_count", 0)),
                        })
                except Exception as e:
                    logger.error(f"Error obteniendo metadata de {user_path}: {e}")
                    continue
            
            # Ordenar por puntos (descendente)
            user_stats.sort(key=lambda x: x["points"], reverse=True)
            
            # Formatear top 10
            top10 = []
            for i, user in enumerate(user_stats[:10], 1):
                rank_name = RANGOS.get(user["rank"], ("Desconocido", 0, 0))[0]
                top10.append({
                    "position": i,
                    "uid_hash": user["uid_hash"],
                    "points": user["points"],
                    "rank": user["rank"],
                    "rank_name": rank_name,
                    "total_uses": user["total_uses_count"],
                })
            
            # Crear estructura final
            result = {
                "generated_at": datetime.utcnow().isoformat(),
                "total_users": total_users,
                "top10": top10,
                "stats": {
                    "avg_points": sum(u["points"] for u in user_stats) / max(len(user_stats), 1),
                    "max_points": user_stats[0]["points"] if user_stats else 0,
                    "active_users": len([u for u in user_stats if u["points"] > 0]),
                }
            }
            
            logger.info(f"Leaderboard generado: {total_users} usuarios, top: {top10[0]['points'] if top10 else 0} pts")
            return result
            
        except Exception as e:
            logger.error(f"Error generando top10.json: {e}")
            # Retornar estructura vacía en caso de error
            return {
                "generated_at": datetime.utcnow().isoformat(),
                "total_users": 0,
                "top10": [],
                "stats": {"avg_points": 0, "max_points": 0, "active_users": 0}
            }
    
    async def save_to_greenfield(self):
        """Guarda el índice actualizado y el leaderboard en DCellar"""
        logger.info("Guardando evolución en DCellar...")
        
        try:
            # Generar versión nueva
            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            version = f"v{timestamp}"
            
            # Serializar índice FAISS
            index_bytes = faiss.serialize_index(self.index)
            
            # Serializar metadatos
            metadata_bytes = pickle.dumps(self.id_to_metadata)
            
            # Subir archivos
            await put_object(
                f"aisynergix/data/brains/{version}.index",
                index_bytes,
                content_type="application/octet-stream"
            )
            
            await put_object(
                f"aisynergix/data/brains/{version}.pkl",
                metadata_bytes,
                content_type="application/octet-stream"
            )
            
            # Actualizar brain_pointer
            pointer_data = {"latest_v": version}
            await put_object(
                "aisynergix/data/brain_pointer",
                json.dumps(pointer_data).encode(),
                content_type="application/json"
            )
            
            # Generar y subir top10.json
            top10_data = await self.generate_top10_json()
            await put_object(
                "aisynergix/data/top10.json",
                json.dumps(top10_data, indent=2).encode(),
                content_type="application/json"
            )
            
            logger.info(f"Evolución guardada: versión {version}, {self.index.ntotal} vectores")
            
        except Exception as e:
            logger.error(f"Error guardando en Greenfield: {e}")
            raise
    
    async def cleanup_old_versions(self, keep_last_n: int = 5):
        """Limpia versiones antiguas del cerebro, manteniendo solo las keep_last_n más recientes"""
        try:
            brain_files = await list_objects("aisynergix/data/brains/")
            
            # Extraer versiones de archivos .index
            versions = []
            for file_path in brain_files:
                if file_path.endswith('.index'):
                    version = file_path.split('/')[-1].replace('.index', '')
                    if version.startswith('v'):
                        versions.append(version)
            
            # Ordenar por timestamp (vYYYYMMDD_HHMMSS)
            versions.sort(reverse=True)
            
            # Eliminar versiones antiguas
            for version in versions[keep_last_n:]:
                try:
                    # En DCellar real, usaríamos delete_object
                    logger.info(f"Marcando versión {version} para limpieza (manteniendo {keep_last_n} versiones)")
                    # Nota: La eliminación real requeriría permisos adicionales
                except Exception as e:
                    logger.error(f"Error limpiando versión {version}: {e}")
                    
        except Exception as e:
            logger.error(f"Error en cleanup_old_versions: {e}")

async def fusion_brain():
    """
    Función principal ejecutada por el cron cada 10 minutos
    """
    logger.info("=== INICIANDO CICLO DE EVOLUCIÓN ===")
    
    fusion = FusionBrain()
    
    try:
        # 1. Inicializar
        await fusion.initialize()
        
        # 2. Escanear nuevos aportes
        nuevos_aportes = await fusion.scan_new_aportes()
        
        if not nuevos_aportes:
            logger.info("No hay nuevos aportes para procesar")
        else:
            # 3. Procesar nuevos aportes por batches
            total_añadidos = 0
            total_duplicados = 0
            
            for i in range(0, len(nuevos_aportes), BATCH_SIZE):
                batch = nuevos_aportes[i:i + BATCH_SIZE]
                añadidos, duplicados = await fusion.process_new_aporte_batch(batch)
                total_añadidos += añadidos
                total_duplicados += duplicados
            
            logger.info(f"Resumen: {total_añadidos} añadidos, {total_duplicados} duplicados filtrados")
        
        # 4. Guardar en Greenfield
        await fusion.save_to_greenfield()
        
        # 5. Limpieza opcional de versiones antiguas
        await fusion.cleanup_old_versions(keep_last_n=3)
        
        logger.info("=== CICLO DE EVOLUCIÓN COMPLETADO ===")
        return True
        
    except Exception as e:
        logger.error(f"Error en ciclo de evolución: {e}")
        return False

if __name__ == "__main__":
    # Para ejecución manual
    asyncio.run(fusion_brain())
