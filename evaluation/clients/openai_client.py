"""
OpenAI API Client

Client for OpenAI's chat completions API with tool calling support.
"""
import json
import time

import httpx

from .base import BaseLLMClient, LLMResponse, ToolCall
from utils.tool_converter import normalize_parameters


class OpenAIClient(BaseLLMClient):
    """OpenAI API client with tool calling"""

    def __init__(self, api_key: str, model: str = "gpt-5.1"):
        self.api_key = api_key
        self.model = model
        self.base_url = "https://api.openai.com/v1"

    @property
    def provider(self) -> str:
        return "openai"

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

        # Set up request with optional reasoning
        if enable_reasoning:
            # When using reasoning_effort, temperature is NOT supported (must be 1.0/default)
            # and max_tokens becomes max_completion_tokens with higher limit
            request_body = {
                "model": self.model,
                "messages": api_messages,
                "max_completion_tokens": 16000,  # Higher limit for reasoning
                "reasoning_effort": "medium"  # none/minimal/low/medium/high/xhigh
            }
            # Note: temperature, top_p, presence_penalty, frequency_penalty not supported with reasoning
        else:
            request_body = {
                "model": self.model,
                "messages": api_messages,
                "temperature": temperature,
                "max_completion_tokens": 4096
            }

        if tools:
            request_body["tools"] = self._convert_tools(tools)

        # Longer timeout for reasoning mode (high effort can take 2+ minutes)
        timeout = 300.0 if enable_reasoning else 60.0

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                },
                json=request_body,
                timeout=timeout
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

        # Extract reasoning tokens from completion_tokens_details (GPT-5.1 feature)
        completion_details = usage.get("completion_tokens_details", {})
        reasoning_tokens = completion_details.get("reasoning_tokens", 0)

        # OpenAI may include reasoning in a separate field (model-dependent)
        reasoning_trace = message.get("reasoning", None)

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
