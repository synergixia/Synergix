"""
fusion_brain.py — Evolución de la Mente Colmena (Tarea de 10m).
Filtra aportes de alta calidad, compila el Top 10 global leyendo los 
archivos 0-bytes de usuarios, actualiza FAISS y sube todo a DCellar.
"""

import os
import json
import time
import logging
import asyncio
import re
from typing import List, Dict, Any

from aisynergix.config.constants import (
    APORTES_PREFIX,
    USERS_PREFIX,
    BRAIN_PREFIX,
    TOP10_OBJECT,
    BRAIN_POINTER_OBJECT,
    LOCAL_BRAIN_DIR,
    TOP10_LOCAL_PATH,
    RAG_MIN_QUALITY_SCORE
)
from aisynergix.services.greenfield import get_object, put_object, get_user_metadata
from aisynergix.services.rag_engine import rag_engine

logger = logging.getLogger(__name__)

async def _mock_list_objects(prefix: str) -> List[str]:
    """
    Simulación robusta de listado de objetos por prefijo en Greenfield.
    En producción real Web3, se parsearía el XML de respuesta del bucket.
    """
    import httpx
    from aisynergix.services.greenfield import GREENFIELD_SP_ENDPOINT, GREENFIELD_BUCKET, _build_signed_headers
    
    uri = f"/{GREENFIELD_BUCKET}/"
    query = {"prefix": prefix, "max-keys": "1000"}
    headers = _build_signed_headers("GET", uri, query, {}, b"")
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.get(f"{GREENFIELD_SP_ENDPOINT}{uri}", params=query, headers=headers)
            if response.status_code == 200:
                # Extracción rápida de keys mediante Regex asumiendo XML de S3/Greenfield
                keys = re.findall(r'<Key>(.*?)</Key>', response.text)
                return [k for k in keys if k != prefix and k != prefix + "/"]
            return []
        except Exception as e:
            logger.error(f"[Fusion] Error listando prefijo {prefix}: {e}")
            return []

async def compile_top10():
    """Escanea los metadatos de los usuarios (0-bytes files) para compilar el ranking."""
    logger.info("[Fusion] Compilando Top 10 Global...")
    user_keys = await _mock_list_objects(USERS_PREFIX)
    
    users_data = []
    for key in user_keys:
        uid_ofuscado = key.split("/")[-1]
        tags = await get_user_metadata(uid_ofuscado)
        if tags:
            points = int(tags.get("points", 0))
            users_data.append({
                "uid": uid_ofuscado,
                "points": points,
                "rank": tags.get("rank", "Iniciado")
            })
            
    # Ordenar por puntos (descendente)
    users_data.sort(key=lambda x: x["points"], reverse=True)
    top_10 = users_data[:10]
    
    payload = {
        "total_users": len(users_data),
        "top_10": top_10,
        "last_updated": int(time.time())
    }
    
    # 1. Guardar en local (Lectura rápida en memoria para el bot)
    with open(TOP10_LOCAL_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
        
    # 2. Subir a Greenfield (Persistencia Stateless)
    payload_bytes = json.dumps(payload).encode("utf-8")
    await put_object(TOP10_OBJECT, payload_bytes)
    logger.info(f"[Fusion] Top 10 compilado y subido. Total usuarios procesados: {len(users_data)}")

async def run_fusion():
    """Ejecuta el ciclo principal de fusión cerebral y rankings."""
    logger.info("🧠 [Fusion] Iniciando ciclo de Evolución (10m)...")
    
    # 1. Compilar el Top 10 primero
    await compile_top10()
    
    # 2. Reconstrucción del RAG
    aporte_keys = await _mock_list_objects(APORTES_PREFIX)
    if not aporte_keys:
        logger.info("[Fusion] No hay aportes nuevos para fusionar.")
        return

    high_quality_data = []
    for key in aporte_keys:
        # Extraemos el Uid ofuscado de la ruta /aisynergix/aportes/UID_TIMESTAMP.json
        filename = key.split("/")[-1]
        author_uid = filename.split("_")[0] if "_" in filename else "unknown"
        
        raw_content = await get_object(key)
        if raw_content:
            try:
                data = json.loads(raw_content.decode('utf-8'))
                # Nota: Idealmente filtramos usando un tag de Greenfield sin descargar el body completo.
                # Aquí asumimos que todos los archivos en este bucket pasaron el filtro del Juez previamente.
                high_quality_data.append({
                    "content": data.get("content", ""),
                    "author_uid": author_uid
                })
            except json.JSONDecodeError:
                continue

    if high_quality_data:
        logger.info(f"[Fusion] Integrando {len(high_quality_data)} aportes al RAG...")
        rag_engine.rebuild_index(high_quality_data)
        
        # Subir índices a Greenfield
        idx_path = os.path.join(LOCAL_BRAIN_DIR, "brain.index")
        meta_path = os.path.join(LOCAL_BRAIN_DIR, "brain_meta.json")
        
        with open(idx_path, 'rb') as f:
            await put_object(f"{BRAIN_PREFIX}/brain.index", f.read())
            
        with open(meta_path, 'rb') as f:
            await put_object(f"{BRAIN_PREFIX}/brain_meta.json", f.read())
            
        # Puntero
        timestamp_str = str(int(time.time())).encode('utf-8')
        await put_object(BRAIN_POINTER_OBJECT, timestamp_str)
        
        logger.info("✅ [Fusion] Cerebro sincronizado y respaldado en DCellar.")
