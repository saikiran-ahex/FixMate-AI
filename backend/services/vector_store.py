from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from settings import Settings
from utils import DataRepository, get_logger, log_event

from .database import DatabaseService

try:
    from openai import AsyncOpenAI
    from qdrant_client import AsyncQdrantClient, models
except ImportError:  # pragma: no cover
    AsyncOpenAI = None
    AsyncQdrantClient = None
    models = None


@dataclass
class QdrantKnowledgeBase:
    repository: DataRepository = field(default_factory=DataRepository.from_project_root)
    settings: Settings = field(default_factory=Settings)
    database: DatabaseService = field(default_factory=DatabaseService)

    def __post_init__(self) -> None:
        self.logger = get_logger("fixmate.qdrant")
        self.enabled = False
        self._initialized = False
        self.documents = self._build_documents()
        self.embedding_client = None
        self.qdrant = None
        log_event(self.logger, 20, "knowledge_base_loaded", chunk_count=len(self.documents))

    @property
    def configured(self) -> bool:
        return bool(self.settings.has_openai and AsyncOpenAI and AsyncQdrantClient and models)

    async def initialize(self, force: bool = False) -> None:
        self.documents = self._build_documents()
        if self._initialized and not force:
            return

        self._initialized = True
        self.enabled = False

        if not self.configured:
            log_event(self.logger, 30, "qdrant_not_configured", has_openai=self.settings.has_openai)
            return

        try:
            self.embedding_client = AsyncOpenAI(api_key=self.settings.openai_api_key)
            self.qdrant = AsyncQdrantClient(
                url=self.settings.qdrant_url,
                api_key=self.settings.qdrant_api_key or None,
            )
            await self._ensure_collection(recreate=force)
            await self._ensure_seeded(force=force)
            self.enabled = True
            log_event(
                self.logger,
                20,
                "qdrant_initialized",
                collection=self.settings.qdrant_collection,
                chunk_count=len(self.documents),
            )
        except Exception as error:
            self.enabled = False
            log_event(self.logger, 40, "qdrant_initialize_failed", error=str(error))

    # Minimum cosine similarity to include a result (0-1 scale)
    _SCORE_THRESHOLD: float = 0.30

    async def search(self, query: str, top_k: int | None = None) -> list[dict[str, Any]]:
        await self.initialize()
        limit = top_k or self.settings.qdrant_top_k
        if self.enabled:
            try:
                query_vector = await self._embed(query)
                response = await self.qdrant.query_points(
                    collection_name=self.settings.qdrant_collection,
                    query=query_vector,
                    limit=limit,
                    with_payload=True,
                    score_threshold=self._SCORE_THRESHOLD,
                )
                points = getattr(response, "points", response)
                payloads = [
                    {**dict(point.payload or {}), "_score": getattr(point, "score", None)}
                    for point in points
                ]
                log_event(
                    self.logger,
                    20,
                    "qdrant_search_completed",
                    mode="vector",
                    top_k=limit,
                    result_count=len(payloads),
                )
                return payloads
            except Exception as error:
                log_event(self.logger, 40, "qdrant_search_failed", error=str(error), mode="vector")

        payloads = self._fallback_search(query, top_k=limit)
        log_event(self.logger, 20, "qdrant_search_completed", mode="fallback", top_k=limit, result_count=len(payloads))
        return payloads

    async def reindex(self) -> dict[str, Any]:
        log_event(self.logger, 20, "qdrant_reindex_started", collection=self.settings.qdrant_collection)
        self._initialized = False
        await self.initialize(force=True)
        result = {
            "configured": self.configured,
            "enabled": self.enabled,
            "collection": self.settings.qdrant_collection,
            "documents_indexed": len(self.documents),
        }
        log_event(self.logger, 20, "qdrant_reindex_completed", **result)
        return result

    def _build_documents(self) -> list[dict[str, Any]]:
        raw_documents: list[dict[str, Any]] = []

        for product in self.repository.load_products():
            raw_documents.append(
                {
                    "id": f"product-{product['model']}",
                    "source": f"product:{product['model']}",
                    "category": "product",
                    "title": f"{product['name']} ({product['model']})",
                    "text": " ".join(
                        [
                            product["model"],
                            product["name"],
                            product["brand"],
                            product["type"].replace("_", " "),
                            " ".join(product["features"]),
                            _flatten_mapping(product["specifications"]),
                            _flatten_mapping(product["warranty"]),
                        ]
                    ),
                    "metadata": product,
                }
            )

        for manual in self.repository.load_manual_index():
            raw_documents.append(
                {
                    "id": manual["path"].replace("/", "-"),
                    "source": manual["path"],
                    "category": "manual",
                    "title": manual["title"],
                    "text": f"{manual['title']} {manual['body']}",
                    "metadata": manual,
                }
            )

        for fix in self.repository.load_quick_fixes():
            raw_documents.append(
                {
                    "id": f"fix-{fix['appliance_type']}-{fix['category']}",
                    "source": f"quick_fix:{fix['appliance_type']}:{fix['category']}",
                    "category": "quick_fix",
                    "title": fix["title"],
                    "text": f"{fix['title']} {fix['why_it_works']} {' '.join(fix['steps'])}",
                    "metadata": fix,
                }
            )

        for appliance_type, codes in self.repository.load_error_codes().items():
            for code, details in codes.items():
                raw_documents.append(
                    {
                        "id": f"error-{appliance_type}-{code}",
                        "source": f"error_code:{appliance_type}:{code}",
                        "category": "error_code",
                        "title": f"{code} {details['title']}",
                        "text": f"{code} {details['title']} {details['summary']} {' '.join(details['steps'])}",
                        "metadata": {"appliance_type": appliance_type, "code": code, **details},
                    }
                )

        raw_documents.extend(self.database.load_uploaded_documents())

        chunked_documents: list[dict[str, Any]] = []
        for document in raw_documents:
            chunks = _chunk_text(
                document["text"],
                chunk_size=self.settings.rag_chunk_size,
                overlap=self.settings.rag_chunk_overlap,
            )
            total_chunks = len(chunks)
            for chunk in chunks:
                chunk_id = f"{document['id']}-chunk-{chunk['chunk_index']}"
                chunked_documents.append(
                    {
                        "id": chunk_id,
                        "source": document["source"],
                        "category": document["category"],
                        "title": document.get("title") or document["source"],
                        "text": chunk["text"],
                        "metadata": {
                            **document.get("metadata", {}),
                            "document_id": document["id"],
                            "chunk_id": chunk_id,
                            "source": document["source"],
                            "category": document["category"],
                            "title": document.get("title") or document["source"],
                            "chunk_index": chunk["chunk_index"],
                            "total_chunks": total_chunks,
                            "chunk_char_count": chunk["char_count"],
                            "chunk_word_count": chunk["word_count"],
                            "start_char": chunk["start_char"],
                            "end_char": chunk["end_char"],
                        },
                    }
                )
        return chunked_documents

    async def _ensure_collection(self, recreate: bool = False) -> None:
        collection_exists = await self.qdrant.collection_exists(self.settings.qdrant_collection)
        if collection_exists and recreate:
            await self.qdrant.delete_collection(self.settings.qdrant_collection)
            collection_exists = False
            log_event(self.logger, 20, "qdrant_collection_deleted", collection=self.settings.qdrant_collection)

        if collection_exists:
            return

        await self.qdrant.create_collection(
            collection_name=self.settings.qdrant_collection,
            vectors_config=models.VectorParams(
                size=self.settings.embedding_dimensions,
                distance=models.Distance.COSINE,
            ),
        )
        log_event(
            self.logger,
            20,
            "qdrant_collection_created",
            collection=self.settings.qdrant_collection,
            dimensions=self.settings.embedding_dimensions,
        )

    async def _ensure_seeded(self, force: bool = False) -> None:
        count_response = await self.qdrant.count(
            collection_name=self.settings.qdrant_collection,
            exact=True,
        )
        existing_count = getattr(count_response, "count", 0)
        if existing_count > 0 and not force:
            log_event(self.logger, 20, "qdrant_seed_skipped", existing_count=existing_count)
            return

        if existing_count > 0 and force:
            await self.qdrant.delete(
                collection_name=self.settings.qdrant_collection,
                points_selector=models.FilterSelector(filter=models.Filter()),
            )

        # Batch embed all documents in one API call instead of N serial calls
        _EMBED_BATCH = 256
        texts = [doc["text"] for doc in self.documents]
        vectors: list[list[float]] = []
        for batch_start in range(0, len(texts), _EMBED_BATCH):
            batch_texts = texts[batch_start : batch_start + _EMBED_BATCH]
            response = await self.embedding_client.embeddings.create(
                model=self.settings.openai_embedding_model,
                input=batch_texts,
            )
            # API returns items sorted by index
            vectors.extend(item.embedding for item in sorted(response.data, key=lambda x: x.index))

        points = [
            models.PointStruct(id=str(uuid.uuid4()), vector=vectors[i], payload=doc)
            for i, doc in enumerate(self.documents)
        ]

        if points:
            # Upsert in batches to avoid request-size limits
            _UPSERT_BATCH = 128
            for batch_start in range(0, len(points), _UPSERT_BATCH):
                await self.qdrant.upsert(
                    collection_name=self.settings.qdrant_collection,
                    points=points[batch_start : batch_start + _UPSERT_BATCH],
                    wait=True,
                )
            log_event(self.logger, 20, "qdrant_seed_completed", points=len(points))

    async def _embed(self, text: str) -> list[float]:
        """Embed a single string (used for query-time embedding)."""
        response = await self.embedding_client.embeddings.create(
            model=self.settings.openai_embedding_model,
            input=text,
        )
        return response.data[0].embedding

    def _fallback_search(self, query: str, top_k: int = 4) -> list[dict[str, Any]]:
        tokens = _tokenize(query)
        scored: list[tuple[int, dict[str, Any]]] = []
        for document in self.documents:
            overlap = len(tokens & _tokenize(document["text"]))
            if overlap:
                payload = dict(document)
                payload["_score"] = overlap
                scored.append((overlap, payload))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [document for _, document in scored[:top_k]]



