from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

from settings import Settings
from utils import get_logger, log_event


@dataclass
class CohereReranker:
    settings: Settings = field(default_factory=Settings)

    def __post_init__(self) -> None:
        self.logger = get_logger("fixmate.reranker")
        self.enabled = bool(self.settings.cohere_api_key)
        self.last_error: str | None = None

    @property
    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "provider": "cohere",
            "model": self.settings.cohere_rerank_model,
            "has_api_key": bool(self.settings.cohere_api_key),
            "last_error": self.last_error,
        }

    async def rerank(
        self,
        query: str,
        matches: list[dict[str, Any]],
        top_n: int | None = None,
    ) -> list[dict[str, Any]]:
        if not self.enabled:
            raise RuntimeError("Cohere reranker is not configured.")

        if not matches:
            return []

        limit = min(top_n or self.settings.rag_rerank_top_k, len(matches))
        documents = [self._build_document_text(match) for match in matches]

        try:
            payload = await asyncio.to_thread(self._request_rerank, query, documents, limit)
            results = payload.get("results", [])
            reranked: list[dict[str, Any]] = []
            selected_indexes: set[int] = set()

            for item in results:
                index = int(item.get("index", -1))
                if index < 0 or index >= len(matches):
                    continue
                match = dict(matches[index])
                match["_rerank_score"] = item.get("relevance_score")
                reranked.append(match)
                selected_indexes.add(index)

            for index, match in enumerate(matches):
                if index not in selected_indexes:
                    reranked.append(match)

            self.last_error = None
            log_event(
                self.logger,
                20,
                "rerank_completed",
                provider="cohere",
                model=self.settings.cohere_rerank_model,
                input_count=len(matches),
                selected_count=len(reranked),
            )
            return reranked
        except Exception as error:
            self.last_error = str(error)
            log_event(self.logger, 40, "rerank_failed", provider="cohere", error=str(error))
            raise

    def _request_rerank(self, query: str, documents: list[str], top_n: int) -> dict[str, Any]:
        body = json.dumps(
            {
                "model": self.settings.cohere_rerank_model,
                "query": query,
                "documents": documents,
                "top_n": top_n,
            }
        ).encode("utf-8")
        request = urllib_request.Request(
            url="https://api.cohere.com/v2/rerank",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.settings.cohere_api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "X-Client-Name": "fixmate-ai",
            },
        )

        try:
            with urllib_request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib_error.HTTPError as error:
            details = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Cohere rerank request failed: HTTP {error.code} {details}") from error
        except urllib_error.URLError as error:
            raise RuntimeError(f"Cohere rerank request failed: {error.reason}") from error

    def _build_document_text(self, match: dict[str, Any]) -> str:
        metadata = match.get("metadata", {})
        return (
            f"Title: {match.get('title') or match.get('source') or 'unknown'}\n"
            f"Source: {match.get('source') or 'unknown'}\n"
            f"Category: {match.get('category') or 'unknown'}\n"
            f"Chunk: {metadata.get('chunk_index', 0) + 1}/{metadata.get('total_chunks', 1)}\n"
            f"Content: {match.get('text') or ''}"
        )
