import hashlib
import asyncio
import pickle
from typing import List, Dict, Optional, Tuple, Any, Set
from pathlib import Path
from datetime import datetime, timezone

import numpy as np
import faiss
from sentence_transformers import SentenceTransformer

EMBEDDING_MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
EMBEDDING_DIM = 384
N_CENTROIDS = 64
M_SUBQUANTIZERS = 48
N_BITS = 8
TOP_K_RESULTS = 10
TEMP_INDEX_DIR = Path("/tmp/synergix_faiss")
TEMP_INDEX_DIR.mkdir(parents=True, exist_ok=True)

BRAIN_CODES: List[str] = ["prog", "tech", "cien", "know"]

# Maps Judge category → brain code
CATEGORY_TO_BRAIN: Dict[str, str] = {
    "programacion": "prog",
    "tecnologia":   "tech",
    "ciencia":      "cien",
    "filosofia":    "know",
    "arte":         "know",
    "vida":         "know",
    "espiritualidad": "know",
    "economia":     "know",
    "naturaleza":   "know",
    "sociedad":     "know",
    "innovacion":   "know",
}

_QUERY_PREFIX = "query: "
_PASSAGE_PREFIX = "passage: "

# Shared embedding model — loaded once, reused by all 4 FAISSEngines
_shared_model: Optional[SentenceTransformer] = None
_model_lock: Optional[asyncio.Lock] = None


async def _get_shared_model() -> SentenceTransformer:
    global _shared_model, _model_lock
    if _model_lock is None:
        _model_lock = asyncio.Lock()
    async with _model_lock:
        if _shared_model is None:
            _shared_model = await asyncio.to_thread(
                lambda: SentenceTransformer(
                    EMBEDDING_MODEL_NAME,
                    device="cpu",
                    cache_folder="/tmp/st_cache",
                )
            )
    return _shared_model


