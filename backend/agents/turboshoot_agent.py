from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from services import SemanticKernelGateway
from utils import DataRepository, get_logger, log_event


@dataclass
class TurboShootAgent:
    gateway: SemanticKernelGateway = field(default_factory=SemanticKernelGateway)
    repository: DataRepository = field(default_factory=DataRepository.from_project_root)
    sessions: dict[str, dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.logger = get_logger("fixmate.turboshoot")
        self.playbooks = self.repository.load_playbooks()

    async def start(self, user_query: str, triage: dict[str, object]) -> dict[str, Any]:
        appliance = str(triage.get("appliance_type", "unknown"))
        category = str(triage.get("issue_category", "general_fault"))
        playbook = self.playbooks.get(appliance, {}).get(category)
        log_event(self.logger, 20, "turboshoot_started", appliance_type=appliance, issue_category=category)

        if not playbook:
            message = (
                "I need a support specialist for this case because the current diagnostic playbooks "
                "do not cover it yet."
            )
            log_event(self.logger, 30, "turboshoot_playbook_missing", appliance_type=appliance, issue_category=category)
            return {"agent": "turboshoot", "resolved": False, "message": message, "conversation_id": None}

        intro = playbook["intro"]
        if self.gateway.enabled:
            try:
                intro = await self.gateway.complete_text(
                    system_prompt=(
                        "You are the FixMate AI TurboShoot agent. Introduce a guided diagnostic flow briefly and clearly."
                    ),
                    user_prompt=f"User issue: {user_query}\nTriage: {triage}\nPlaybook intro: {playbook['intro']}",
                )
            except Exception as error:
                log_event(self.logger, 40, "turboshoot_intro_llm_failed", error=str(error))

        conversation_id = str(uuid4())
        self.sessions[conversation_id] = {
            "playbook": playbook,
            "answers": {},
            "current_index": 0,
        }
        first_question = playbook["questions"][0]
        log_event(self.logger, 20, "turboshoot_session_created", conversation_id=conversation_id, input_key=first_question["id"])
        return {
            "agent": "turboshoot",
            "resolved": False,
            "conversation_id": conversation_id,
            "message": intro,
            "input_key": first_question["id"],
            "questions": [first_question["question"]],
        }

    async def continue_session(self, conversation_id: str, answers: dict[str, str]) -> dict[str, Any]:
        log_event(self.logger, 20, "turboshoot_continue_started", conversation_id=conversation_id, answers=answers)
        session = self.sessions[conversation_id]
        session["answers"].update(answers)

        playbook = session["playbook"]
        question = playbook["questions"][session["current_index"]]
        chosen = answers.get(question["id"], "").lower()

        if chosen in question["diagnosis_map"]:
            outcome = question["diagnosis_map"][chosen]
            self.sessions.pop(conversation_id, None)
            log_event(self.logger, 20, "turboshoot_session_completed", conversation_id=conversation_id, resolved=outcome["resolved"])
            return {
                "agent": "turboshoot",
                "resolved": outcome["resolved"],
                "message": outcome["message"],
                "input_key": None,
            }

        session["current_index"] += 1
        if session["current_index"] >= len(playbook["questions"]):
            self.sessions.pop(conversation_id, None)
            log_event(self.logger, 30, "turboshoot_session_exhausted", conversation_id=conversation_id)
            return {
                "agent": "turboshoot",
                "resolved": False,
                "message": "We have reached the end of the guided checks. This case should be escalated to a technician.",
                "input_key": None,
            }

        next_question = playbook["questions"][session["current_index"]]
        log_event(self.logger, 20, "turboshoot_next_question", conversation_id=conversation_id, input_key=next_question["id"])
        return {
            "agent": "turboshoot",
            "resolved": False,
            "message": "Thanks. I need one more detail to narrow down the root cause.",
            "input_key": next_question["id"],
            "questions": [next_question["question"]],
        }
