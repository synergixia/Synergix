"""
fusion_brain.py — Tarea de 10 minutos de Evolución del Cerebro Colectivo.
Filtra aportes con quality_score > 7, reconstruye índice FAISS,
escanea metadatos de usuarios para compilar top10.json y sube todo a DCellar.
"""

import os
import json
import logging
import hashlib
import asyncio
from datetime import datetime, timezone
from typing import List, Dict, Any, Tuple

from aisynergix.services.greenfield import (
    list_objects,
    get_object,
    put_object,
    get_user_metadata
)
from aisynergix.services.rag_engine import rag_engine
from aisynergix.config.constants import (
    APORTES_PREFIX,
    BRAIN_PREFIX,
    BRAIN_POINTER_OBJECT,
    TOP10_JSON_OBJECT,
    USERS_PREFIX,
    LOCAL_BRAIN_DIR,
    LOCAL_DATA_DIR,
    LOCAL_INDEX_FILE,
    LOCAL_INDEX_META,
    LOCAL_TOP10_JSON_PATH,
    RAG_MIN_QUALITY_SCORE,
    get_rank_for_points
)

logger = logging.getLogger(__name__)


async def fetch_high_quality_aportes() -> List[Dict[str, Any]]:
    """
    Obtiene todos los aportes de Greenfield y filtra por calidad > 7.
    
    Returns:
        List[Dict]: Aportes de alta calidad listos para el índice
    """
    logger.info("[Fusion] Buscando aportes de alta calidad en Greenfield...")
    
    # Listar todos los aportes
    all_aportes_keys = await list_objects(APORTES_PREFIX)
    if not all_aportes_keys:
        logger.info("[Fusion] No se encontraron aportes en Greenfield.")
        return []
    
    high_quality_data = []
    processed_count = 0
    error_count = 0
    
    logger.info(f"[Fusion] Procesando {len(all_aportes_keys)} aportes...")
    
    for key in all_aportes_keys:
        try:
            # Descargar aporte
            raw_data = await get_object(key)
            if not raw_data:
                continue
            
            # Parsear JSON
            aporte_json = json.loads(raw_data.decode('utf-8'))
            content = aporte_json.get("content", "")
            metadata = aporte_json.get("metadata", {})
            
            # Extraer campos
            score = float(metadata.get("quality_score", 0))
            author_uid = metadata.get("author_uid", "unknown")
            categoria = metadata.get("categoria", "otro")
            timestamp = metadata.get("timestamp", "0")
            
            # Filtrar por calidad mínima
            if score >= RAG_MIN_QUALITY_SCORE:
                high_quality_data.append({
                    "text": content,
                    "author_uid": author_uid,
                    "score": score,
                    "categoria": categoria,
                    "timestamp": timestamp,
                    "source_key": key
                })
            
            processed_count += 1
            
            # Log cada 50 aportes
            if processed_count % 50 == 0:
                logger.info(f"[Fusion] Procesados {processed_count}/{len(all_aportes_keys)} aportes, "
                          f"{len(high_quality_data)} de alta calidad")
                
        except json.JSONDecodeError as e:
            logger.warning(f"[Fusion] JSON inválido en aporte {key}: {e}")
            error_count += 1
        except Exception as e:
            logger.error(f"[Fusion] Error procesando aporte {key}: {e}")
            error_count += 1
    
    logger.info(f"[Fusion] Procesamiento completado: "
                f"{processed_count} procesados, "
                f"{len(high_quality_data)} de alta calidad, "
                f"{error_count} errores")
    
    return high_quality_data


