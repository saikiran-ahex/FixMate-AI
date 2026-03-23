from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from services import SemanticKernelGateway
from utils import get_logger, log_event


@dataclass
class AutoResolverAgent:
    gateway: SemanticKernelGateway = field(default_factory=SemanticKernelGateway)
    repository: Any = None

    def __post_init__(self) -> None:
        self.logger = get_logger("fixmate.auto_resolver")
        self.error_codes = self.repository.load_error_codes()
        self.quick_fixes = self.repository.load_quick_fixes()

    async def resolve(self, user_query: str, triage: dict[str, object]) -> dict[str, object]:
        appliance = str(triage.get("appliance_type", "unknown"))
        error_code = triage.get("error_code")
        category = str(triage.get("issue_category", "general_fault"))
        log_event(self.logger, 20, "auto_resolver_started", appliance_type=appliance, issue_category=category, error_code=error_code)

        fix_context = None
        source = None
        if error_code and appliance in self.error_codes:
            issue = self.error_codes[appliance].get(str(error_code))
            if issue:
                source = "error_code"
                fix_context = {
                    "title": issue["title"],
                    "summary": issue["summary"],
                    "estimated_fix_time": issue["estimated_fix_time"],
                    "steps": issue["steps"],
                }

        if fix_context is None:
            for fix in self.quick_fixes:
                if appliance == fix["appliance_type"] and category == fix["category"]:
                    source = "quick_fix"
                    fix_context = {
                        "title": fix["title"],
                        "summary": fix["why_it_works"],
                        "estimated_fix_time": "10-20 minutes",
                        "steps": fix["steps"],
                    }
                    break

        if fix_context is None:
            log_event(self.logger, 30, "auto_resolver_no_fix_found", appliance_type=appliance, issue_category=category)
            return {
                "agent": "auto_resolver",
                "resolved": False,
                "response": "I do not have a safe quick fix for this issue yet, so I am escalating to troubleshooting.",
            }

        if self.gateway.enabled:
            try:
                steps = "\n".join(f"- {step}" for step in fix_context["steps"])
                response = await self.gateway.complete_text(
                    system_prompt=(
                        "You are the FixMate AI auto-resolver. Produce a safe step-by-step appliance fix. "
                        "Do not invent steps outside the provided fix context. Mention when to stop and call service."
                    ),
                    user_prompt=(
                        f"User issue:\n{user_query}\n\n"
                        f"Triage:\n{triage}\n\n"
                        f"Fix context:\nTitle: {fix_context['title']}\n"
                        f"Summary: {fix_context['summary']}\n"
                        f"Estimated time: {fix_context['estimated_fix_time']}\n"
                        f"Steps:\n{steps}"
                    ),
                )
                log_event(self.logger, 20, "auto_resolver_completed", provider="openai", source=source, resolved=True)
                return {
                    "agent": "auto_resolver",
                    "resolved": True,
                    "response": response,
                }
            except Exception as error:
                log_event(self.logger, 40, "auto_resolver_llm_failed", error=str(error), source=source)

        steps = "\n".join(f"{index}. {step}" for index, step in enumerate(fix_context["steps"], start=1))
        log_event(self.logger, 20, "auto_resolver_completed", provider="fallback", source=source, resolved=True)
        return {
            "agent": "auto_resolver",
            "resolved": True,
            "response": (
                f"{fix_context['title']}: {fix_context['summary']}\n"
                f"Estimated time: {fix_context['estimated_fix_time']}\n"
                f"Steps:\n{steps}\n"
                f"If the issue remains after these steps, stop and escalate to service."
            ),
        }
