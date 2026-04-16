"""
sync_brain.py — Sincronización de Ignición del Nodo Fantasma.
Descarga el cerebro colectivo desde BNB Greenfield al iniciar el servidor.
Incluye verificación SHA-256 de integridad y descarga de top10.json estático.
"""

import os
import json
import logging
import hashlib
import asyncio
from datetime import datetime
from typing import Optional, Dict, Any

from aisynergix.services.greenfield import (
    get_object,
    get_user_metadata,
    list_objects
)
from aisynergix.services.rag_engine import rag_engine
from aisynergix.config.constants import (
    BRAIN_POINTER_OBJECT,
    BRAIN_PREFIX,
    TOP10_JSON_OBJECT,
    LOCAL_BRAIN_DIR,
    LOCAL_DATA_DIR,
    LOCAL_INDEX_FILE,
    LOCAL_INDEX_META,
    LOCAL_TOP10_JSON_PATH,
    USERS_PREFIX
)

logger = logging.getLogger(__name__)


def calculate_sha256(filepath: str) -> Optional[str]:
    """
    Calcula el hash SHA-256 de un archivo.
    
    Args:
        filepath: Ruta al archivo
    
    Returns:
        Optional[str]: Hash hexadecimal o None si error
    """
    try:
        sha256_hash = hashlib.sha256()
        with open(filepath, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()
    except Exception as e:
        logger.error(f"Error calculando SHA-256 de {filepath}: {e}")
        return None


async def download_with_verification(
    object_key: str,
    local_path: str,
    expected_hash: Optional[str] = None
) -> bool:
    """
    Descarga un objeto de Greenfield con verificación de integridad opcional.
    
    Args:
        object_key: Clave del objeto en Greenfield
        local_path: Ruta local donde guardar
        expected_hash: Hash SHA-256 esperado (opcional)
    
    Returns:
        bool: True si la descarga y verificación fueron exitosas
    """
    try:
        logger.info(f"Descargando {object_key} → {local_path}")
        
        # Descargar de Greenfield
        content = await get_object(object_key)
        if content is None:
            logger.error(f"Objeto no encontrado: {object_key}")
            return False
        
        # Crear directorio si no existe
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        
        # Guardar localmente
        with open(local_path, "wb") as f:
            f.write(content)
        
        # Verificar hash si se proporciona
        if expected_hash:
            actual_hash = calculate_sha256(local_path)
            if actual_hash != expected_hash:
                logger.error(f"Hash mismatch para {object_key}: "
                           f"esperado {expected_hash[:16]}..., "
                           f"obtenido {actual_hash[:16] if actual_hash else 'None'}")
                os.remove(local_path)
                return False
            logger.debug(f"Hash verificado para {object_key}: {actual_hash[:16]}...")
        
        logger.info(f"✅ {object_key} descargado ({len(content)} bytes)")
        return True
        
    except Exception as e:
        logger.error(f"Error descargando {object_key}: {e}", exc_info=True)
        return False


async def sync_brain() -> bool:
    """
    Sincroniza el cerebro completo desde Greenfield.
    
    Proceso:
    1. Leer brain_pointer para obtener versión actual
    2. Descargar índice FAISS y metadatos con verificación SHA-256
    3. Descargar top10.json para ranking estático
    4. Cargar todo en el motor RAG
    
    Returns:
        bool: True si la sincronización fue exitosa
    """
    logger.info("🚀 SYNC_BRAIN: Iniciando sincronización desde Greenfield...")
    
    # 1. Obtener versión actual desde brain_pointer
    pointer_content = await get_object(BRAIN_POINTER_OBJECT)
    if pointer_content:
        try:
            pointer_data = json.loads(pointer_content.decode("utf-8"))
            version = pointer_data.get("version", "v1")
            index_hash = pointer_data.get("index_hash")
            meta_hash = pointer_data.get("meta_hash")
            top10_hash = pointer_data.get("top10_hash")
            logger.info(f"Brain pointer encontrado: versión {version}")
        except json.JSONDecodeError:
            logger.warning("Brain pointer no es JSON válido, usando valores por defecto")
            version = "v1"
            index_hash = meta_hash = top10_hash = None
    else:
        logger.warning("No se encontró brain_pointer. Iniciando cerebro vacío.")
        version = "v1"
        index_hash = meta_hash = top10_hash = None
    
    # 2. Descargar archivos del cerebro
    success_count = 0
    
    # Índice FAISS
    index_key = f"{BRAIN_PREFIX}/{LOCAL_INDEX_FILE}"
    index_path = os.path.join(LOCAL_BRAIN_DIR, LOCAL_INDEX_FILE)
    if await download_with_verification(index_key, index_path, index_hash):
        success_count += 1
    
    # Metadatos del índice
    meta_key = f"{BRAIN_PREFIX}/{LOCAL_INDEX_META}"
    meta_path = os.path.join(LOCAL_BRAIN_DIR, LOCAL_INDEX_META)
    if await download_with_verification(meta_key, meta_path, meta_hash):
        success_count += 1
    
    # 3. Descargar top10.json para ranking estático
    top10_path = LOCAL_TOP10_JSON_PATH
    if await download_with_verification(TOP10_JSON_OBJECT, top10_path, top10_hash):
        success_count += 1
        logger.info(f"Top10.json descargado en {top10_path}")
    else:
        # Crear top10.json vacío si no existe
        default_top10 = {
            "ranking": [],
            "total_users": 0,
            "generated_at": datetime.utcnow().isoformat() + "Z"
        }
        os.makedirs(LOCAL_DATA_DIR, exist_ok=True)
        with open(top10_path, "w", encoding="utf-8") as f:
            json.dump(default_top10, f, ensure_ascii=False)
        logger.info("Top10.json creado vacío (no había en Greenfield)")
    
    # 4. Cargar índice en el motor RAG
    if os.path.exists(index_path) and os.path.exists(meta_path):
        try:
            # El RAG engine ya carga automáticamente al inicializarse
            # pero forzamos recarga para asegurar la versión correcta
            rag_engine.load_index()
            
            stats = rag_engine.get_stats()
            logger.info(f"✅ Cerebro cargado: {stats['total_contributions']} aportes, "
                       f"{stats['index_size']} vectores")
            
            # Verificar que el índice se cargó correctamente
            if rag_engine.index and rag_engine.index.ntotal > 0:
                logger.info("🚀 Sincronización cerebral completada exitosamente")
                return True
            else:
                logger.warning("Índice cargado pero vacío o inválido")
                return False
                
        except Exception as e:
            logger.error(f"Error cargando índice RAG: {e}", exc_info=True)
            return False
    else:
        logger.warning("No se pudieron descargar los archivos del cerebro. "
                      "Iniciando con cerebro vacío.")
        
        # Inicializar cerebro vacío
        rag_engine._init_empty()
        return True


async def emergency_sync_users() -> Dict[str, Any]:
    """
    Sincronización de emergencia: descarga metadatos de usuarios para top10.json
    Útil cuando el archivo top10.json no existe o está corrupto.
    
    Returns:
        Dict: Datos para top10.json
    """
    logger.warning("Realizando sincronización de emergencia de usuarios...")
    
    try:
        # Listar usuarios
        user_keys = await list_objects(USERS_PREFIX)
        if not user_keys:
            logger.warning("No se encontraron usuarios en Greenfield")
            return {"ranking": [], "total_users": 0}
        
        users_data = []
        
        # Por cada usuario, obtener metadatos
        for key in user_keys[:100]:  # Limitar a 100 para no saturar
            try:
                # Extraer UID de la key
                uid = key.replace(f"{USERS_PREFIX}/", "")
                if not uid:
                    continue
                
                # Obtener tags del usuario
                tags = await get_user_metadata(uid)
                if not tags:
                    continue
                
                # Extraer información
                points = int(tags.get("points", 0))
                rank = tags.get("rank", "Iniciado")
                first_name = tags.get("first_name", "Usuario")
                
                users_data.append({
                    "uid": uid,
                    "name": first_name,
                    "points": points,
                    "rank": rank
                })
                
            except Exception as e:
                logger.debug(f"Error procesando usuario {key}: {e}")
        
        # Ordenar por puntos (descendente)
        users_data.sort(key=lambda x: x["points"], reverse=True)
        
        result = {
            "ranking": users_data[:10],  # Top 10
            "total_users": len(user_keys),
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "emergency_sync": True
        }
        
        logger.info(f"Sincronización de emergencia: {len(user_keys)} usuarios, "
                   f"top {len(users_data[:10])} extraídos")
        
        return result
        
    except Exception as e:
        logger.error(f"Error en sincronización de emergencia: {e}", exc_info=True)
        return {"ranking": [], "total_users": 0, "error": str(e)}


async def run_sync():
    """
    Punto de entrada para sincronización manual.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    
    success = await sync_brain()
    
    if success:
        logger.info("✅ Sincronización completada exitosamente")
        
        # Estadísticas finales
        stats = rag_engine.get_stats()
        logger.info(f"📊 Estadísticas RAG: {stats['total_contributions']} aportes, "
                   f"{stats['index_size']} vectores")
        
        # Verificar top10.json
        if os.path.exists(LOCAL_TOP10_JSON_PATH):
            with open(LOCAL_TOP10_JSON_PATH, "r", encoding="utf-8") as f:
                top10 = json.load(f)
            logger.info(f"🏆 Ranking: {top10.get('total_users', 0)} usuarios, "
                       f"Top {len(top10.get('ranking', []))}")
        else:
            logger.warning("⚠️ top10.json no encontrado localmente")
            
    else:
        logger.error("❌ Sincronización falló")
        
        # Intentar sincronización de emergencia
        logger.info("Intentando sincronización de emergencia...")
        emergency_data = await emergency_sync_users()
        
        # Guardar top10.json de emergencia
        os.makedirs(LOCAL_DATA_DIR, exist_ok=True)
        with open(LOCAL_TOP10_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(emergency_data, f, ensure_ascii=False, indent=2)
        
        logger.info(f"Top10.json de emergencia guardado: {emergency_data['total_users']} usuarios")
    
    return success


if __name__ == "__main__":
    # Ejecutar sincronización
    asyncio.run(run_sync())
