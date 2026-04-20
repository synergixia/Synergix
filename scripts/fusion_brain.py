"""
Módulo 5 (2/2): Fusión de la Memoria Colectiva (fusion_brain.py)
---------------------------------------------------------
Cron de 10 Minutos que alimenta a la Mente Enjambre:
1. Revisa aportes nuevos en DCellar sin procesar.
2. Comprobación matemática Anti-Plagio (< 0.92 similitud de cosenos).
3. Escribe Top 10 JSON leyendo a cada Identidad directamente de Greenfield.
4. Genera la nueva memoria RAM comprimida FAISS y la publica.
"""

import asyncio
import json
import logging
import pickle
import time
from datetime import datetime
from collections import defaultdict
import tempfile

import faiss
import numpy as np
import torch
from sentence_transformers import SentenceTransformer

from aisynergix.services.greenfield import get_object, put_object, list_objects, get_user_metadata
from aisynergix.bot.identity import RANGOS

logger = logging.getLogger("synergix.fusion")

MODEL_PATH = "/aisynergix/ai/models/multilingual-e5-small"
SIMILARITY_THRESHOLD = 0.92

class FusionBrain:
    def __init__(self):
        self.model = None
        self.index = None
        self.metadata = []

    async def run(self):
        logger.info("⚡ CICLO FUSIÓN NEURONAL INICIADO")
        
        self.model = SentenceTransformer(MODEL_PATH, device="cpu", model_kwargs={"torch_dtype": torch.float16})
        self.model.eval()

        await self._pull_cortex()
        await self._assimilate_new_knowledge()
        await self._generate_leaderboard()

        logger.info("⚡ CICLO FUSIÓN NEURONAL FINALIZADO")

    async def _pull_cortex(self):
        """Descarga en caliente el índice actual desde memoria DCellar."""
        try:
            pt, _ = await get_object("aisynergix/data/brain_pointer")
            pt_v = json.loads(pt).get("latest_v", "v1")
            
            idx_b, _ = await get_object(f"aisynergix/data/brains/{pt_v}.index")
            meta_b, _ = await get_object(f"aisynergix/data/brains/{pt_v}.pkl")
            
            with tempfile.NamedTemporaryFile(suffix=".index", delete=False) as t:
                t.write(idx_b)
                t.flush()
                self.index = faiss.read_index(t.name)
            self.metadata = pickle.loads(meta_b)
            
        except Exception as e:
            logger.error(f"Caos en DCellar al jalar córtex, aborando fusión: {e}")
            raise e

    async def _assimilate_new_knowledge(self):
        """Barrido de todos los .txt del bucket en busca de conocimientos no indexados."""
        current_paths = {m.get("object_path") for m in self.metadata}
        todos_aportes = await list_objects("aisynergix/aportes/")
        
        nuevos = []
        for p, tgs in todos_aportes:
            if p not in current_paths and p.endswith(".txt"):
                nuevos.append((p, tgs))

        if not nuevos:
            return logger.info("💤 No hay pensamientos nuevos que procesar en la red.")

        logger.info(f"🌀 Asimilando {len(nuevos)} ideas frescas.")
        asimilados = 0

        for path, meta_tags in nuevos:
            raw, _ = await get_object(path)
            texto = raw.decode("utf-8", errors="ignore")
            
            with torch.no_grad():
                embed = self.model.encode(texto, convert_to_tensor=True, normalize_embeddings=True, device="cpu")
            np_emb = embed.cpu().numpy().astype(np.float32).reshape(1, -1)

            # Plagio estricto
            is_unique = True
            if self.index.ntotal > 0:
                dist, _ = self.index.search(np_emb, 1)
                if 1.0 - dist[0][0] >= SIMILARITY_THRESHOLD:
                    is_unique = False

            if is_unique:
                self.index.add(np_emb)
                self.metadata.append({
                    "text": texto,
                    "author_uid": meta_tags.get("author_uid", "unknown"),
                    "lang": meta_tags.get("lang", "es"),
                    "object_path": path
                })
                asimilados += 1

        # Generar Mutación del Córtex SI hubieron asimilados
        if asimilados > 0:
            nuevo_tag = f"v_{int(time.time())}"
            idx_bin = faiss.serialize_index(self.index)
            mt_bin = pickle.dumps(self.metadata)
            
            await put_object(f"aisynergix/data/brains/{nuevo_tag}.index", idx_bin)
            await put_object(f"aisynergix/data/brains/{nuevo_tag}.pkl", mt_bin)
            await put_object("aisynergix/data/brain_pointer", json.dumps({"latest_v": nuevo_tag}).encode("utf-8"))
            
            logger.info(f"🧬 Mutación Completada. Córtex saltó a {nuevo_tag} con {self.index.ntotal} ideas.")

    async def _generate_leaderboard(self):
        """Regla 2 y 5: Calcular y cachear masivamente las Almas Conectadas."""
        logger.info("🏆 Analizando rango inmortales en DCellar...")
        todos = await list_objects("aisynergix/users/")
        
        tabla = []
        for p, tgs in todos:
            # Replicamos hidratación rápida sin re-escrituras masivas
            pts = int(tgs.get("points", "0"))
            uid = p.split("/")[-1]
            rank_label = tgs.get("rank", "🌱 Iniciado")
            tabla.append((pts, uid, rank_label))

        tabla.sort(key=lambda x: x[0], reverse=True)
        top = []
        for i, (pts, uid, tg) in enumerate(tabla[:10]):
            top.append({"position": i+1, "points": pts, "uid_ofuscado": uid, "rank_name": tg})

        jsn = {
            "generated_at": datetime.utcnow().isoformat(),
            "total_users": len(todos),
            "top10": top
        }

        await put_object("aisynergix/data/top10.json", json.dumps(jsn).encode("utf-8"), content_type="application/json")


async def fusion_brain():
    fb = FusionBrain()
    try:
        await fb.run()
    except Exception as e:
        logger.error(f"Colapso en cron Fusion: {e}")
