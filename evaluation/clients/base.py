"""
Base LLM Client

Abstract base class and shared data structures for LLM clients.
"""
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Model context limits (input + output tokens)
# These are conservative estimates - actual limits may vary by API version
MODEL_CONTEXT_LIMITS = {
    # OpenAI models
    "gpt-5.1": 131072,
    "gpt-4.1": 128000,
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
    "gpt-4-turbo": 128000,
    "gpt-4": 8192,
    "gpt-3.5-turbo": 16385,
    # DeepSeek models
    "deepseek-chat": 65536,
    "deepseek-reasoner": 65536,
    # Anthropic models
    "claude-sonnet-4-5-20250514": 200000,
    "claude-sonnet-4.5": 200000,
    "claude-opus-4-5-20251101": 200000,
    "claude-3-5-sonnet-20241022": 200000,
    "claude-3-opus-20240229": 200000,
    # Default fallback
    "default": 65536,
}

# Minimum completion tokens to reserve - if we can't fit this, fail gracefully
MIN_COMPLETION_TOKENS = 1024


def estimate_tokens(content: Any) -> int:
    """Estimate token count from content using a simple heuristic.

    Uses ~4 characters per token as a rough approximation.
    This is conservative to avoid underestimating.

    Args:
        content: String, dict, or list to estimate tokens for

    Returns:
        Estimated token count
    """
    if content is None:
        return 0
    if isinstance(content, str):
        # ~4 chars per token is a conservative estimate
        return len(content) // 4 + 1
    if isinstance(content, (dict, list)):
        # Serialize to JSON and estimate
        try:
            json_str = json.dumps(content, ensure_ascii=False)
            return len(json_str) // 4 + 1
        except (TypeError, ValueError):
            return 1000  # Fallback for non-serializable content
    return 100  # Fallback for unknown types


def get_context_limit(model: str) -> int:
    """Get the context limit for a model.

    Args:
        model: Model name (may include provider prefix)

    Returns:
        Context limit in tokens
    """
    # Strip provider prefix if present (e.g., "openai:gpt-5.1" -> "gpt-5.1")
    model_name = model.split(":")[-1] if ":" in model else model

    # Try exact match first
    if model_name in MODEL_CONTEXT_LIMITS:
        return MODEL_CONTEXT_LIMITS[model_name]

    # Try prefix matching for versioned models
    for known_model, limit in MODEL_CONTEXT_LIMITS.items():
        if model_name.startswith(known_model) or known_model.startswith(model_name):
            return limit

    return MODEL_CONTEXT_LIMITS["default"]


def calculate_safe_max_tokens(
    model: str,
    messages: list[dict],
    system_prompt: str = "",
    tools: list[dict] | None = None,
    desired_max_tokens: int = 16000,
    buffer_tokens: int = 500,
) -> int:
    """Calculate a safe max_tokens value that won't exceed context limits.

    Args:
        model: Model name
        messages: Conversation messages
        system_prompt: System prompt content
        tools: Tool definitions (if any)
        desired_max_tokens: Ideal max tokens for completion
        buffer_tokens: Safety buffer to account for estimation errors

    Returns:
        Safe max_tokens value, capped to fit within context

    Raises:
        ValueError: If messages are too large to fit even MIN_COMPLETION_TOKENS
    """
    context_limit = get_context_limit(model)

    # Estimate input token usage
    input_tokens = estimate_tokens(system_prompt)
    input_tokens += estimate_tokens(messages)
    if tools:
        input_tokens += estimate_tokens(tools)

    # Calculate available tokens for completion
    available = context_limit - input_tokens - buffer_tokens

    if available < MIN_COMPLETION_TOKENS:
        logger.error(
            f"Message too large for context: estimated {input_tokens} input tokens, "
            f"context limit {context_limit}, only {available} available for completion"
        )
        raise ValueError(
            f"Messages exceed context limit: ~{input_tokens} tokens used, "
            f"need at least {MIN_COMPLETION_TOKENS} for completion, "
            f"but only {available} available in {context_limit} token context"
        )

    # Cap to desired max or available, whichever is smaller
    safe_max = min(desired_max_tokens, available)

    if safe_max < desired_max_tokens:
        logger.warning(
            f"Reducing max_tokens from {desired_max_tokens} to {safe_max} "
            f"(estimated {input_tokens} input tokens, {context_limit} context limit)"
        )

    return safe_max


@dataclass
class ToolCall:
    """Represents a tool call from an LLM"""
    tool: str
    parameters: dict[str, Any]
    call_id: str | None = None


@dataclass
class LLMResponse:
    """Response from an LLM API"""
    tool_calls: list[ToolCall]
    text_response: str
    raw_response: dict
    request_body: dict  # Full request sent to API
    finish_reason: str
    latency_ms: int
    prompt_tokens: int
    completion_tokens: int
    reasoning_trace: str | None = None  # Extended thinking content from model
    reasoning_tokens: int = 0  # Token count for reasoning (for cost tracking)


@dataclass
class ToolLoopStep:
    """Single iteration in the tool-calling loop.

    Captures the complete state of one step including the model's request,
    response, reasoning, tool calls, and their results.
    """
    step_number: int
    request_body: dict
    raw_response: dict
    reasoning_trace: str | None
    reasoning_tokens: int
    tool_calls: list[ToolCall]
    tool_results: list[dict]
    text_response: str | None
    finish_reason: str
    latency_ms: int
    prompt_tokens: int
    completion_tokens: int


@dataclass
class ToolLoopTrace:
    """Complete trace of tool-calling loop execution.

    Contains all steps from the agentic loop plus aggregated metrics
    and backward-compatible flattened data structures.
    """
    steps: list[ToolLoopStep]
    total_steps: int
    total_tool_calls: int
    total_latency_ms: int
    total_prompt_tokens: int
    total_completion_tokens: int
    total_reasoning_tokens: int
    # Backward compatibility - flattened data
    all_tool_calls: list[dict]
    all_tool_results: list[dict]
    final_text_response: str
    last_request_body: dict
    last_raw_response: dict
    first_reasoning_trace: str | None


class BaseLLMClient(ABC):
    """Abstract base for LLM clients"""

    @abstractmethod
    async def chat_with_tools(
        self,
        system_prompt: str,
        messages: list[dict],
        tools: list[dict],
        temperature: float = 0.0,
        enable_reasoning: bool = False
    ) -> LLMResponse:
        """Send a chat request with tool definitions.

        Args:
            enable_reasoning: If True, enable extended thinking/reasoning mode
                for the model (provider-specific implementation).
        """
        pass
