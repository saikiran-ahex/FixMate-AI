from __future__ import annotations

import re
from dataclasses import dataclass, field

from services import SemanticKernelGateway
from utils import DataRepository, get_logger, log_event


APPLIANCE_HINTS = {
    "washing_machine": {"washer", "washing machine", "front load", "top load", "spin cycle"},
    "refrigerator": {"fridge", "refrigerator", "freezer", "ice maker"},
    "dishwasher": {"dishwasher", "dish washer"},
    "dryer": {"dryer", "tumble", "lint filter"},
}

CATEGORY_HINTS = {
    "drainage": {"drain", "water remains", "standing water", "pump"},
    "cooling": {"cool", "cold", "warm", "ice", "freezer"},
    "vibration": {"vibration", "vibrates", "shaking", "moving"},
    "leak": {"leak", "water on floor", "dripping"},
    "heating": {"heat", "heating", "dry", "not drying"},
}

LOW_COMPLEXITY_CATEGORIES = {"drainage", "cooling", "heating"}
ERROR_CODE_RE = re.compile(r"\b([A-Z]{1,2}\d{1,3})\b", re.IGNORECASE)


@dataclass
class TriageAgent:
    gateway: SemanticKernelGateway = field(default_factory=SemanticKernelGateway)
    repository: DataRepository = field(default_factory=DataRepository.from_project_root)

    def __post_init__(self) -> None:
        self.logger = get_logger("fixmate.triage")

    async def analyze(self, user_query: str) -> dict[str, object]:
        log_event(self.logger, 20, "triage_started", query_preview=user_query[:120])
        if self.gateway.enabled:
            try:
                result = await self.gateway.complete_json(
                    system_prompt=(
                        "You are the FixMate AI triage agent. "
                        "Return JSON with exactly these keys: appliance_type, issue_category, error_code, severity, complexity, recommended_handler. "
                        "appliance_type must be one of ['washing_machine', 'refrigerator', 'dishwasher', 'dryer', 'unknown']. "
                        "issue_category should be a short snake_case label like drainage, cooling, vibration, leak, heating, or general_fault. "
                        "severity must be one of ['low', 'medium', 'high']. "
                        "complexity must be one of ['low', 'medium', 'high']. "
                        "recommended_handler must be one of ['auto_resolver', 'troubleshooting']. "
                        "Set error_code to null if none is present. Prefer auto_resolver only for safe common fixes."
                    ),
                    user_prompt=(
                        "Triage this appliance support request.\n"
                        f"User query: {user_query}"
                    ),
                )
                log_event(self.logger, 20, "triage_completed", provider="openai", appliance_type=result.get("appliance_type"), issue_category=result.get("issue_category"), recommended_handler=result.get("recommended_handler"))
                return result
            except Exception as error:
                log_event(self.logger, 40, "triage_llm_failed", error=str(error))
        result = self._fallback_analyze(user_query)
        log_event(self.logger, 20, "triage_completed", provider="fallback", appliance_type=result.get("appliance_type"), issue_category=result.get("issue_category"), recommended_handler=result.get("recommended_handler"))
        return result

    def _fallback_analyze(self, user_query: str) -> dict[str, object]:
        query = user_query.lower()
        appliance_type = self._detect_appliance(query)
        category = self._detect_category(query)
        error_code = self._extract_error_code(user_query)
        severity = "high" if any(term in query for term in {"burning", "smoke", "sparks"}) else "medium"
        complexity = "low" if error_code or category in LOW_COMPLEXITY_CATEGORIES else "medium"
        recommended_handler = "auto_resolver" if complexity == "low" else "troubleshooting"

        return {
            "appliance_type": appliance_type,
            "issue_category": category,
            "error_code": error_code,
            "severity": severity,
            "complexity": complexity,
            "recommended_handler": recommended_handler,
        }

    def _detect_appliance(self, query: str) -> str:
        for appliance, hints in APPLIANCE_HINTS.items():
            if any(hint in query for hint in hints):
                return appliance
        return "unknown"

    def _detect_category(self, query: str) -> str:
        for category, hints in CATEGORY_HINTS.items():
            if any(hint in query for hint in hints):
                return category
        return "general_fault"

    def _extract_error_code(self, user_query: str) -> str | None:
        match = ERROR_CODE_RE.search(user_query)
        return match.group(1).upper() if match else None