class FAISSEngine:
    def __init__(self, code: str):
        self._code = code
        self._index: Optional[faiss.Index] = None
        self._metadata: Dict[int, Dict[str, Any]] = {}
        self._current_id: int = 0
        self._indexed_objects: Set[str] = set()
        self._lock = asyncio.Lock()
        self._is_trained = False

    @property
    def code(self) -> str:
        return self._code

    @property
    def indexed_objects(self) -> Set[str]:
        return self._indexed_objects

    @property
    def total_documents(self) -> int:
        if self._index is None:
            return 0
        return self._index.ntotal

    async def _ensure_index(self):
        if self._index is not None:
            return

        quantizer = faiss.IndexFlatL2(EMBEDDING_DIM)
        self._index = faiss.IndexIVFPQ(
            quantizer, EMBEDDING_DIM, N_CENTROIDS, M_SUBQUANTIZERS, N_BITS
        )

        index_path = TEMP_INDEX_DIR / f"{self._code}_brain.index"
        meta_path = TEMP_INDEX_DIR / f"{self._code}_metadata.pkl"

        if index_path.exists() and meta_path.exists():
            try:
                self._index = await asyncio.to_thread(
                    lambda: faiss.read_index(str(index_path))
                )
                with open(meta_path, "rb") as f:
                    saved = pickle.load(f)
                    self._metadata = saved.get("metadata", {})
                    self._current_id = saved.get("current_id", 0)
                    self._indexed_objects = saved.get("indexed_objects", set())
                if isinstance(self._index, faiss.IndexIVFPQ):
                    self._is_trained = self._index.is_trained
                if not self._is_trained:
                    await self._train_with_dummy()
                return
            except Exception:
                quantizer = faiss.IndexFlatL2(EMBEDDING_DIM)
                self._index = faiss.IndexIVFPQ(
                    quantizer, EMBEDDING_DIM, N_CENTROIDS, M_SUBQUANTIZERS, N_BITS
                )

        await self._train_with_dummy()

    async def _train_with_dummy(self):
        n_train = max(10_000, N_CENTROIDS * 160)
        dummy = np.random.randn(n_train, EMBEDDING_DIM).astype(np.float32)
        faiss.normalize_L2(dummy)
        await asyncio.to_thread(self._index.train, dummy)
        self._is_trained = True

    async def _save_index(self):
        index_path = TEMP_INDEX_DIR / f"{self._code}_brain.index"
        meta_path = TEMP_INDEX_DIR / f"{self._code}_metadata.pkl"
        await asyncio.to_thread(faiss.write_index, self._index, str(index_path))
        with open(meta_path, "wb") as f:
            pickle.dump({
                "metadata": self._metadata,
                "current_id": self._current_id,
                "indexed_objects": self._indexed_objects,
            }, f)

    async def load_from_bytes(self, index_bytes: bytes, meta_dict: Dict[str, Any]) -> None:
        """Load index binary + meta from Irys into this engine."""
        tmp_path = TEMP_INDEX_DIR / f"{self._code}_import.index"
        with open(tmp_path, "wb") as f:
            f.write(index_bytes)
        self._index = await asyncio.to_thread(faiss.read_index, str(tmp_path))
        tmp_path.unlink(missing_ok=True)
        if isinstance(self._index, faiss.IndexIVFPQ):
            self._is_trained = self._index.is_trained
        self._indexed_objects = set(meta_dict.get("indexed_objects", []))
        self._metadata = {
            int(k): v for k, v in meta_dict.get("vector_meta", {}).items()
        }
        self._current_id = meta_dict.get("current_id", 0)
        # Cache locally so the next startup can skip an Irys download
        await self._save_index()

    async def serialize_index(self) -> bytes:
        """Serialize current FAISS index to bytes for Irys upload."""
        await self._ensure_index()
        tmp_path = TEMP_INDEX_DIR / f"{self._code}_export.index"
        await asyncio.to_thread(faiss.write_index, self._index, str(tmp_path))
        with open(tmp_path, "rb") as f:
            data = f.read()
        tmp_path.unlink(missing_ok=True)
        return data

    async def add_batch(
        self, documents: List[Tuple[str, Dict[str, Any]]]
    ) -> List[int]:
        if not documents:
            return []
        async with self._lock:
            await self._ensure_index()
            model = await _get_shared_model()
            texts = [doc[0] for doc in documents]
            metadatas = [doc[1] for doc in documents]

            input_texts = [f"{_PASSAGE_PREFIX}{t}" for t in texts]
            embeddings = await asyncio.to_thread(
                lambda: model.encode(
                    input_texts,
                    normalize_embeddings=True,
                    show_progress_bar=False,
                    batch_size=32,
                )
            )
            embeddings = embeddings.astype(np.float32)

            start_id = self._current_id
            ids = np.arange(start_id, start_id + len(documents), dtype=np.int64)
            self._index.add_with_ids(embeddings, ids)

            for i, meta in enumerate(metadatas):
                doc_id = start_id + i
                self._metadata[doc_id] = {
                    **meta,
                    "text": texts[i],
                    "text_hash": hashlib.sha256(texts[i].encode()).hexdigest()[:16],
                }
                obj_name = meta.get("object_name", "")
                if obj_name:
                    self._indexed_objects.add(obj_name)

            self._current_id = start_id + len(documents)
            await self._save_index()
            return list(range(start_id, start_id + len(documents)))

    async def search(
        self, query: str, top_k: int = TOP_K_RESULTS
    ) -> List[Dict[str, Any]]:
        async with self._lock:
            await self._ensure_index()
            if self._index.ntotal == 0:
                return []

            model = await _get_shared_model()
            query_emb = await asyncio.to_thread(
                lambda: model.encode(
                    [f"{_QUERY_PREFIX}{query}"],
                    normalize_embeddings=True,
                    show_progress_bar=False,
                )[0].astype(np.float32)
            )
            vector = query_emb.reshape(1, -1)
            effective_k = min(top_k, self._index.ntotal)
            distances, indices = await asyncio.to_thread(
                self._index.search, vector, effective_k
            )

            results = []
            for distance, idx in zip(distances[0], indices[0]):
                if idx < 0:
                    continue
                meta = self._metadata.get(int(idx), {})
                results.append({
                    "id": int(idx),
                    "score": float(1.0 / (1.0 + distance)),
                    "text": meta.get("text", ""),
                    "metadata": {k: v for k, v in meta.items() if k != "text"},
                    "brain": self._code,
                })
            return results


