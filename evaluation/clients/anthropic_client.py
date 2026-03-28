"""
Anthropic API Client

Client for Anthropic's messages API with tool use support.
"""
import json
import time

import httpx

from .base import BaseLLMClient, LLMResponse, ToolCall
from utils.tool_converter import normalize_parameters


class AnthropicClient(BaseLLMClient):
    """Anthropic API client with tool use"""

    def __init__(self, api_key: str, model: str = "claude-sonnet-4.5"):
        self.api_key = api_key
        self.model = model
        self.base_url = "https://api.anthropic.com/v1"

    @property
    def provider(self) -> str:
        return "anthropic"

    def _convert_tools(self, tools: list[dict]) -> list[dict]:
        """Convert MCP format to Anthropic format"""
        result = []
        for tool in tools:
            props = {}
            required = []

            # Normalize parameters to flat format (handles both flat and JSON Schema)
            params = normalize_parameters(tool.get("parameters", {}))

            for name, spec in params.items():
                props[name] = {
                    "type": spec.get("type", "string"),
                    "description": spec.get("description", "")
                }
                if spec.get("required"):
                    required.append(name)

            result.append({
                "name": tool["name"],
                "description": tool.get("description", ""),
                "input_schema": {
                    "type": "object",
                    "properties": props,
                    "required": required
                }
            })
        return result

    def _convert_messages(
        self,
        messages: list[dict],
        enable_reasoning: bool = False,
        last_thinking_block: dict | None = None
    ) -> list[dict]:
        """Convert messages to Anthropic format.

        Args:
            messages: List of messages to convert
            enable_reasoning: If True, include thinking blocks in assistant messages
            last_thinking_block: The thinking block from the previous response to include
        """
        api_messages = []
        # Track tool_use IDs for matching with tool_results
        tool_id_counter = 0
        last_tool_ids = []  # IDs from the most recent assistant tool_use

        for msg in messages:
            if msg["role"] == "tool_result":
                # Always use the tool_use ID from the previous assistant message
                # to ensure consistency (ignore any incoming tool_call_id)
                if last_tool_ids:
                    tool_use_id = last_tool_ids[0]  # Use first tool ID
                    last_tool_ids = last_tool_ids[1:]  # Consume it
                else:
                    tool_use_id = f"tool_{tool_id_counter}"
                api_messages.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": msg["content"]
                    }]
                })
            elif msg["role"] == "assistant" and msg.get("tool_calls"):
                content = []
                # Include thinking block if provided (for extended thinking mode)
                if enable_reasoning and msg.get("thinking_block"):
                    content.append(msg["thinking_block"])
                if msg.get("content"):
                    content.append({"type": "text", "text": msg["content"]})
                last_tool_ids = []  # Reset for this assistant message
                for tc in msg["tool_calls"]:
                    tool_id = tc.get("call_id")
                    if not tool_id:
                        tool_id = f"tool_{tool_id_counter}"
                        tool_id_counter += 1
                    last_tool_ids.append(tool_id)
                    content.append({
                        "type": "tool_use",
                        "id": tool_id,
                        "name": tc["tool"],
                        "input": tc["parameters"]
                    })
                api_messages.append({"role": "assistant", "content": content})
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
        start = time.time()

        api_messages = self._convert_messages(messages, enable_reasoning=enable_reasoning)

        # Extended thinking requires temperature=1.0 and higher max_tokens
        if enable_reasoning:
            request_body = {
                "model": self.model,
                "system": system_prompt,
                "messages": api_messages,
                "temperature": 1.0,  # Required for extended thinking
                "max_tokens": 16000,  # Higher limit for thinking + response
                "thinking": {
                    "type": "enabled",
                    "budget_tokens": 10000  # Reasoning budget
                }
            }
        else:
            request_body = {
                "model": self.model,
                "system": system_prompt,
                "messages": api_messages,
                "temperature": temperature,
                "max_tokens": 4096
            }

        if tools:
            request_body["tools"] = self._convert_tools(tools)

        # Build headers - add beta header for interleaved thinking with tool use
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json"
        }
        if enable_reasoning:
            headers["anthropic-beta"] = "interleaved-thinking-2025-05-14"

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.base_url}/messages",
                headers=headers,
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

        tool_calls = []
        text_response = ""
        reasoning_trace = None

        for block in data.get("content", []):
            if block["type"] == "thinking":
                # Extended thinking block - extract reasoning
                reasoning_trace = block.get("thinking", "")
            elif block["type"] == "text":
                text_response += block["text"]
            elif block["type"] == "tool_use":
                tool_calls.append(ToolCall(
                    tool=block["name"],
                    parameters=block["input"],
                    call_id=block.get("id")
                ))

        usage = data.get("usage", {})

        # Calculate reasoning tokens if available (Anthropic includes in output_tokens)
        # The reasoning content contributes to output tokens
        reasoning_tokens = 0
        if reasoning_trace:
            # Estimate based on typical token ratio (~1.3 tokens per word)
            reasoning_tokens = int(len(reasoning_trace.split()) * 1.3)

        return LLMResponse(
            tool_calls=tool_calls,
            text_response=text_response,
            raw_response=data,
            request_body=request_body,
            finish_reason=data.get("stop_reason", "end_turn"),
            latency_ms=latency,
            prompt_tokens=usage.get("input_tokens", 0),
            completion_tokens=usage.get("output_tokens", 0),
            reasoning_trace=reasoning_trace,
            reasoning_tokens=reasoning_tokens
        )
