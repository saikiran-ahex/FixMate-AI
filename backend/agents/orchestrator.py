from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from services import DatabaseService, QdrantKnowledgeBase, SemanticKernelGateway
from utils import DataRepository, get_logger, log_event

from .auto_resolver import AutoResolverAgent
from .rag_agent import RAGAgent
from .router import RouterAgent
from .triage_agent import TriageAgent
from .troubleshooting_agent import TroubleshootingAgent


@dataclass
class SupportOrchestrator:
    repository: DataRepository = field(default_factory=DataRepository.from_project_root)
    database: DatabaseService = field(default_factory=DatabaseService)
    gateway: SemanticKernelGateway = field(default_factory=SemanticKernelGateway)
    knowledge_base: QdrantKnowledgeBase | None = None
    router: RouterAgent | None = None
    rag_agent: RAGAgent | None = None
    triage_agent: TriageAgent | None = None
    auto_resolver: AutoResolverAgent | None = None
    troubleshooting_agent: TroubleshootingAgent | None = None

    def __post_init__(self) -> None:
        self.logger = get_logger("fixmate.orchestrator")
        self.knowledge_base = self.knowledge_base or QdrantKnowledgeBase(
            repository=self.repository,
            settings=self.gateway.settings,
            database=self.database,
        )
        self.router = self.router or RouterAgent(gateway=self.gateway)
        self.rag_agent = self.rag_agent or RAGAgent(gateway=self.gateway, knowledge_base=self.knowledge_base)
        self.triage_agent = self.triage_agent or TriageAgent(gateway=self.gateway, repository=self.repository)
        self.auto_resolver = self.auto_resolver or AutoResolverAgent(gateway=self.gateway, repository=self.repository)
        self.troubleshooting_agent = self.troubleshooting_agent or TroubleshootingAgent(gateway=self.gateway, repository=self.repository)

    def handle_query(self, user_query: str) -> dict[str, Any]:
        return asyncio.run(self.handle_query_async(user_query))

    def continue_conversation(self, conversation_id: str, answers: dict[str, str]) -> dict[str, Any]:
        return asyncio.run(self.continue_conversation_async(conversation_id, answers))

    async def handle_query_async(self, user_query: str) -> dict[str, Any]:
        log_event(self.logger, 20, "orchestration_started", query_preview=user_query[:120])
        routing = await self.router.classify(user_query)
        log_event(self.logger, 20, "orchestration_routing_decided", route=routing["route"], confidence=routing.get("confidence"))
        if routing["route"] == "rag":
            rag_result = await self.rag_agent.answer_query(user_query)
            result = {"routing": routing, **rag_result}
            log_event(self.logger, 20, "orchestration_completed", final_agent=result.get("agent"), route="rag")
            return result

        triage = await self.triage_agent.analyze(user_query)
        log_event(self.logger, 20, "orchestration_triage_completed", recommended_handler=triage.get("recommended_handler"), appliance_type=triage.get("appliance_type"), issue_category=triage.get("issue_category"))
        if triage["recommended_handler"] == "auto_resolver":
            resolution = await self.auto_resolver.resolve(user_query, triage)
            if resolution["resolved"]:
                result = {"routing": routing, "triage": triage, **resolution}
                log_event(self.logger, 20, "orchestration_completed", final_agent=result.get("agent"), route="troubleshoot")
                return result

        troubleshooting = await self.troubleshooting_agent.start(user_query, triage)
        result = {"routing": routing, "triage": triage, **troubleshooting}
        log_event(self.logger, 20, "orchestration_completed", final_agent=result.get("agent"), route="troubleshoot")
        return result

    async def continue_conversation_async(self, conversation_id: str, answers: dict[str, str]) -> dict[str, Any]:
        log_event(self.logger, 20, "orchestration_continue_started", conversation_id=conversation_id)
        result = await self.troubleshooting_agent.continue_session(conversation_id, answers)
        log_event(self.logger, 20, "orchestration_continue_completed", conversation_id=conversation_id, resolved=result.get("resolved"))
        return result
