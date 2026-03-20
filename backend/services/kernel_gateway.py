from __future__ import annotations

import json
from typing import Any

from settings import Settings
from utils import get_logger, log_event

try:
    from semantic_kernel import Kernel
    from semantic_kernel.connectors.ai.open_ai import OpenAIChatCompletion, OpenAIChatPromptExecutionSettings
    from semantic_kernel.functions import KernelArguments
except ImportError:  # pragma: no cover
    Kernel = None
    OpenAIChatCompletion = None
    OpenAIChatPromptExecutionSettings = None
    KernelArguments = None


class SemanticKernelGateway:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings()
        self.enabled = False
        self.kernel = None
        self.logger = get_logger("fixmate.kernel")
        self.last_error: str | None = None
        self.has_openai_key = self.settings.has_openai
        self.kernel_installed = Kernel is not None
        self.openai_connector_installed = OpenAIChatCompletion is not None

        if not self.has_openai_key or not self.kernel_installed or not self.openai_connector_installed:
            reasons = []
            if not self.has_openai_key:
                reasons.append("OPENAI_API_KEY missing")
            if not self.kernel_installed:
                reasons.append("semantic-kernel package unavailable")
            if not self.openai_connector_installed:
                reasons.append("semantic-kernel OpenAI connector unavailable")
            self.last_error = "; ".join(reasons)
            log_event(
                self.logger,
                30,
                "kernel_gateway_unavailable",
                has_openai=self.has_openai_key,
                kernel_installed=self.kernel_installed,
                openai_connector_installed=self.openai_connector_installed,
                reason=self.last_error,
            )
            return

        try:
            kernel = Kernel()
            kernel.add_service(
                OpenAIChatCompletion(
                    service_id="chat",
                    ai_model_id=self.settings.openai_chat_model,
                    api_key=self.settings.openai_api_key,
                    instruction_role="developer",
                )
            )
            self.kernel = kernel
            self.enabled = True
            log_event(self.logger, 20, "kernel_gateway_initialized", model=self.settings.openai_chat_model)
        except Exception as error:
            self.kernel = None
            self.enabled = False
            self.last_error = str(error)
            log_event(self.logger, 40, "kernel_gateway_init_failed", error=str(error))

    @property
    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "has_openai_key": self.has_openai_key,
            "kernel_installed": self.kernel_installed,
            "openai_connector_installed": self.openai_connector_installed,
            "model": self.settings.openai_chat_model,
            "last_error": self.last_error,
        }

    async def complete_text(self, system_prompt: str, user_prompt: str, temperature: float = 0.1) -> str:
        if not self.enabled or self.kernel is None or OpenAIChatPromptExecutionSettings is None or KernelArguments is None:
            raise RuntimeError(f"Semantic Kernel OpenAI gateway is not configured. {self.last_error or ''}".strip())

        execution_settings = OpenAIChatPromptExecutionSettings(
            service_id="chat",
            temperature=temperature,
            reasoning_effort="medium",
        )
        prompt = (
            f"System instructions:\n{system_prompt}\n\n"
            f"User input:\n{user_prompt}\n"
        )
        log_event(self.logger, 20, "kernel_complete_text_started", temperature=temperature)
        result = await self.kernel.invoke_prompt(
            prompt,
            arguments=KernelArguments(settings=execution_settings),
        )
        text = str(result).strip()
        log_event(self.logger, 20, "kernel_complete_text_completed", response_length=len(text))
        return text

    async def complete_json(self, system_prompt: str, user_prompt: str, temperature: float = 0.0) -> dict[str, Any]:
        if not self.enabled or self.kernel is None or OpenAIChatPromptExecutionSettings is None or KernelArguments is None:
            raise RuntimeError(f"Semantic Kernel OpenAI gateway is not configured. {self.last_error or ''}".strip())

        execution_settings = OpenAIChatPromptExecutionSettings(
            service_id="chat",
            temperature=temperature,
            reasoning_effort="medium",
            response_format={"type": "json_object"},
        )
        prompt = (
            f"System instructions:\n{system_prompt}\n\n"
            "Return valid JSON only. Do not wrap the response in markdown fences.\n\n"
            f"User input:\n{user_prompt}\n"
        )
        log_event(self.logger, 20, "kernel_complete_json_started", temperature=temperature)
        result = await self.kernel.invoke_prompt(
            prompt,
            arguments=KernelArguments(settings=execution_settings),
        )
        payload = json.loads(_extract_json(str(result).strip()))
        log_event(self.logger, 20, "kernel_complete_json_completed", keys=list(payload.keys()))
        return payload


def _extract_json(raw_text: str) -> str:
    text = raw_text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            text = "\n".join(lines[1:-1]).strip()

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"Model did not return JSON: {raw_text}")
    return text[start : end + 1]
