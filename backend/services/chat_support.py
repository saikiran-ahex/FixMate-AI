from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from services import DatabaseService, QdrantKnowledgeBase, SemanticKernelGateway
from utils import get_logger, log_event


@dataclass
class RAGAgent:
    gateway: SemanticKernelGateway = field(default_factory=SemanticKernelGateway)
    knowledge_base: QdrantKnowledgeBase = field(default_factory=QdrantKnowledgeBase)

    def __post_init__(self) -> None:
        self.logger = get_logger("fixmate.rag")

    async def answer_query(self, user_query: str) -> dict[str, Any]:
        log_event(self.logger, 20, "rag_started", query_preview=user_query[:120])
        matches = await self.knowledge_base.search(user_query)
        if not matches:
            log_event(self.logger, 30, "rag_no_matches")
            return {
                "agent": "rag",
                "response": (
                    "I could not find a confident match in the current appliance knowledge base. "
                    "Please share the appliance type or model so I can narrow it down."
                ),
                "sources": [],
            }

        sources = [match["source"] for match in matches]
        log_event(self.logger, 20, "rag_context_loaded", sources=sources, match_count=len(matches))
        context = "\n\n".join(
            f"Source: {match['source']}\nContent: {match['text']}"
            for match in matches
        )

        if self.gateway.enabled:
            try:
                response = await self.gateway.complete_text(
                    system_prompt=(
                        "You are FixMate AI. Answer only from the supplied appliance support and uploaded document context. "
                        "If context is incomplete, say what information is missing. "
                        "Prefer short direct answers, then practical details."
                    ),
                    user_prompt=f"Question:\n{user_query}\n\nContext:\n{context}",
                )
                log_event(self.logger, 20, "rag_completed", provider="openai", sources=sources)
                return {
                    "agent": "rag",
                    "response": response,
                    "sources": sources,
                }
            except Exception as error:
                log_event(self.logger, 40, "rag_llm_failed", error=str(error), sources=sources)

        best = matches[0].get("metadata", {})
        if "model" in best:
            response = (
                f"{best['name']} ({best['model']}) is a {best['brand']} "
                f"{best['type'].replace('_', ' ')} with features like "
                f"{', '.join(best['features'][:3])}. "
                f"It offers {best['warranty']['parts_labor']} parts and labor coverage."
            )
        else:
            response = matches[0]["text"]

        log_event(self.logger, 20, "rag_completed", provider="fallback", sources=sources)
        return {
            "agent": "rag",
            "response": response,
            "sources": sources,
        }


@dataclass
class ChatSummaryService:
    gateway: SemanticKernelGateway = field(default_factory=SemanticKernelGateway)
    database: DatabaseService = field(default_factory=DatabaseService)

    def __post_init__(self) -> None:
        self.logger = get_logger("fixmate.summary")

    async def summarize_if_needed(self, thread_id: str) -> dict[str, Any] | None:
        messages = self.database.list_unsummarized_messages(thread_id)
        batch_size = self.database.settings.summary_batch_size
        if len(messages) < batch_size:
            return None

        batch = messages[:batch_size]
        message_ids = [message["id"] for message in batch]
        transcript = "\n".join(f"{message['role']}: {message['content']}" for message in batch)
        log_event(self.logger, 20, "chat_summary_started", thread_id=thread_id, message_count=len(batch))

        summary_text = None
        if self.gateway.enabled:
            try:
                summary_text = await self.gateway.complete_text(
                    system_prompt=(
                        "You summarize support chats. Capture the user problem, important facts, attempted fixes, and current state. "
                        "Keep it concise and useful for loading future context."
                    ),
                    user_prompt=f"Summarize this chat excerpt:\n\n{transcript}",
                    temperature=0.2,
                )
            except Exception as error:
                log_event(self.logger, 40, "chat_summary_llm_failed", thread_id=thread_id, error=str(error))

        if summary_text is None:
            summary_text = _fallback_summary(batch)

        summary = self.database.create_summary(thread_id, summary_text, message_ids)
        log_event(self.logger, 20, "chat_summary_completed", thread_id=thread_id, covered_message_count=summary["covered_message_count"])
        return summary


def _fallback_summary(messages: list[dict[str, Any]]) -> str:
    snippets = []
    for message in messages[:6]:
        role = message["role"].capitalize()
        snippets.append(f"{role}: {message['content'][:140]}")
    return " | ".join(snippets)