def _flatten_mapping(data: dict[str, Any]) -> str:
    return " ".join(f"{key} {value}" for key, value in data.items())



def _chunk_text(text: str, chunk_size: int, overlap: int) -> list[dict[str, Any]]:
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return []

    if len(normalized) <= chunk_size:
        return [
            {
                "text": normalized,
                "chunk_index": 0,
                "char_count": len(normalized),
                "word_count": len(normalized.split()),
                "start_char": 0,
                "end_char": len(normalized),
            }
        ]

    chunks: list[dict[str, Any]] = []
    start = 0
    text_length = len(normalized)
    chunk_index = 0

    while start < text_length:
        chunk_start = start
        end = min(text_length, start + chunk_size)
        if end < text_length:
            boundary = normalized.rfind(" ", start, end)
            if boundary > start + int(chunk_size * 0.6):
                end = boundary
        chunk = normalized[start:end].strip()
        if chunk:
            chunks.append(
                {
                    "text": chunk,
                    "chunk_index": chunk_index,
                    "char_count": len(chunk),
                    "word_count": len(chunk.split()),
                    "start_char": chunk_start,
                    "end_char": end,
                }
            )
            chunk_index += 1
        if end >= text_length:
            break
        # Step forward by (chunk_size - overlap) so consecutive chunks share `overlap` chars
        step = max(chunk_size - overlap, 1)
        start = chunk_start + step

    return chunks



def _tokenize(text: str) -> set[str]:
    cleaned = "".join(char.lower() if char.isalnum() else " " for char in text)
    return {token for token in cleaned.split() if len(token) > 2}