class CrossLingualRAG:
    def __init__(self):
        self._brains: Dict[str, FAISSEngine] = {
            code: FAISSEngine(code) for code in BRAIN_CODES
        }
        self._initialized = False

    async def initialize(self):
        if self._initialized:
            return
        await _get_shared_model()
        for engine in self._brains.values():
            await engine._ensure_index()
        self._initialized = True

    def _brain_for_category(self, category: str) -> str:
        return CATEGORY_TO_BRAIN.get(category, "know")

    def get_all_indexed_objects(self) -> Set[str]:
        result: Set[str] = set()
        for engine in self._brains.values():
            result.update(engine.indexed_objects)
        return result

    def get_indexed_objects(self, code: str) -> Set[str]:
        engine = self._brains.get(code)
        return engine.indexed_objects if engine else set()

    async def add_contributions_to_brain(
        self,
        code: str,
        contributions: List[Dict[str, Any]],
    ) -> int:
        engine = self._brains.get(code)
        if engine is None or not contributions:
            return 0
        documents = []
        for c in contributions:
            meta = {
                "author_uid": c.get("author_uid", "unknown"),
                "language": c.get("language", "es"),
                "quality_score": str(c.get("quality_score", 0)),
                "object_name": c.get("object_name", ""),
                "added_at": datetime.now(timezone.utc).isoformat(),
            }
            documents.append((c["text"], meta))
        ids = await engine.add_batch(documents)
        return len(ids)

    async def add_contribution(
        self,
        text: str,
        author_uid: str,
        language: str,
        quality_score: float,
        object_name: str,
        category: str = "filosofia",
    ) -> int:
        code = self._brain_for_category(category)
        meta = {
            "author_uid": author_uid,
            "language": language,
            "quality_score": str(quality_score),
            "object_name": object_name,
            "added_at": datetime.now(timezone.utc).isoformat(),
        }
        ids = await self._brains[code].add_batch([(text, meta)])
        return ids[0] if ids else 0

    async def add_contributions_batch(
        self,
        contributions: List[Dict[str, Any]],
    ) -> List[int]:
        by_code: Dict[str, List[Dict[str, Any]]] = {c: [] for c in BRAIN_CODES}
        for contrib in contributions:
            category = contrib.get("category", "filosofia")
            code = self._brain_for_category(category)
            by_code[code].append(contrib)

        all_ids: List[int] = []
        for code, contribs in by_code.items():
            if contribs:
                await self.add_contributions_to_brain(code, contribs)
                all_ids.extend(range(len(contribs)))
        return all_ids

    async def query(
        self,
        query_text: str,
        target_language: str,
    ) -> Tuple[str, List[Dict[str, Any]]]:
        # Parallel search across all 4 brains, top-5 each
        raw = await asyncio.gather(
            *[brain.search(query_text, top_k=5) for brain in self._brains.values()],
            return_exceptions=True,
        )

        all_results: List[Dict[str, Any]] = []
        for r in raw:
            if isinstance(r, list):
                all_results.extend(r)

        all_results.sort(key=lambda x: x["score"], reverse=True)

        # Dedup by first-100-char hash, keep top 7
        seen: Set[str] = set()
        merged: List[Dict[str, Any]] = []
        for r in all_results:
            h = hashlib.sha256(r["text"][:100].encode()).hexdigest()[:8]
            if h not in seen:
                seen.add(h)
                r["cross_lingual"] = (
                    r.get("metadata", {}).get("language") != target_language
                )
                merged.append(r)
            if len(merged) == 7:
                break

        if not merged:
            return "", []

        context_parts = []
        total_chars = 0
        max_chars = 3000
        for r in merged:
            text = r["text"]
            score = r["score"]
            meta = r.get("metadata", {})
            author = meta.get("author_uid", "Anónimo")
            quality = meta.get("quality_score", "N/A")
            entry = (
                f"[Sabiduría de {author[:8]} | Calidad: {quality} | "
                f"Relevancia: {score:.2f}]\n{text}\n"
            )
            if total_chars + len(entry) > max_chars:
                remaining = max_chars - total_chars
                if remaining > 50:
                    context_parts.append(entry[:remaining] + "...")
                break
            context_parts.append(entry)
            total_chars += len(entry)

        return "\n".join(context_parts), merged

    async def save_brain_to_greenfield(
        self, code: str, current_version: str
    ) -> Optional[str]:
        """Serializa y sube el cerebro a Irys como nueva versión."""
        from aisynergix.services.irys import upload_brain_index, upload_brain_meta

        engine = self._brains.get(code)
        if engine is None:
            return None

        try:
            n = int(current_version.split("_v")[-1])
        except (ValueError, IndexError):
            n = 0
        new_version = f"{code}_v{n + 1}"

        try:
            index_bytes = await engine.serialize_index()
            meta_dict = {
                "version": new_version,
                "code": code,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "total_docs": engine.total_documents,
                "current_id": engine._current_id,
                "indexed_objects": list(engine.indexed_objects),
                "vector_meta": {str(k): v for k, v in engine._metadata.items()},
            }
            await upload_brain_index(code, new_version, index_bytes)
            await upload_brain_meta(code, new_version, meta_dict)
            return new_version
        except Exception as exc:
            import logging
            logging.getLogger(__name__).error(
                "save_brain_to_irys [%s]: %s", code, exc
            )
            return None

    async def load_brain_from_greenfield(self, code: str, version: str) -> bool:
        """Carga un cerebro desde Irys. Retorna True si tuvo éxito."""
        from aisynergix.services.irys import download_brain_index, download_brain_meta

        engine = self._brains.get(code)
        if engine is None:
            return False
        try:
            index_bytes = await download_brain_index(code, version)
            if not index_bytes:
                return False
            meta_dict = await download_brain_meta(code, version)
            await engine.load_from_bytes(index_bytes, meta_dict)
            return True
        except Exception:
            return False

    async def get_stats(self) -> Dict[str, Any]:
        return {
            "total_documents": sum(b.total_documents for b in self._brains.values()),
            "by_brain": {
                code: b.total_documents for code, b in self._brains.items()
            },
            "model_name": EMBEDDING_MODEL_NAME,
            "embedding_dim": EMBEDDING_DIM,
        }

    async def rebuild_from_bucket(self, only_codes: Optional[List[str]] = None) -> Dict[str, int]:
        """
        Reconstruye los cerebros leyendo aportes del bucket.

        only_codes: si se especifica, solo vectoriza los cerebros de esa lista.
                    Los otros cerebros son ignorados (sus indexed_objects quedan
                    intactos).  Útil para rellenar cerebros vacíos sin rehacer
                    los que ya tienen vectores correctos.

        Retorna dict {code: docs_added}.
        """
        from aisynergix.services.irys import (
            get_all_user_uids,
            list_aportes,
            read_aporte,
        )
        target = set(only_codes) if only_codes else set(BRAIN_CODES)
        user_uids = await get_all_user_uids()
        if not user_uids:
            return {c: 0 for c in target}

        by_code: Dict[str, List[Dict[str, Any]]] = {c: [] for c in target}
        for uid in user_uids:
            try:
                aportes = await list_aportes(uid, limit=50)
                for aporte in aportes:
                    try:
                        texto, tags = await read_aporte(aporte["path"])
                        if not texto.strip():
                            continue
                        category = tags.get("category", "filosofia")
                        code = self._brain_for_category(category)
                        if code not in target:
                            continue
                        by_code[code].append({
                            "text": texto,
                            "author_uid": tags.get("author_uid", uid),
                            "language": tags.get("lang", "es"),
                            "quality_score": float(tags.get("quality_score", 0)),
                            "object_name": aporte["path"],
                        })
                    except Exception:
                        continue
            except Exception:
                continue

        added: Dict[str, int] = {}
        for code, contribs in by_code.items():
            if contribs:
                n = await self.add_contributions_to_brain(code, contribs)
                added[code] = n
            else:
                added[code] = 0
        return added


_rag_instance: Optional[CrossLingualRAG] = None


async def get_rag_engine() -> CrossLingualRAG:
    global _rag_instance
    if _rag_instance is None:
        _rag_instance = CrossLingualRAG()
        await _rag_instance.initialize()
    return _rag_instance


__all__ = [
    "CrossLingualRAG",
    "FAISSEngine",
    "get_rag_engine",
    "BRAIN_CODES",
    "CATEGORY_TO_BRAIN",
    "EMBEDDING_DIM",
    "TOP_K_RESULTS",
]
