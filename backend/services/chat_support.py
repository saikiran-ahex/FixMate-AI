from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from services import DatabaseService, SemanticKernelGateway
from utils import get_logger, log_event


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
