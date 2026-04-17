#!/usr/bin/env python3
"""
Módulo de Sincronización - Sync Brain
Ejecutado al iniciar el nodo para:
1. Descargar la última versión del cerebro desde DCellar
2. Cargar el índice FAISS y mapeo de metadatos
3. Inicializar el modelo SentenceTransformer
4. Preparar el sistema para operaciones RAG
"""

import asyncio
import json
import logging
import pickle
import numpy as np
from pathlib import Path
from typing import Optional, Dict, Any

import faiss
from sentence_transformers import SentenceTransformer
from tenacity import retry, stop_after_attempt, wait_exponential

from aisynergix.services.greenfield import get_object, put_object
from aisynergix.services.rag_engine import RAGEngine

logging.basicConfig(
    level=logging.INFO,
    format='%(asctiasctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ==================== CONFIGURACIÓN ====================
MODEL_NAME = "intfloat/multilingual-e5-small"
LOCAL_MODEL_PATH = Path("/aisynergix/ai/models/multilingual-e5-small")
LOCAL_BRAIN_PATH = Path("/aisynergix/data/brains")
LOCAL_CONFIG_PATH = Path("/aisynergix/data/config")

class SyncBrain:
    def __init__(self):
        self.model = None
        self.faiss_index = None
        self.metadata_map = {}
        self.latest_version = "v0"
        self.rag_engine = None
        
    async def initialize(self) -> bool:
        """Inicializa todo el sistema descargando desde DCellar"""
        logger.info("Inicializando SyncBrain...")
        
        try:
            # 1. Crear directorios locales si no existen
            self._ensure_directories()
            
            # 2. Descargar o cargar modelo localmente
            await self._load_model()
            
            # 3. Descargar configuración del sistema
            await self._download_system_config()
            
            # 4. Descargar última versión del cerebro
            success = await self._download_latest_brain()
            
            if success:
                # 5. Inicializar RAG Engine
                self.rag_engine = RAGEngine()
                await self.rag_engine.initialize(
                    model=self.model,
                    index=self.faiss_index,
                    metadata_map=self.metadata_map
                )
                logger.info("SyncBrain inicializado exitosamente")
                return True
            else:
                logger.error("No se pudo descargar el cerebro. Sistema en modo limitado.")
                return False
                
        except Exception as e:
            logger.error(f"Error inicializando SyncBrain: {e}")
            return False
    
    def _ensure_directories(self):
        """Crea los directorios necesarios localmente"""
        LOCAL_MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        LOCAL_BRAIN_PATH.mkdir(parents=True, exist_ok=True)
        LOCAL_CONFIG_PATH.mkdir(parents=True, exist_ok=True)
        logger.info("Directorios locales verificados")
    
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
    async def _load_model(self):
        """Carga el modelo SentenceTransformer, descargándolo si es necesario"""
        try:
            # Verificar si el modelo ya existe localmente
            if LOCAL_MODEL_PATH.exists():
                logger.info(f"Cargando modelo local desde {LOCAL_MODEL_PATH}")
                self.model = SentenceTransformer(
                    str(LOCAL_MODEL_PATH),
                    device="cpu",
                    model_kwargs={"torch_dtype": "float16"}
                )
            else:
                logger.info(f"Descargando modelo {MODEL_NAME}...")
                self.model = SentenceTransformer(
                    MODEL_NAME,
                    device="cpu",
                    model_kwargs={"torch_dtype": "float16"}
                )
                
                # Guardar localmente para futuras ejecuciones
                self.model.save(str(LOCAL_MODEL_PATH))
                logger.info(f"Modelo guardado localmente en {LOCAL_MODEL_PATH}")
                
        except Exception as e:
            logger.error(f"Error cargando modelo: {e}")
            raise
    
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
    async def _download_system_config(self):
        """Descarga la configuración del sistema desde DCellar"""
        try:
            config_content = await get_object("aisynergix/data/system_config.json")
            config_data = json.loads(config_content.decode())
            
            # Guardar localmente
            local_config_file = LOCAL_CONFIG_PATH / "system_config.json"
            with open(local_config_file, 'w') as f:
                json.dump(config_data, f, indent=2)
            
            logger.info(f"Configuración del sistema descargada: {len(config_data)} parámetros")
            return config_data
            
        except Exception as e:
            logger.warning(f"No se pudo descargar configuración del sistema: {e}")
            # Crear configuración por defecto
            default_config = {
                "version": "1.0.0",
                "model": MODEL_NAME,
                "embedding_dim": 384,
                "similarity_threshold": 0.92,
                "max_context_length": 2000,
                "languages": ["es", "en", "zh-hans", "zh-hant"],
                "ranks": {
                    "0": {"name": "🌱 Iniciado", "min_points": 0, "daily_limit": 5},
                    "1": {"name": "📈 Activo", "min_points": 100, "daily_limit": 12},
                    "2": {"name": "🧬 Sincronizado", "min_points": 500, "daily_limit": 25},
                    "3": {"name": "🏗️ Arquitecto", "min_points": 1500, "daily_limit": 40},
                    "4": {"name": "🧠 Mente Colmena", "min_points": 5000, "daily_limit": 60},
                    "5": {"name": "🔮 Oráculo", "min_points": 15000, "daily_limit": None},
                },
                "ia_config": {
                    "pensador": {
                        "temperature": 0.3,
                        "top_k": 40,
                        "max_tokens": 500
                    },
                    "juez": {
                        "temperature": 0.1,
                        "top_k": 20,
                        "max_tokens": 100
                    }
                }
            }
            
            local_config_file = LOCAL_CONFIG_PATH / "system_config.json"
            with open(local_config_file, 'w') as f:
                json.dump(default_config, f, indent=2)
            
            logger.info("Configuración por defecto creada localmente")
            return default_config
    
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
    async def _download_latest_brain(self) -> bool:
        """Descarga la última versión del cerebro desde DCellar"""
        try:
            # 1. Leer brain_pointer para obtener la versión actual
            pointer_content = await get_object("aisynergix/data/brain_pointer")
            pointer_data = json.loads(pointer_content.decode())
            self.latest_version = pointer_data.get("latest_v", "v0")
            
            logger.info(f"Versión más reciente: {self.latest_version}")
            
            # 2. Descargar índice FAISS
            index_path = f"aisynergix/data/brains/{self.latest_version}.index"
            index_content = await get_object(index_path)
            
            # Guardar localmente
            local_index_file = LOCAL_BRAIN_PATH / f"{self.latest_version}.index"
            with open(local_index_file, 'wb') as f:
                f.write(index_content)
            
            # Cargar índice
            self.faiss_index = faiss.deserialize_index(index_content)
            
            # 3. Descargar mapeo de metadatos
            metadata_path = f"aisynergix/data/brains/{self.latest_version}.pkl"
            metadata_content = await get_object(metadata_path)
            
            # Guardar localmente
            local_metadata_file = LOCAL_BRAIN_PATH / f"{self.latest_version}.pkl"
            with open(local_metadata_file, 'wb') as f:
                f.write(metadata_content)
            
            # Cargar metadatos
            self.metadata_map = pickle.loads(metadata_content)
            
            # 4. Descargar top10.json para cache local
            try:
                top10_content = await get_object("aisynergix/data/top10.json")
                local_top10_file = LOCAL_BRAIN_PATH / "top10.json"
                with open(local_top10_file, 'wb') as f:
                    f.write(top10_content)
                logger.info("Leaderboard descargado y cacheado")
            except Exception as e:
                logger.warning(f"No se pudo descargar leaderboard: {e}")
            
            logger.info(f"Cerebro descargado: {self.faiss_index.ntotal} vectores, {len(self.metadata_map)} metadatos")
            return True
            
        except Exception as e:
            logger.error(f"Error descargando cerebro: {e}")
            
            # Intentar cargar versión local si existe
            return await self._load_local_backup()
    
    async def _load_local_backup(self) -> bool:
        """Intenta cargar una copia local del cerebro como respaldo"""
        try:
            # Buscar el archivo .index más reciente localmente
            index_files = list(LOCAL_BRAIN_PATH.glob("*.index"))
            if not index_files:
                logger.warning("No hay backups locales disponibles")
                return False
            
            # Ordenar por timestamp (vYYYYMMDD_HHMMSS)
            index_files.sort(reverse=True)
            latest_local = index_files[0].stem
            
            logger.info(f"Cargando backup local: {latest_local}")
            
            # Cargar índice
            with open(LOCAL_BRAIN_PATH / f"{latest_local}.index", 'rb') as f:
                self.faiss_index = faiss.deserialize_index(f.read())
            
            # Cargar metadatos
            with open(LOCAL_BRAIN_PATH / f"{latest_local}.pkl", 'rb') as f:
                self.metadata_map = pickle.loads(f.read())
            
            self.latest_version = latest_local
            logger.info(f"Backup local cargado: {self.faiss_index.ntotal} vectores")
            return True
            
        except Exception as e:
            logger.error(f"Error cargando backup local: {e}")
            return False
    
    def get_stats(self) -> Dict[str, Any]:
        """Retorna estadísticas del sistema cargado"""
        if not self.faiss_index:
            return {"status": "not_initialized"}
        
        return {
            "status": "initialized",
            "brain_version": self.latest_version,
            "total_vectors": self.faiss_index.ntotal,
            "total_metadata": len(self.metadata_map),
            "embedding_dim": self.faiss_index.d if hasattr(self.faiss_index, 'd') else None,
            "model": MODEL_NAME,
            "local_backup": True if list(LOCAL_BRAIN_PATH.glob("*.index")) else False,
        }
    
    async def create_initial_brain(self):
        """Crea un cerebro inicial vacío si no existe ninguno"""
        logger.info("Creando cerebro inicial vacío...")
        
        try:
            # Crear índice FAISS vacío con PQ
            index = faiss.IndexPQ(384, 12, 8)
            index = faiss.IndexIDMap(index)
            
            # Serializar
            index_bytes = faiss.serialize_index(index)
            metadata_bytes = pickle.dumps({})
            
            # Versión inicial
            version = "v0_initial"
            
            # Subir a DCellar
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
            
            # Crear brain_pointer
            pointer_data = {"latest_v": version}
            await put_object(
                "aisynergix/data/brain_pointer",
                json.dumps(pointer_data).encode(),
                content_type="application/json"
            )
            
            # Crear top10.json inicial
            top10_data = {
                "generated_at": "2024-01-01T00:00:00Z",
                "total_users": 0,
                "top10": [],
                "stats": {"avg_points": 0, "max_points": 0, "active_users": 0}
            }
            
            await put_object(
                "aisynergix/data/top10.json",
                json.dumps(top10_data, indent=2).encode(),
                content_type="application/json"
            )
            
            logger.info("Cerebro inicial creado en DCellar")
            return True
            
        except Exception as e:
            logger.error(f"Error creando cerebro inicial: {e}")
            return False

async def sync_brain():
    """
    Función principal ejecutada al iniciar el nodo
    """
    logger.info("=== INICIANDO SINCRONIZACIÓN DEL CEREBRO ===")
    
    sync = SyncBrain()
    
    try:
        # Inicializar sistema
        success = await sync.initialize()
        
        if not success:
            logger.warning("No se pudo inicializar desde DCellar. Creando cerebro inicial...")
            await sync.create_initial_brain()
            
            # Reintentar inicialización
            success = await sync.initialize()
        
        if success:
            stats = sync.get_stats()
            logger.info(f"Sistema sincronizado: {stats}")
            
            # Retornar instancia para uso por otros módulos
            return sync
        else:
            logger.error("No se pudo sincronizar el cerebro")
            return None
            
    except Exception as e:
        logger.error(f"Error en sincronización: {e}")
        return None

# ==================== INTEGRACIÓN CON RAG ENGINE ====================
async def initialize_rag_engine() -> Optional[RAGEngine]:
    """
    Función de conveniencia para inicializar el RAG Engine
    Usada por el módulo principal del bot
    """
    sync = await sync_brain()
    if sync and sync.rag_engine:
        return sync.rag_engine
    return None

async def get_brain_stats() -> Dict[str, Any]:
    """
    Obtiene estadísticas del cerebro actual
    """
    sync = SyncBrain()
    await sync.initialize()
    return sync.get_stats()

if __name__ == "__main__":
    # Para ejecución manual
    result = asyncio.run(sync_brain())
    if result:
        print("Sincronización exitosa")
        print(json.dumps(result.get_stats(), indent=2))
    else:
        print("Sincronización fallida")
        exit(1)
