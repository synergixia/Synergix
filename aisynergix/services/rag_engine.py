import os
import json
import time                                                                                          import hashlib                                                                                       import asyncio
import numpy as np
from typing import List, Dict, Any, Optional, Tuple                                                  from pathlib import Path                                                                             
import faiss                                                                                         from sentence_transformers import SentenceTransformer                                                                                                                                                     from aisynergix.services.greenfield import greenfield                                                
EMBEDDING_MODEL_NAME = "intfloat/multilingual-e5-small"                                              VECTOR_DIM = 384
N_CLUSTERS = 16
N_PROBE = 4
M_SUBQUANTIZERS = 32
BITS_PER_CODE = 8                                                                                    BRAIN_DIR = Path("/tmp/synergix_brains")
INDEX_FILE = BRAIN_DIR / "faiss_ivfpq.index"
META_FILE = BRAIN_DIR / "metadata.json"
TOP_K_DEFAULT = 5
                                                                                                                                                                                                          def _ensure_dir() -> None:
    BRAIN_DIR.mkdir(parents=True, exist_ok=True)                                                                                                                                                                                                                                                               class RAGEngine:
    def __init__(self):                                                                                      self.model: Optional[SentenceTransformer] = None                                                     self.index: Optional[faiss.Index] = None
        self.metadata: List[Dict[str, Any]] = []
        self._lock = asyncio.Lock()                                                                          self._loaded = False                                                                                 self._training_samples: List[np.ndarray] = []                                                                                                                                                         @property
    def is_loaded(self) -> bool:                                                                             return self._loaded

    async def load_model(self) -> None:                                                                      if self.model is not None:
            return
        loop = asyncio.get_event_loop()                                                                      self.model = await loop.run_in_executor(                                                                 None,                                                                                                lambda: SentenceTransformer(EMBEDDING_MODEL_NAME, device="cpu")
        )                                                                                                    self.model.half()

    async def _encode(self, texts: List[str], instruction: str = "") -> np.ndarray:
        if self.model is None:                                                                                   await self.load_model()                                                                          loop = asyncio.get_event_loop()
        if instruction and hasattr(self.model, 'encode'):                                                        pass
        if instruction:                                                                                          input_texts = [f"{instruction}: {t}" for t in texts]                                             else:
            input_texts = texts                                                                              embeddings = await loop.run_in_executor(                                                                 None,                                                                                                lambda: self.model.encode(                                                                               input_texts,
                batch_size=16,                                                                                       show_progress_bar=False,
                normalize_embeddings=True,                                                                           convert_to_numpy=True,                                                                           )                                                                                                )
        return embeddings.astype(np.float32)                                                         
    async def _load_index_from_greenfield(self) -> bool:                                                     try:                                                                                                     data = await greenfield.get_object("aisynergix/data/brains/faiss_ivfpq.index")
            _ensure_dir()                                                                                        INDEX_FILE.write_bytes(data)                                                                         self.index = faiss.read_index(str(INDEX_FILE))                                                       if self.index.ntotal > N_CLUSTERS * 2:                                                                   try:                                                                                                     self.index.nprobe = N_PROBE
                except Exception:                                                                                        pass
            return True                                                                                      except Exception:
            return False                                                                             
    async def _load_metadata_from_greenfield(self) -> bool:                                                  try:
            data = await greenfield.get_object_text("aisynergix/data/brains/metadata.json")                      self.metadata = json.loads(data)                                                                     return True                                                                                      except Exception:                                                                                        self.metadata = []                                                                                   return False

    async def initialize(self) -> None:                                                                      async with self._lock:                                                                                   await self.load_model()                                                                              idx_loaded = await self._load_index_from_greenfield()                                                meta_loaded = await self._load_metadata_from_greenfield()
            if not idx_loaded:                                                                                       self._init_empty_index()                                                                             self.metadata = []                                                                               if not meta_loaded and self.index is not None and self.index.ntotal > 0:
                self.metadata = [{"id": f"vec_{i}", "ts": 0, "lang": "unknown"} for i in range(self.index.ntotal)]                                                                                                    self._loaded = True                                                                                                                                                                               def _init_empty_index(self) -> None:                                                                     quantizer = faiss.IndexFlatL2(VECTOR_DIM)                                                            self.index = faiss.IndexIVFPQ(                                                                           quantizer, VECTOR_DIM, N_CLUSTERS, M_SUBQUANTIZERS, BITS_PER_CODE                                )                                                                                                    self.index.nprobe = N_PROBE

    def _init_trained_index(self, vectors: np.ndarray) -> None:                                              quantizer = faiss.IndexFlatL2(VECTOR_DIM)
        n_clusters_actual = min(N_CLUSTERS, vectors.shape[0] // 2)
        if n_clusters_actual < 2:
            n_clusters_actual = 2
        self.index = faiss.IndexIVFPQ(
            quantizer, VECTOR_DIM, n_clusters_actual, M_SUBQUANTIZERS, BITS_PER_CODE
        )
        self.index.train(vectors.astype(np.float32))                                                         self.index.nprobe = N_PROBE

    async def add_texts(self, texts: List[str], metadata_list: List[Dict[str, Any]]) -> int:
        if not texts:
            return 0
        async with self._lock:
            embeddings = await self._encode(texts)
            return await self._add_vectors(embeddings, metadata_list)                                
    async def _add_vectors(self, vectors: np.ndarray, metadata_list: List[Dict[str, Any]]) -> int:
        vectors = vectors.astype(np.float32)
        if vectors.ndim == 1:
            vectors = vectors.reshape(1, -1)
        n = vectors.shape[0]
        if self.index is None or not self.index.is_trained:
            if len(self._training_samples) < N_CLUSTERS * 2:
                self._training_samples.extend([v for v in vectors])                                                  return 0                                                                                         all_samples = np.vstack(self._training_samples)
            self._init_trained_index(all_samples)                                                                self._training_samples.clear()
        if self.index is not None and self.index.is_trained:
            self.index.add(vectors)
            start_idx = len(self.metadata)
            for i in range(n):                                                                                       meta = metadata_list[i] if i < len(metadata_list) else {}                                            entry = {                                                                                                "id": f"vec_{start_idx + i}",
                    "ts": meta.get("ts", int(time.time())),
                    "lang": meta.get("lang", "unknown"),                                                                 "author_uid": meta.get("author_uid", ""),                                                            "quality_score": meta.get("quality_score", "0"),
                    "path": meta.get("path", ""),
                }
                self.metadata.append(entry)
            return n
        return 0

    async def search(
        self, query: str, top_k: int = TOP_K_DEFAULT, lang_filter: Optional[str] = None
    ) -> List[Dict[str, Any]]:                                                                               async with self._lock:
            if self.index is None or self.index.ntotal == 0:
                return []                                                                                        query_embedding = await self._encode([query], instruction="query")                                   query_embedding = query_embedding.astype(np.float32).reshape(1, -1)                                  k = min(top_k, self.index.ntotal)
            distances, indices = self.index.search(query_embedding, k)
            results: List[Dict[str, Any]] = []
            for dist, idx in zip(distances[0], indices[0]):
                if idx < 0 or idx >= len(self.metadata):
                    continue
                meta = self.metadata[idx]
                score = float(1.0 / (1.0 + float(dist))) if dist > 0 else 1.0
                if lang_filter and meta.get("lang") != lang_filter:
                    score *= 0.85
                results.append({                                                                                         "score": score,
                    "distance": float(dist),
                    "metadata": meta,                                                                                    "index": int(idx),                                                                               })                                                                                               results.sort(key=lambda x: x["score"], reverse=True)
            return results

    async def get_context_for_query(
        self, query: str, top_k: int = TOP_K_DEFAULT, lang_filter: Optional[str] = None                  ) -> Tuple[str, List[Dict[str, Any]]]:
        hits = await self.search(query, top_k=top_k, lang_filter=lang_filter)
        if not hits:                                                                                             return ("", [])
        context_parts: List[str] = []                                                                        enriched: List[Dict[str, Any]] = []                                                                  for h in hits:                                                                                           meta = h["metadata"]
            path = meta.get("path", "")                                                                          text = ""
            if path:                                                                                                 try:
                    text = await greenfield.get_object_text(path)                                                    except Exception:                                                                                        text = f"[Aporte {meta.get('id', '?')}]"                                                     if not text:                                                                                             text = f"[Aporte {meta.get('id', '?')}]"                                                         context_parts.append(text)                                                                           enriched.append({**h, "text": text})                                                             return ("\n---\n".join(context_parts), enriched)                                                                                                                                                      async def save_to_greenfield(self) -> bool:
        async with self._lock:
            if self.index is None or self.index.ntotal == 0:                                                         return False
            _ensure_dir()                                                                                        faiss.write_index(self.index, str(INDEX_FILE))
            index_bytes = INDEX_FILE.read_bytes()                                                                meta_json = json.dumps(self.metadata, ensure_ascii=False)                                            try:                                                                                                     await greenfield.create_object(
                    "aisynergix/data/brains/faiss_ivfpq.index",                                                          index_bytes,                                                                                         {"ntotal": str(self.index.ntotal), "updated": str(int(time.time()))},                                content_type="application/octet-stream",
                )                                                                                                    await greenfield.create_object(
                    "aisynergix/data/brains/metadata.json",                                                              meta_json.encode("utf-8"),
                    {"entries": str(len(self.metadata)), "updated": str(int(time.time()))},
                    content_type="application/json",                                                                 )
                return True
            except Exception as e:
                return False                                                                                                                                                                                  async def evolve_from_recent_aportes(self, minutes: int = 10) -> int:                                    aportes = await greenfield.list_recent_aportes(minutes=minutes)
        if not aportes:                                                                                          return 0
        texts: List[str] = []                                                                                metas: List[Dict[str, Any]] = []                                                                     for obj in aportes:                                                                                      name = obj.get("name", obj.get("Name", obj.get("object_name", "")))                                  if not name or not name.endswith(".txt"):                                                                continue                                                                                         try:
                content = await greenfield.get_object_text(name)                                                     tags = await greenfield.get_aporte_tags(name)
            except Exception:
                continue
            if not content or len(content.strip()) < 20:
                continue                                                                                         try:
                ts_part = name.rsplit("/", 1)[-1].replace(".txt", "").rsplit("_", 1)[-1]
                obj_ts = int(ts_part)                                                                            except (ValueError, IndexError):
                obj_ts = int(time.time())                                                                        texts.append(content)
            metas.append({                                                                                           "ts": obj_ts,                                                                                        "lang": tags.get("lang", "unknown"),
                "author_uid": tags.get("author_uid", ""),                                                            "quality_score": tags.get("quality_score", "0"),
                "path": name,                                                                                    })
        if texts:                                                                                                added = await self.add_texts(texts, metas)
            if added > 0:
                await self.save_to_greenfield()
                new_version = hashlib.sha256(f"{self.index.ntotal}_{time.time()}".encode()).hexdigest()[:16]
                await greenfield.set_brain_pointer(f"v{new_version}")
            return added
        return 0                                                                                     
    async def get_stats(self) -> Dict[str, Any]:                                                             async with self._lock:
            return {                                                                                                 "total_vectors": self.index.ntotal if self.index else 0,
                "metadata_entries": len(self.metadata),
                "index_trained": self.index.is_trained if self.index else False,
                "loaded": self._loaded,                                                                              "model_loaded": self.model is not None,
            }

                                                                                                     rag_engine = RAGEngine()
