import os
import json
import hashlib
import asyncio
import pickle
from typing import List, Dict, Optional, Tuple, Any
from pathlib import Path
from datetime import datetime, timezone

import numpy as np
import faiss
from sentence_transformers import SentenceTransformer

EMBEDDING_MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
EMBEDDING_DIM = 384
FAISS_INDEX_TYPE = "IVFPQ"
N_CENTROIDS = 64
M_SUBQUANTIZERS = 48
N_BITS = 8
TOP_K_RESULTS = 10
TEMP_INDEX_DIR = Path("/tmp/synergix_faiss")
TEMP_INDEX_DIR.mkdir(parents=True, exist_ok=True)

_QUERY_PREFIX = "query: "
_PASSAGE_PREFIX = "passage: "


class FAISSEngine:
    def __init__(self):
        self._model: Optional[SentenceTransformer] = None
        self._index: Optional[faiss.Index] = None
        self._metadata: Dict[int, Dict[str, Any]] = {}
        self._current_id: int = 0
        self._lock = asyncio.Lock()
        self._is_trained = False

    async def _ensure_model(self) -> SentenceTransformer:
        if self._model is None:
            self._model = await asyncio.to_thread(
                lambda: SentenceTransformer(
                    EMBEDDING_MODEL_NAME,
                    device="cpu",
                    cache_folder="/tmp/st_cache",
                )
            )
        return self._model

    async def _ensure_index(self):
        if self._index is None:
            quantizer = faiss.IndexFlatL2(EMBEDDING_DIM)
            self._index = faiss.IndexIVFPQ(
                quantizer,
                EMBEDDING_DIM,
                N_CENTROIDS,
                M_SUBQUANTIZERS,
                N_BITS,
            )

            index_path = TEMP_INDEX_DIR / "synergix_brain.index"
            meta_path = TEMP_INDEX_DIR / "synergix_metadata.pkl"

            if index_path.exists() and meta_path.exists():
                try:
                    self._index = await asyncio.to_thread(
                        lambda: faiss.read_index(str(index_path))
                    )
                    with open(meta_path, "rb") as f:
                        saved_data = pickle.load(f)
                        self._metadata = saved_data.get("metadata", {})
                        self._current_id = saved_data.get("current_id", 0)

                    if isinstance(self._index, faiss.IndexIVFPQ):
                        self._is_trained = self._index.is_trained

                    if not self._is_trained:
                        await self._train_with_dummy()
                except Exception:
                    await self._train_with_dummy()
            else:
                await self._train_with_dummy()

    async def _train_with_dummy(self):
        # FAISS IVFPQ requires ≥39×n_centroids training points; PQ sub-quantizers
        # use 256 codewords so need ≥9984 points.  Use 10 000 to stay above that
        # threshold and silence the "please provide at least N training points" warnings.
        n_train = max(10_000, N_CENTROIDS * 160)
        dummy_vectors = np.random.randn(n_train, EMBEDDING_DIM).astype(np.float32)
        faiss.normalize_L2(dummy_vectors)
        await asyncio.to_thread(self._index.train, dummy_vectors)
        self._is_trained = True

    async def _save_index(self):
        index_path = TEMP_INDEX_DIR / "synergix_brain.index"
        meta_path = TEMP_INDEX_DIR / "synergix_metadata.pkl"

        await asyncio.to_thread(
            faiss.write_index,
            self._index,
            str(index_path),
        )

        with open(meta_path, "wb") as f:
            pickle.dump(
                {
                    "metadata": self._metadata,
                    "current_id": self._current_id,
                },
                f,
            )

    async def compute_embedding(self, text: str, is_query: bool = False) -> np.ndarray:
        model = await self._ensure_model()

        prefix = _QUERY_PREFIX if is_query else _PASSAGE_PREFIX
        input_text = f"{prefix}{text}"

        embedding = await asyncio.to_thread(
            lambda: model.encode(
                [input_text],
                normalize_embeddings=True,
                show_progress_bar=False,
            )
        )

        return embedding[0].astype(np.float32)

    async def add_document(self, text: str, metadata: Dict[str, Any]) -> int:
        async with self._lock:
            await self._ensure_index()

            embedding = await self.compute_embedding(text, is_query=False)
            vector = embedding.reshape(1, -1)

            self._index.add_with_ids(
                vector,
                np.array([self._current_id], dtype=np.int64),
            )

            self._metadata[self._current_id] = {
                **metadata,
                "text": text,
                "text_hash": hashlib.sha256(text.encode()).hexdigest()[:16],
            }

            doc_id = self._current_id
            self._current_id += 1

            if self._current_id % 10 == 0:
                await self._save_index()

            return doc_id

    async def add_batch(
        self,
        documents: List[Tuple[str, Dict[str, Any]]],
    ) -> List[int]:
        if not documents:
            return []

        async with self._lock:
            await self._ensure_index()

            texts = [doc[0] for doc in documents]
            metadatas = [doc[1] for doc in documents]

            embeddings = await self._embed_batch(texts)

            start_id = self._current_id
            ids = np.arange(start_id, start_id + len(documents), dtype=np.int64)

            self._index.add_with_ids(embeddings, ids)

            for i, metadata in enumerate(metadatas):
                doc_id = start_id + i
                self._metadata[doc_id] = {
                    **metadata,
                    "text": texts[i],
                    "text_hash": hashlib.sha256(texts[i].encode()).hexdigest()[:16],
                }

            self._current_id = start_id + len(documents)
            await self._save_index()

            return list(range(start_id, start_id + len(documents)))

    async def _embed_batch(self, texts: List[str]) -> np.ndarray:
        model = await self._ensure_model()

        input_texts = [f"{_PASSAGE_PREFIX}{t}" for t in texts]

        embeddings = await asyncio.to_thread(
            lambda: model.encode(
                input_texts,
                normalize_embeddings=True,
                show_progress_bar=False,
                batch_size=32,
            )
        )

        return embeddings.astype(np.float32)

    async def search(
        self,
        query: str,
        top_k: int = TOP_K_RESULTS,
    ) -> List[Dict[str, Any]]:
        async with self._lock:
            await self._ensure_index()

            if self._index.ntotal == 0:
                return []

            query_embedding = await self.compute_embedding(query, is_query=True)
            vector = query_embedding.reshape(1, -1)

            effective_k = min(top_k, self._index.ntotal)

            distances, indices = await asyncio.to_thread(
                self._index.search,
                vector,
                effective_k,
            )

            results = []
            for distance, idx in zip(distances[0], indices[0]):
                if idx < 0:
                    continue

                metadata = self._metadata.get(int(idx), {})
                similarity = float(1.0 / (1.0 + distance))

                results.append({
                    "id": int(idx),
                    "score": similarity,
                    "text": metadata.get("text", ""),
                    "metadata": {
                        k: v
                        for k, v in metadata.items()
                        if k != "text"
                    },
                })

            return results

    async def get_context_for_llm(
        self,
        query: str,
        max_chars: int = 3000,
    ) -> str:
        results = await self.search(query, top_k=TOP_K_RESULTS)

        if not results:
            return ""

        filtered = [r for r in results if r["score"] > 0.3]

        if not filtered:
            filtered = results[:3]

        context_parts = []
        total_chars = 0

        for r in filtered:
            text = r["text"]
            score = r["score"]
            metadata = r.get("metadata", {})

            author = metadata.get("author_uid", "Anónimo")
            quality = metadata.get("quality_score", "N/A")

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

        return "\n".join(context_parts)

    @property
    def total_documents(self) -> int:
        if self._index is None:
            return 0
        return self._index.ntotal