async def compile_top10_ranking() -> Dict[str, Any]:
    """
    Escanea metadatos de usuarios en Greenfield para compilar ranking Top 10.
    
    Returns:
        Dict: Datos para top10.json con ranking ordenado por puntos
    """
    logger.info("[Fusion] Compilando ranking Top 10 desde Greenfield...")
    
    try:
        # Listar usuarios
        user_keys = await list_objects(USERS_PREFIX)
        if not user_keys:
            logger.warning("[Fusion] No se encontraron usuarios en Greenfield")
            return {
                "ranking": [],
                "total_users": 0,
                "generated_at": datetime.now(timezone.utc).isoformat() + "Z"
            }
        
        users_data = []
        processed = 0
        
        # Procesar usuarios (limitar a 500 para no saturar)
        for key in user_keys[:500]:
            try:
                # Extraer UID
                uid = key.replace(f"{USERS_PREFIX}/", "")
                if not uid:
                    continue
                
                # Obtener metadatos
                tags = await get_user_metadata(uid)
                if not tags:
                    continue
                
                # Extraer información
                points = int(tags.get("points", 0))
                first_name = tags.get("first_name", "Usuario")
                rank = tags.get("rank", get_rank_for_points(points))
                welcomed = tags.get("welcomed", "false").lower() == "true"
                last_seen = int(tags.get("last_seen_ts", "0"))
                
                # Solo incluir usuarios que hayan hecho onboarding
                if welcomed and points > 0:
                    users_data.append({
                        "uid": uid,
                        "name": first_name,
                        "points": points,
                        "rank": rank,
                        "last_seen": last_seen
                    })
                
                processed += 1
                
                # Log cada 100 usuarios
                if processed % 100 == 0:
                    logger.debug(f"[Fusion] Procesados {processed}/{len(user_keys)} usuarios")
                    
            except Exception as e:
                logger.debug(f"[Fusion] Error procesando usuario {key}: {e}")
        
        # Ordenar por puntos (descendente)
        users_data.sort(key=lambda x: x["points"], reverse=True)
        
        # Tomar Top 10
        top10 = users_data[:10]
        
        # Formatear para frontend
        formatted_ranking = []
        for i, user in enumerate(top10, 1):
            formatted_ranking.append({
                "position": i,
                "name": user["name"],
                "points": user["points"],
                "rank": user["rank"],
                "uid": user["uid"][:8] + "..."  # Solo primeros 8 chars por privacidad
            })
        
        result = {
            "ranking": formatted_ranking,
            "total_users": len(user_keys),
            "active_users": len(users_data),  # Usuarios con puntos > 0
            "generated_at": datetime.now(timezone.utc).isoformat() + "Z",
            "total_points": sum(u["points"] for u in users_data)
        }
        
        logger.info(f"[Fusion] Ranking compilado: {len(user_keys)} usuarios totales, "
                   f"{len(users_data)} activos, {result['total_points']} puntos totales")
        
        return result
        
    except Exception as e:
        logger.error(f"[Fusion] Error compilando ranking: {e}", exc_info=True)
        # Retornar estructura vacía en caso de error
        return {
            "ranking": [],
            "total_users": 0,
            "active_users": 0,
            "generated_at": datetime.now(timezone.utc).isoformat() + "Z",
            "total_points": 0,
            "error": str(e)
        }


