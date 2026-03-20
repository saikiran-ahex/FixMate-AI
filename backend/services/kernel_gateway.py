from __future__ import annotations

import json
from typing import Any

from openai import AsyncOpenAI

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
        self.direct_client: AsyncOpenAI | None = None
        self.logger = get_logger("fixmate.kernel")
        self.last_error: str | None = None
        self.last_completion_error: str | None = None
        self.has_openai_key = self.settings.has_openai
        self.kernel_installed = Kernel is not None
        self.openai_connector_installed = OpenAIChatCompletion is not None

        if self.has_openai_key:
            self.direct_client = AsyncOpenAI(api_key=self.settings.openai_api_key)

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
            "last_completion_error": self.last_completion_error,
        }

    async def complete_text(self, system_prompt: str, user_prompt: str, temperature: float = 0.1) -> str:
        if not self.enabled or self.kernel is None or OpenAIChatPromptExecutionSettings is None or KernelArguments is None:
            raise RuntimeError(f"Semantic Kernel OpenAI gateway is not configured. {self.last_error or ''}".strip())

        execution_settings = OpenAIChatPromptExecutionSettings(
            service_id="chat",
            temperature=temperature,
        )
        prompt = (
            f"System instructions:\n{system_prompt}\n\n"
            f"User input:\n{user_prompt}\n"
        )
        log_event(self.logger, 20, "kernel_complete_text_started", temperature=temperature)
        try:
            result = await self.kernel.invoke_prompt(
                prompt,
                arguments=KernelArguments(settings=execution_settings),
            )
            text = str(result).strip()
            self.last_completion_error = None
            log_event(
                self.logger,
                20,
                "kernel_complete_text_completed",
                provider="semantic-kernel",
                response_length=len(text),
            )
            return text
        except Exception as error:
            self.last_completion_error = str(error)
            if not self._should_fallback_to_direct_openai(error):
                raise
            log_event(self.logger, 30, "kernel_complete_text_fallback", provider="semantic-kernel", error=str(error))
            return await self._complete_text_direct(system_prompt, user_prompt, temperature)

    async def complete_json(self, system_prompt: str, user_prompt: str, temperature: float = 0.0) -> dict[str, Any]:
        if not self.enabled or self.kernel is None or OpenAIChatPromptExecutionSettings is None or KernelArguments is None:
            raise RuntimeError(f"Semantic Kernel OpenAI gateway is not configured. {self.last_error or ''}".strip())

        execution_settings = OpenAIChatPromptExecutionSettings(
            service_id="chat",
            temperature=temperature,
            response_format={"type": "json_object"},
        )
        prompt = (
            f"System instructions:\n{system_prompt}\n\n"
            "Return valid JSON only. Do not wrap the response in markdown fences.\n\n"
            f"User input:\n{user_prompt}\n"
        )
        log_event(self.logger, 20, "kernel_complete_json_started", temperature=temperature)
        try:
            result = await self.kernel.invoke_prompt(
                prompt,
                arguments=KernelArguments(settings=execution_settings),
            )
            payload = json.loads(_extract_json(str(result).strip()))
            self.last_completion_error = None
            log_event(
                self.logger,
                20,
                "kernel_complete_json_completed",
                provider="semantic-kernel",
                keys=list(payload.keys()),
            )
            return payload
        except Exception as error:
            self.last_completion_error = str(error)
            if not self._should_fallback_to_direct_openai(error):
                raise
            log_event(self.logger, 30, "kernel_complete_json_fallback", provider="semantic-kernel", error=str(error))
            return await self._complete_json_direct(system_prompt, user_prompt, temperature)

    def _should_fallback_to_direct_openai(self, error: Exception) -> bool:
        if self.direct_client is None:
            return False
        return "Unrecognized request argument supplied: reasoning_effort" in str(error)

    async def _complete_text_direct(self, system_prompt: str, user_prompt: str, temperature: float) -> str:
        if self.direct_client is None:
            raise RuntimeError("Direct OpenAI client is not configured.")

        response = await self.direct_client.chat.completions.create(
            model=self.settings.openai_chat_model,
            temperature=temperature,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        text = (response.choices[0].message.content or "").strip()
        self.last_completion_error = None
        log_event(
            self.logger,
            20,
            "kernel_complete_text_completed",
            provider="openai-direct",
            response_length=len(text),
        )
        return text

    async def _complete_json_direct(self, system_prompt: str, user_prompt: str, temperature: float) -> dict[str, Any]:
        if self.direct_client is None:
            raise RuntimeError("Direct OpenAI client is not configured.")

        response = await self.direct_client.chat.completions.create(
            model=self.settings.openai_chat_model,
            temperature=temperature,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        text = (response.choices[0].message.content or "").strip()
        payload = json.loads(_extract_json(text))
        self.last_completion_error = None
        log_event(
            self.logger,
            20,
            "kernel_complete_json_completed",
            provider="openai-direct",
            keys=list(payload.keys()),
        )
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
