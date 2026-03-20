from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from services import SemanticKernelGateway
from utils import get_logger, log_event


RAG_KEYWORDS = {
    "warranty",
    "price",
    "pricing",
    "feature",
    "features",
    "spec",
    "specs",
    "capacity",
    "installation",
    "install",
    "manual",
    "guide",
    "compare",
    "comparison",
    "how to use",
    "maintenance",
}

TROUBLESHOOT_KEYWORDS = {
    "error",
    "code",
    "won't",
    "wont",
    "doesn't",
    "doesnt",
    "not working",
    "broken",
    "issue",
    "problem",
    "leak",
    "smell",
    "noise",
    "vibration",
    "drain",
    "cool",
    "heating",
    "repair",
    "fix",
}


@dataclass
class RouterAgent:
    gateway: SemanticKernelGateway = field(default_factory=SemanticKernelGateway)

    def __post_init__(self) -> None:
        self.logger = get_logger("fixmate.router")

    async def classify(self, user_query: str) -> dict[str, object]:
        log_event(self.logger, 20, "router_classify_started", query_preview=user_query[:120])
        if self.gateway.enabled:
            try:
                result = await self.gateway.complete_json(
                    system_prompt=(
                        "You are the FixMate AI router. "
                        "Choose exactly one route from this enum: ['rag', 'troubleshoot']. "
                        "Use rag for product information, manuals, usage instructions, warranties, maintenance, and comparisons. "
                        "Use troubleshoot for broken behavior, repairs, leaks, noises, temperatures, error codes, or anything not working. "
                        "Return JSON with keys: route, confidence, reasoning. "
                        "confidence must be a number between 0 and 1. reasoning must be one short sentence."
                    ),
                    user_prompt=(
                        "Classify this user request for routing.\n"
                        f"User query: {user_query}"
                    ),
                )
                log_event(self.logger, 20, "router_classify_completed", provider="openai", route=result.get("route"), confidence=result.get("confidence"))
                return result
            except Exception as error:
                log_event(self.logger, 40, "router_llm_failed", error=str(error))
        result = self._fallback_classify(user_query)
        log_event(self.logger, 20, "router_classify_completed", provider="fallback", route=result.get("route"), confidence=result.get("confidence"))
        return result

    def _fallback_classify(self, user_query: str) -> dict[str, object]:
        query = user_query.lower()
        rag_hits = sum(keyword in query for keyword in RAG_KEYWORDS)
        troubleshoot_hits = sum(keyword in query for keyword in TROUBLESHOOT_KEYWORDS)

        if troubleshoot_hits > rag_hits:
            route = "troubleshoot"
            confidence = min(0.6 + 0.1 * troubleshoot_hits, 0.98)
            reasoning = "Troubleshooting indicators were stronger than general information intent."
        else:
            route = "rag"
            confidence = min(0.6 + 0.1 * max(rag_hits, 1), 0.95)
            reasoning = "The query looks like an information or guidance request."

        return {
            "route": route,
            "confidence": round(confidence, 2),
            "reasoning": reasoning,
        }
