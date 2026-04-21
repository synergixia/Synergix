import os
import json
import time
import logging
import asyncio
from datetime import datetime
import numpy as np

try:
    import faiss
except ImportError:
    faiss = None

from aisynergix.services.greenfield import greenfield
from aisynergix.bot.identity import get_rank_info
from aisynergix.services.rag_engine import rag

logger = logging.getLogger(__name__)

async def run_federated_evolution():
    """
    1. Cron Job: Evolución Federada (Cada 10 minutos).
    Lee los nuevos aportes recien subidos a la Memoria Inmortal (Greenfield),
    los vectoriza vía AI local y forja una nueva versión del cerebo global (.index).
    """
    logger.info("⚡ Iniciando Evolución Federada y Fusión Cerebral...")
    
    # 1. Recuperar memoria actual del mes
    date_folder = time.strftime("%Y-%m")
    aportes_brutos = await greenfield.list_recent_aportes(date_folder)
    
    if not aportes_brutos:
        logger.info("Sin aportes nuevos. Fusión skipeada.")
        return

    # En producción real aquí compararíamos la última V de brain vs la cantidad de files
    # para inyectar solos los deltas. Para la PoC/Stateless se reconstruye index fresco rápido 
    # dado que e5-small y FAISS RAM manejan decenas de miles en milisegundos.
    
    brain_version_id = f"v_{int(time.time())}"
    
    # 2. Re-vectorización Masiva y Fusión
    new_vectors = []
    metadata_cache = []
    
    rag.initialize_model() # Asegurar Float16 engine en RAM
    
    for obj in aportes_brutos:
        path = obj.get("object_name", "")
        tags = obj.get("tags", {}) # En API REST S3 esto requiere stat file o check custom, para el ejemplo asumimos payload hidratada
        
        # Leemos el texto puro on-chain
        try:
            content_text = await greenfield.read_aporte(path)
            vector = rag.vectorize(content_text)
            
            new_vectors.append(vector)
            metadata_cache.append({
                "path": path,
                "content": content_text[:300] + "...", # Recorte para context-window seguro
                "author_uid": tags.get("author_uid", "unknown"),
                "cid": path[-15:]
            })
        except Exception as e:
            logger.error(f"Fallo vectorizando fragmento {path}: {str(e)}")

    if not new_vectors:
         return
         
    # 3. Entrenamiento IndexIVFPQ de FAISS en RAM 
    # (Usamos IndexFlatIP localmente para el proof-of-concept por velocidad asíncrona sobre arrays pequeños)
    dimension = 384
    index_ram = faiss.IndexFlatIP(dimension) 
    
    v_matrix = np.array(new_vectors, dtype=np.float32)
    faiss.normalize_L2(v_matrix) # E5 require normalizar
    index_ram.add(v_matrix)

    # 4. Inyección a Storage On-Chain 
    # El API nativo de faiss.write_index exporta a disco. Bajo Stateless: 
    # Volcamos a un archivo /tmp y lo leemos en binario (Unica excepción temporal)
    tmp_path = f"/tmp/{brain_version_id}.index"
    faiss.write_index(index_ram, tmp_path)
    
    with open(tmp_path, "rb") as f:
        binary_index_data = f.read()
        
    await greenfield._execute_request(
        "PUT", 
        f"aisynergix/data/brains/{brain_version_id}.index", 
        content=binary_index_data
    )
    os.remove(tmp_path)
    
    # Subida Metadata json
    meta_json = json.dumps(metadata_cache, ensure_ascii=False).encode('utf-8')
    await greenfield._execute_request(
        "PUT", 
        f"aisynergix/data/brains/{brain_version_id}_meta.json", 
        content=meta_json
    )

    # 5. Promocionar el Brain Pointer final
    await greenfield.update_brain_pointer(brain_version_id)
    
    # Refresh local en la instancia IA en VIVO sin bajar el nodo bot
    await rag.sync_brain_to_ram()
    logger.info(f"✅ Evolución {brain_version_id} exitosa. Red neuronal viva actualizada.")


async def execute_daily_cleansing():
    """
    2. Cron Job Diario (00:00 UTC):
    - Subida de logs por transparencia general
    - UpdateObjectMetadata masivo en users para setear 'daily_aportes_count' = 0
    (Para fines prácticos de prueba, ejecuta el script o lo llama apscheduler).
    """
    logger.info("🧹 Iniciando Auditoria y Limpieza Diaria...")
    
    # Reset SPAM filters 
    # (Iterador distribuido, en Greenfield real usaríamos script de bash/bambo con concurrency en bash, aquí lógica bot Python)
    try:
        # Nota: La paginacion no es cubierta por completo en ListObjects para un script asi de rapido, 
        # asumimos payload lista reducida o un sub-indexer API para el loop "users/*".
        response = await greenfield._execute_request("GET", "?prefix=aisynergix/users/")
        users_files = response.json().get("objects", [])
        
        for u in users_files:
             uid_ofus = u.get("object_name", "").split("/")[-1]
             # Mutacion asincronica sin tocar el points tag
             current_tags = await greenfield.get_user_tags(uid_ofus)
             if current_tags:
                 current_tags["daily_aportes_count"] = 0
                 # Rangos hydratador: Aprovechamos de revisar upgrade pasivo
                 new_rank, _ = get_rank_info(current_tags["points"])
                 if new_rank != current_tags["rank"]:
                     current_tags["rank"] = new_rank
                     # Aquí despacharíamos un mensaje bot Rank UP asincrono para el bot
                 await greenfield.update_user_tags(uid_ofus, current_tags)
                 
    except Exception as e:
         logger.error(f"Falla rutinaria de reset: {str(e)}")

    logger.info("✅ Reseteo diario concluido.")
    
async def generate_weekly_challenge():
    """
    3. Cron Job Semanal (Lunes 00:00 UTC): Forja retos autonómos técnicos
    (Puntos extra de regalía).
    """
    ts = int(time.time())
    logger.info("🎲 Lanzando Reto Semanal Autónomo...")
    
    # Se le puede inyectar logica LLM al pensador (local_ia) para generar creatividad técnica
    challenge_payload = {
         "title": f"Synergix Challenge {ts}",
         "description": "Explora y detalla vulnerabilidades L2 en la nueva era Rollups.",
         "multiplier": 5,
         "ts": ts
    }
    
    content = json.dumps(challenge_payload, ensure_ascii=False).encode('utf-8')
    await greenfield._execute_request("PUT", f"aisynergix/data/challenges/{ts}.json", content=content)
    logger.info("✅ Challenge Forjado en Storage Master.")
