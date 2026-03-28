"""
DeepSeek API Client.

Client for DeepSeek's OpenAI-compatible API with tool calling support.
"""
import json
import time

import httpx

from .base import LLMResponse, ToolCall
from .openai_client import OpenAIClient


class DeepSeekClient(OpenAIClient):
    """DeepSeek API client (OpenAI-compatible)"""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.deepseek.com/v1",
        model: str = "deepseek-chat"
    ):
        super().__init__(api_key, model)
        self.base_url = base_url.rstrip("/")

    @property
    def provider(self) -> str:
        return "deepseek"

    def _convert_messages_with_reasoning(
        self,
        system_prompt: str,
        messages: list[dict]
    ) -> list[dict]:
        """Convert messages to DeepSeek format with reasoning_content support.

        For deepseek-reasoner, assistant messages need to include reasoning_content
        when it was present in the original response.
        """
        api_messages = [{"role": "system", "content": system_prompt}]

        for msg in messages:
            if msg["role"] == "tool_result":
                api_messages.append({
                    "role": "tool",
                    "tool_call_id": msg.get("tool_call_id", "call_1"),
                    "content": msg["content"]
                })
            elif msg["role"] == "assistant" and msg.get("tool_calls"):
                # Convert tool calls to OpenAI format
                openai_tool_calls = []
                for tc in msg["tool_calls"]:
                    openai_tool_calls.append({
                        "id": tc.get("call_id", "call_1"),
                        "type": "function",
                        "function": {
                            "name": tc["tool"],
                            "arguments": json.dumps(tc["parameters"])
                        }
                    })
                assistant_msg = {
                    "role": "assistant",
                    "content": msg.get("content", ""),
                    "tool_calls": openai_tool_calls
                }
                # Always include reasoning_content for deepseek-reasoner multi-turn
                # DeepSeek API requires this field for all assistant messages with tool_calls
                # Must default to "" for historical messages that don't have the key
                assistant_msg["reasoning_content"] = msg.get("reasoning_content", "")
                api_messages.append(assistant_msg)
            elif msg["role"] == "assistant":
                # Assistant message without tool_calls - still needs reasoning_content
                api_messages.append({
                    "role": "assistant",
                    "content": msg.get("content", ""),
                    "reasoning_content": msg.get("reasoning_content", "")
                })
            else:
                api_messages.append({
                    "role": msg["role"],
                    "content": msg.get("content", "")
                })

        return api_messages

    async def chat_with_tools(
        self,
        system_prompt: str,
        messages: list[dict],
        tools: list[dict],
        temperature: float = 0.0,
        enable_reasoning: bool = False
    ) -> LLMResponse:
        """Override to use max_tokens and handle deepseek-reasoner specifics"""
        start = time.time()

        # Use deepseek-reasoner for reasoning mode
        model = "deepseek-reasoner" if enable_reasoning else self.model

        # Use reasoning-aware message conversion when reasoning is enabled
        if enable_reasoning:
            api_messages = self._convert_messages_with_reasoning(system_prompt, messages)
        else:
            api_messages = self._convert_messages(system_prompt, messages)

        # DeepSeek uses max_tokens, not max_completion_tokens
        # Note: deepseek-reasoner may have different temperature handling
        request_body = {
            "model": model,
            "messages": api_messages,
            "max_tokens": 16000 if enable_reasoning else 4096
        }

        # Only include temperature for non-reasoning mode
        if not enable_reasoning:
            request_body["temperature"] = temperature

        if tools:
            request_body["tools"] = self._convert_tools(tools)

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                },
                json=request_body,
                timeout=120.0  # Longer timeout for reasoning
            )
            if resp.status_code >= 400:
                error_body = resp.text
                raise httpx.HTTPStatusError(
                    f"API error {resp.status_code}: {error_body}",
                    request=resp.request,
                    response=resp
                )
            data = resp.json()

        latency = int((time.time() - start) * 1000)

        choice = data["choices"][0]
        message = choice["message"]

        tool_calls = []
        if message.get("tool_calls"):
            for tc in message["tool_calls"]:
                tool_calls.append(ToolCall(
                    tool=tc["function"]["name"],
                    parameters=json.loads(tc["function"]["arguments"]),
                    call_id=tc.get("id")
                ))

        usage = data.get("usage", {})

        # DeepSeek reasoner returns reasoning_content separately from content
        reasoning_trace = message.get("reasoning_content", None)

        # Estimate reasoning tokens if we have a reasoning trace
        reasoning_tokens = 0
        if reasoning_trace:
            reasoning_tokens = int(len(reasoning_trace.split()) * 1.3)

        return LLMResponse(
            tool_calls=tool_calls,
            text_response=message.get("content") or "",
            raw_response=data,
            request_body=request_body,
            finish_reason=choice.get("finish_reason", "stop"),
            latency_ms=latency,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            reasoning_trace=reasoning_trace,
            reasoning_tokens=reasoning_tokens
        )