class CrossLingualRAG:
    def __init__(self):
        self._faiss = FAISSEngine()
        self._initialized = False

    async def initialize(self):
        if self._initialized:
            return

        await self._faiss._ensure_model()
        await self._faiss._ensure_index()
        self._initialized = True

    async def add_contribution(
        self,
        text: str,
        author_uid: str,
        language: str,
        quality_score: float,
        object_name: str,
    ) -> int:
        metadata = {
            "author_uid": author_uid,
            "language": language,
            "quality_score": str(quality_score),
            "object_name": object_name,
            "added_at": datetime.now(timezone.utc).isoformat(),
        }

        return await self._faiss.add_document(text, metadata)

    async def add_contributions_batch(
        self,
        contributions: List[Dict[str, Any]],
    ) -> List[int]:
        documents = []

        for contrib in contributions:
            metadata = {
                "author_uid": contrib.get("author_uid", "unknown"),
                "language": contrib.get("language", "es"),
                "quality_score": str(contrib.get("quality_score", 0)),
                "object_name": contrib.get("object_name", ""),
                "added_at": datetime.now(timezone.utc).isoformat(),
            }
            documents.append((contrib["text"], metadata))

        return await self._faiss.add_batch(documents)

    async def query(
        self,
        query_text: str,
        target_language: str,
    ) -> Tuple[str, List[Dict[str, Any]]]:
        context = await self._faiss.get_context_for_llm(query_text)

        search_results = await self._faiss.search(query_text, top_k=TOP_K_RESULTS)

        if context:
            enriched_results = []
            for result in search_results:
                enriched_result = dict(result)
                enriched_result["cross_lingual"] = (
                    result.get("metadata", {}).get("language") != target_language
                )
                enriched_results.append(enriched_result)

            return context, enriched_results

        return "", []

    async def get_stats(self) -> Dict[str, Any]:
        return {
            "total_documents": self._faiss.total_documents,
            "model_name": EMBEDDING_MODEL_NAME,
            "embedding_dim": EMBEDDING_DIM,
            "index_type": FAISS_INDEX_TYPE,
        }

    async def rebuild_from_bucket(self):
        from aisynergix.services.greenfield import (
            get_all_user_uids,
            list_aportes,
            read_aporte,
        )

        user_uids = await get_all_user_uids()
        if not user_uids:
            return

        contributions = []
        for uid in user_uids:
            try:
                aportes = await list_aportes(uid, limit=50)
                for aporte in aportes:
                    try:
                        texto, tags = await read_aporte(aporte["path"])
                        if not texto.strip():
                            continue
                        contributions.append({
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

        if contributions:
            await self.add_contributions_batch(contributions)


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
    "EMBEDDING_DIM",
    "TOP_K_RESULTS",
]
