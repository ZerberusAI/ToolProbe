"""
OpenRouter API Client

Client for OpenRouter's OpenAI-compatible API with tool calling support.
OpenRouter provides access to multiple LLM providers through a unified API.
"""
import json
import time

import httpx

from .base import BaseLLMClient, LLMResponse, ToolCall
from utils.tool_converter import normalize_parameters


class OpenRouterClient(BaseLLMClient):
    """OpenRouter API client with tool calling"""

    def __init__(self, api_key: str, model: str = "openai/gpt-4o"):
        self.api_key = api_key
        self.model = model
        self.base_url = "https://openrouter.ai/api/v1"

    def _convert_tools(self, tools: list[dict]) -> list[dict]:
        """Convert MCP format to OpenAI function format"""
        result = []
        for tool in tools:
            props = {}
            required = []

            # Normalize parameters to flat format (handles both flat and JSON Schema)
            params = normalize_parameters(tool.get("parameters", {}))

            for name, spec in params.items():
                prop_type = spec.get("type", "string")
                prop_def = {
                    "type": prop_type,
                    "description": spec.get("description", "")
                }
                # Handle array types - OpenAI requires items schema
                if prop_type == "array":
                    items = spec.get("items", {"type": "string"})
                    prop_def["items"] = items
                if spec.get("enum"):
                    prop_def["enum"] = spec["enum"]
                if spec.get("required"):
                    required.append(name)
                props[name] = prop_def

            result.append({
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": {
                        "type": "object",
                        "properties": props,
                        "required": required
                    }
                }
            })
        return result

    def _convert_messages(
        self,
        system_prompt: str,
        messages: list[dict]
    ) -> list[dict]:
        """Convert messages to OpenAI format"""
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
                api_messages.append({
                    "role": "assistant",
                    "content": msg.get("content", ""),
                    "tool_calls": openai_tool_calls
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
        start = time.time()

        api_messages = self._convert_messages(system_prompt, messages)

        request_body = {
            "model": self.model,
            "messages": api_messages,
            "temperature": temperature,
            "max_tokens": 4096
        }

        if tools:
            request_body["tools"] = self._convert_tools(tools)

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://github.com/ZerberusAI/LLM-Evaluation",
                    "X-Title": "MCP Safety Evaluation"
                },
                json=request_body,
                timeout=120.0
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

        # Note: OpenRouter doesn't have standardized reasoning API
        # Reasoning trace would depend on the underlying model
        return LLMResponse(
            tool_calls=tool_calls,
            text_response=message.get("content") or "",
            raw_response=data,
            request_body=request_body,
            finish_reason=choice.get("finish_reason", "stop"),
            latency_ms=latency,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            reasoning_trace=None,
            reasoning_tokens=0
        )
