"""
MÃ³dulo 5 (1/2): Arranque DCellar (sync_brain.py)
---------------------------------------------------------
Descarga o inicializa FAISS con FlatL2 universal pre-inyectado.
"""
import asyncio
import json
import logging
import pickle
import numpy as np
from pathlib import Path
import faiss
from sentence_transformers import SentenceTransformer
from aisynergix.services.greenfield import get_object, put_object

logger = logging.getLogger("synergix.sync")
MODEL_NAME = "intfloat/multilingual-e5-small"
LOCAL_MODEL_PATH = Path("/aisynergix/ai/models/multilingual-e5-small")
LOCAL_BRAIN_PATH = Path("/aisynergix/data/brains")

class NodeIgnition:
    def __init__(self):
        self.model = None

    async def _ensure_directories(self):
        LOCAL_BRAIN_PATH.mkdir(parents=True, exist_ok=True)
        LOCAL_MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)

    async def pull_brain(self) -> bool:
        logger.info("ðŸ“¡ Iniciando Protocolo DCellar: Descarga de Intelecto...")
        await self._ensure_directories()
        try:
            if LOCAL_MODEL_PATH.exists() and len(list(LOCAL_MODEL_PATH.iterdir())) > 2:
                logger.info("âœ… Transformer local en volumen activado.")
            else:
                logger.info(f"â³ Descargando Sentinel Vectorial...")
                self.model = SentenceTransformer(MODEL_NAME, device="cpu", model_kwargs={"torch_dtype": "float16"})
                self.model.save(str(LOCAL_MODEL_PATH))
                logger.info("âœ… Transformer salvado permanentemente.")

            try:
                pointer_raw, _ = await get_object("aisynergix/data/brain_pointer")
                latest_v = json.loads(pointer_raw.decode("utf-8")).get("latest_v", "v1")
            except Exception as e:
                logger.warning(f"Excepcion DCellar jalar pointer. Forjando GÃ©nesis...")
                await self.create_genesis()
            return True
        except Exception as e:
            logger.error(f"Fallo Masivo en Arranque: {e}")
            return False

    async def create_genesis(self):
        """FlatL2 simple evitando numpy arrays multidimensionales y truth-values en IndexIDMap."""
        logger.info("ðŸ”® Forjando el GÃ©nesis FAISS (FlatL2)...")
        d = 384
        
        index_raw = faiss.IndexFlatL2(d)
        index = faiss.IndexIDMap(index_raw)
        
        vec = np.zeros((1, d), dtype=np.float32)
        idx = np.array([0], dtype=np.int64)
        index.add_with_ids(vec, idx)

        # ====== EL PARCHE MÃGICO: .tobytes() ======
        idx_bytes = faiss.serialize_index(index).tobytes()
        # ==========================================
        meta_bytes = pickle.dumps([])
        
        await put_object("aisynergix/data/brains/v1_genesis.index", idx_bytes, content_type="application/octet-stream")
        await put_object("aisynergix/data/brains/v1_genesis.pkl", meta_bytes, content_type="application/octet-stream")
        await put_object("aisynergix/data/brain_pointer", json.dumps({"latest_v": "v1_genesis"}).encode("utf-8"), content_type="application/json")
        logger.info("ðŸŒ± El GÃ©nesis ha nacido libre de bugs.")


async def sync_brain() -> bool:
    ign = NodeIgnition()
    return await ign.pull_brain()