def calculate_file_hashes() -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Calcula hashes SHA-256 de los archivos locales del cerebro.
    
    Returns:
        Tuple: (index_hash, meta_hash, top10_hash) o None si el archivo no existe
    """
    def hash_file(path: str) -> Optional[str]:
        if not os.path.exists(path):
            return None
        try:
            with open(path, "rb") as f:
                return hashlib.sha256(f.read()).hexdigest()
        except Exception as e:
            logger.error(f"Error calculando hash de {path}: {e}")
            return None
    
    index_path = os.path.join(LOCAL_BRAIN_DIR, LOCAL_INDEX_FILE)
    meta_path = os.path.join(LOCAL_BRAIN_DIR, LOCAL_INDEX_META)
    top10_path = LOCAL_TOP10_JSON_PATH
    
    index_hash = hash_file(index_path)
    meta_hash = hash_file(meta_path)
    top10_hash = hash_file(top10_path)
    
    return index_hash, meta_hash, top10_hash


async def upload_brain_to_greenfield(
    index_hash: Optional[str],
    meta_hash: Optional[str],
    top10_hash: Optional[str]
) -> bool:
    """
    Sube el cerebro evolucionado a Greenfield (DCellar).
    
    Args:
        index_hash: Hash del índice FAISS
        meta_hash: Hash de los metadatos
        top10_hash: Hash del top10.json
    
    Returns:
        bool: True si todas las subidas fueron exitosas
    """
    logger.info("[Fusion] Subiendo cerebro evolucionado a Greenfield...")
    
    success_count = 0
    total_files = 3
    
    try:
        # 1. Subir índice FAISS
        idx_path = os.path.join(LOCAL_BRAIN_DIR, LOCAL_INDEX_FILE)
        if os.path.exists(idx_path):
            with open(idx_path, "rb") as f:
                content = f.read()
                success = await put_object(
                    f"{BRAIN_PREFIX}/{LOCAL_INDEX_FILE}",
                    content,
                    content_type="application/octet-stream"
                )
                if success:
                    success_count += 1
                    logger.info(f"[Fusion] ✅ Índice FAISS subido ({len(content)} bytes)")
                else:
                    logger.error("[Fusion] ❌ Error subiendo índice FAISS")
        else:
            logger.warning("[Fusion] Índice FAISS no encontrado localmente")
        
        # 2. Subir metadatos
        meta_path = os.path.join(LOCAL_BRAIN_DIR, LOCAL_INDEX_META)
        if os.path.exists(meta_path):
            with open(meta_path, "rb") as f:
                content = f.read()
                success = await put_object(
                    f"{BRAIN_PREFIX}/{LOCAL_INDEX_META}",
                    content,
                    content_type="application/json"
                )
                if success:
                    success_count += 1
                    logger.info(f"[Fusion] ✅ Metadatos subidos ({len(content)} bytes)")
                else:
                    logger.error("[Fusion] ❌ Error subiendo metadatos")
        else:
            logger.warning("[Fusion] Metadatos no encontrados localmente")
        
        # 3. Subir top10.json
        top10_path = LOCAL_TOP10_JSON_PATH
        if os.path.exists(top10_path):
            with open(top10_path, "rb") as f:
                content = f.read()
                success = await put_object(
                    TOP10_JSON_OBJECT,
                    content,
                    content_type="application/json"
                )
                if success:
                    success_count += 1
                    logger.info(f"[Fusion] ✅ Top10.json subido ({len(content)} bytes)")
                else:
                    logger.error("[Fusion] ❌ Error subiendo top10.json")
        else:
            logger.warning("[Fusion] Top10.json no encontrado localmente")
        
        # 4. Actualizar brain_pointer con hashes y timestamp
        if success_count > 0:
            pointer_data = {
                "version": "v" + str(int(datetime.now().timestamp())),
                "index_hash": index_hash,
                "meta_hash": meta_hash,
                "top10_hash": top10_hash,
                "uploaded_at": datetime.now(timezone.utc).isoformat() + "Z",
                "files_uploaded": success_count
            }
            
            success = await put_object(
                BRAIN_POINTER_OBJECT,
                json.dumps(pointer_data).encode("utf-8"),
                content_type="application/json"
            )
            
            if success:
                logger.info(f"[Fusion] ✅ Brain pointer actualizado: {pointer_data['version']}")
                return True
            else:
                logger.error("[Fusion] ❌ Error actualizando brain pointer")
                return False
        else:
            logger.error("[Fusion] ❌ No se subió ningún archivo, omitiendo brain pointer")
            return False
            
    except Exception as e:
        logger.error(f"[Fusion] Error subiendo cerebro a Greenfield: {e}", exc_info=True)
        return False


async def run_fusion():
    """
    Ejecuta el ciclo completo de fusión cerebral.
    
    Proceso:
    1. Filtrar aportes con quality_score > 7
    2. Reconstruir índice FAISS local
    3. Compilar top10.json desde metadatos de usuarios
    4. Calcular hashes de integridad
    5. Subir todo a Greenfield (DCellar)
    6. Actualizar brain_pointer
    """
    logger.info("[Fusion] 🧠 Iniciando ciclo de fusión cerebral...")
    
    start_time = datetime.now()
    
    try:
        # 1. Obtener aportes de alta calidad
        high_quality_data = await fetch_high_quality_aportes()
        
        if not high_quality_data:
            logger.info("[Fusion] No hay aportes nuevos de alta calidad. Nada que fusionar.")
            return
        
        logger.info(f"[Fusion] Fusionando {len(high_quality_data)} aportes de alta calidad")
        
        # 2. Reconstruir índice FAISS local
        rag_engine.rebuild_index(high_quality_data)
        
        # 3. Compilar ranking Top 10
        top10_data = await compile_top10_ranking()
        
        # Guardar top10.json localmente
        os.makedirs(LOCAL_DATA_DIR, exist_ok=True)
        with open(LOCAL_TOP10_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(top10_data, f, ensure_ascii=False, indent=2)
        
        logger.info(f"[Fusion] Top10.json guardado: {top10_data['total_users']} usuarios, "
                   f"Top {len(top10_data['ranking'])}")
        
        # 4. Calcular hashes de integridad
        index_hash, meta_hash, top10_hash = calculate_file_hashes()
        
        if index_hash and meta_hash:
            logger.info(f"[Fusion] Hashes calculados: "
                       f"índice={index_hash[:16]}..., "
                       f"meta={meta_hash[:16]}..., "
                       f"top10={top10_hash[:16] if top10_hash else 'N/A'}...")
        else:
            logger.warning("[Fusion] No se pudieron calcular todos los hashes")
        
        # 5. Subir todo a Greenfield
        upload_success = await upload_brain_to_greenfield(index_hash, meta_hash, top10_hash)
        
        # 6. Estadísticas finales
        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()
        
        stats = rag_engine.get_stats()
        
        if upload_success:
            logger.info(f"[Fusion] ✅ Fusión completada en {duration:.1f}s")
            logger.info(f"[Fusion] 📊 Resultado: {stats['total_contributions']} aportes, "
                       f"{stats['index_size']} vectores, "
                       f"{top10_data['total_users']} usuarios")
        else:
            logger.error(f"[Fusion] ❌ Fusión completada con errores en {duration:.1f}s")
            logger.info(f"[Fusion] 📊 Estado local: {stats['total_contributions']} aportes")
        
        return upload_success
        
    except Exception as e:
        logger.error(f"[Fusion] ❌ Error fatal en fusión cerebral: {e}", exc_info=True)
        return False


async def run_fusion_with_retry(max_retries: int = 2):
    """
    Ejecuta fusión cerebral con reintentos en caso de error.
    
    Args:
        max_retries: Intentos máximos antes de fallar
    """
    for attempt in range(max_retries + 1):
        try:
            if attempt > 0:
                logger.warning(f"[Fusion] Reintento {attempt}/{max_retries}...")
                await asyncio.sleep(5 * attempt)  # Backoff exponencial
            
            success = await run_fusion()
            if success:
                return True
                
        except Exception as e:
            logger.error(f"[Fusion] Error en intento {attempt}: {e}")
            if attempt >= max_retries:
                logger.critical(f"[Fusion] Fallo después de {max_retries + 1} intentos")
                return False
    
    return False


if __name__ == "__main__":
    # Configuración de logging para ejecución manual
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    
    # Ejecutar fusión
    asyncio.run(run_fusion_with_retry())
