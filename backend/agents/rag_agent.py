from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from services import QdrantKnowledgeBase, SemanticKernelGateway
from settings import Settings
from utils import get_logger, log_event


@dataclass
class RAGAgent:
    gateway: SemanticKernelGateway = field(default_factory=SemanticKernelGateway)
    knowledge_base: QdrantKnowledgeBase = field(default_factory=QdrantKnowledgeBase)
    settings: Settings = field(default_factory=Settings)

    def __post_init__(self) -> None:
        self.logger = get_logger("fixmate.rag")

    async def answer_query(self, user_query: str) -> dict[str, Any]:
        log_event(self.logger, 20, "rag_started", query_preview=user_query[:120])
        matches = await self.knowledge_base.search(user_query, top_k=self.settings.rag_rerank_top_k)
        if not matches:
            log_event(self.logger, 30, "rag_no_matches")
            return {
                "agent": "rag",
                "response": (
                    "I could not find a confident match in the current knowledge base. "
                    "Please share the device name, model, or the exact issue so I can narrow it down."
                ),
                "sources": [],
            }

        reranked_matches = await self._rerank_matches(user_query, matches)
        selected_matches = self._select_context_matches(reranked_matches)
        sources = [self._build_source(match) for match in selected_matches]
        log_event(
            self.logger,
            20,
            "rag_context_loaded",
            sources=sources,
            match_count=len(selected_matches),
            candidate_count=len(matches),
            gateway_enabled=self.gateway.enabled,
        )

        context = "\n\n".join(self._format_context_block(match) for match in selected_matches)

        if self.gateway.enabled:
            try:
                response = await self.gateway.complete_text(
                    system_prompt=(
                        "You are FixMate AI. Answer the user's question using only the retrieved support context. "
                        "Do not quote or dump raw manual text unless a tiny phrase is essential. "
                        "Rewrite the information into clean, natural support guidance. "
                        "If the context is incomplete or ambiguous, say exactly what is missing. "
                        "If there are steps, present them in a short numbered list. "
                        "Start with the direct answer in one sentence, then give only the most useful steps. "
                        "Ignore page headers, copyright notices, tables of contents, and repeated boilerplate. "
                        "Mention the source titles briefly when useful, but keep the answer natural."
                    ),
                    user_prompt=(
                        f"Question:\n{user_query}\n\n"
                        f"Retrieved support context:\n{context}\n\n"
                        "Write a direct support answer for the user. Do not copy long spans from the context."
                    ),
                    temperature=0.0,
                )
                log_event(self.logger, 20, "rag_completed", provider="openai", sources=sources)
                return {
                    "agent": "rag",
                    "response": response,
                    "sources": sources,
                }
            except Exception as error:
                log_event(self.logger, 40, "rag_llm_failed", error=str(error), sources=sources)

        response = self._fallback_answer(selected_matches)
        log_event(self.logger, 30, "rag_completed_without_llm", sources=sources)
        return {
            "agent": "rag",
            "response": response,
            "sources": sources,
        }

    async def _rerank_matches(self, user_query: str, matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
        heuristic_ranked = sorted(matches, key=lambda match: self._heuristic_score(user_query, match), reverse=True)

        if not self.gateway.enabled:
            log_event(self.logger, 20, "rag_rerank_completed", provider="heuristic", selected=len(heuristic_ranked))
            return heuristic_ranked

        candidate_payload = []
        preview_limit = self.settings.rag_chunk_preview_chars
        for match in heuristic_ranked[: self.settings.rag_rerank_top_k]:
            metadata = match.get("metadata", {})
            candidate_payload.append(
                {
                    "chunk_id": metadata.get("chunk_id") or match.get("id"),
                    "title": match.get("title") or match.get("source"),
                    "source": match.get("source"),
                    "category": match.get("category"),
                    "chunk_index": metadata.get("chunk_index", 0),
                    "total_chunks": metadata.get("total_chunks", 1),
                    "score": match.get("_score"),
                    "preview": (match.get("text") or "")[:preview_limit],
                }
            )

        try:
            rerank_result = await self.gateway.complete_json(
                system_prompt=(
                    "You are ranking retrieval chunks for a RAG system. "
                    "Choose the chunks that are most useful for answering the user's question. "
                    "Prioritize chunks with exact device match, direct instructions, troubleshooting steps, and clear relevance. "
                    "Return JSON with key selected_chunk_ids as an ordered array of the best chunk ids."
                ),
                user_prompt=(
                    f"Question:\n{user_query}\n\n"
                    f"Candidate chunks:\n{json.dumps(candidate_payload, ensure_ascii=False)}"
                ),
            )
            selected_ids = rerank_result.get("selected_chunk_ids", [])
            selected_map = {
                (match.get("metadata", {}).get("chunk_id") or match.get("id")): match
                for match in heuristic_ranked
            }
            reranked = [selected_map[chunk_id] for chunk_id in selected_ids if chunk_id in selected_map]
            for match in heuristic_ranked:
                if match not in reranked:
                    reranked.append(match)
            log_event(self.logger, 20, "rag_rerank_completed", provider="openai", selected=len(reranked))
            return reranked
        except Exception as error:
            log_event(self.logger, 40, "rag_rerank_failed", error=str(error))
            log_event(self.logger, 20, "rag_rerank_completed", provider="heuristic", selected=len(heuristic_ranked))
            return heuristic_ranked

    def _select_context_matches(self, matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
        selected: list[dict[str, Any]] = []
        seen_chunk_ids: set[str] = set()

        for match in matches:
            for candidate in self._expand_match_window(match, matches):
                chunk_id = candidate.get("metadata", {}).get("chunk_id") or candidate.get("id")
                if chunk_id in seen_chunk_ids:
                    continue
                selected.append(candidate)
                seen_chunk_ids.add(chunk_id)
                if len(selected) >= self.settings.rag_context_chunks:
                    return selected

        return selected[: self.settings.rag_context_chunks]

    def _heuristic_score(self, user_query: str, match: dict[str, Any]) -> float:
        query_tokens = _tokenize(user_query)
        text_tokens = _tokenize(match.get("text", ""))
        metadata = match.get("metadata", {})
        title_tokens = _tokenize(match.get("title", ""))
        source_tokens = _tokenize(match.get("source", ""))
        overlap = len(query_tokens & text_tokens)
        title_overlap = len(query_tokens & title_tokens)
        source_overlap = len(query_tokens & source_tokens)
        vector_score = float(match.get("_score") or 0.0)
        chunk_index = metadata.get("chunk_index", 0)
        return (vector_score * 10.0) + (overlap * 3.0) + (title_overlap * 5.0) + (source_overlap * 2.0) - (chunk_index * 0.05)

    def _build_source(self, match: dict[str, Any]) -> dict[str, Any]:
        metadata = match.get("metadata", {})
        return {
            "source": match.get("source"),
            "title": match.get("title"),
            "category": match.get("category"),
            "chunk_index": metadata.get("chunk_index"),
            "total_chunks": metadata.get("total_chunks"),
            "score": match.get("_score"),
        }

    def _format_context_block(self, match: dict[str, Any]) -> str:
        metadata = match.get("metadata", {})
        cleaned_text = _clean_context_text(match.get("text", ""))
        return (
            f"Title: {match.get('title', match.get('source', 'unknown'))}\n"
            f"Source: {match.get('source', 'unknown')}\n"
            f"Category: {match.get('category', 'unknown')}\n"
            f"Chunk: {metadata.get('chunk_index', 0) + 1}/{metadata.get('total_chunks', 1)}\n"
            f"Score: {match.get('_score')}\n"
            f"Content: {cleaned_text}"
        )

    def _fallback_answer(self, matches: list[dict[str, Any]]) -> str:
        top = matches[0]
        title = top.get("title") or top.get("source") or "the retrieved document"
        runtime_error = self.gateway.last_completion_error
        if runtime_error:
            return (
                f"I found relevant content in {title}, but the answer generation step failed before I could write a final response. "
                f"Backend error: {runtime_error}"
            )
        return (
            f"I found relevant content in {title}, but the LLM answer step is not active right now, "
            "so I am not going to return raw document chunks. Please verify OPENAI_API_KEY is loaded in the backend container and try again."
        )

    def _expand_match_window(self, match: dict[str, Any], matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
        metadata = match.get("metadata", {})
        document_id = metadata.get("document_id")
        chunk_index = metadata.get("chunk_index")
        if document_id is None or chunk_index is None:
            return [match]

        by_position = {
            (
                candidate.get("metadata", {}).get("document_id"),
                candidate.get("metadata", {}).get("chunk_index"),
            ): candidate
            for candidate in matches
        }

        window: list[dict[str, Any]] = []
        for index in (chunk_index - 1, chunk_index, chunk_index + 1):
            candidate = by_position.get((document_id, index))
            if candidate:
                window.append(candidate)
        return window or [match]


def _tokenize(text: str) -> set[str]:
    cleaned = "".join(char.lower() if char.isalnum() else " " for char in text)
    return {token for token in cleaned.split() if len(token) > 2}


def _clean_context_text(text: str) -> str:
    cleaned = text or ""
    cleaned = re.sub(r"_+", " ", cleaned)
    cleaned = re.sub(r"\bPg\s+\d+\b", " ", cleaned, flags=re.IGNORECASE)
    # Fix: was r"\b\d+\s*[]\s*\d{4}..." - empty [] is an invalid character class
    cleaned = re.sub(r"\b\d+\s*\S+\s*\d{4}.*?reserved\.", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bAll rights reserved\.\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bConnection digram\b", "Connection diagram", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned
