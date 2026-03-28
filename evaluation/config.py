"""
Configuration Management

Loads configuration from environment variables.
"""
import os
from dataclasses import dataclass, field


@dataclass
class Config:
    """Configuration for MCP tool-calling evaluation"""

    # OpenEnv Server
    openenv_url: str = field(
        default_factory=lambda: os.getenv("OPENENV_URL", "http://localhost:8006")
    )

    # API Keys
    openai_api_key: str = field(
        default_factory=lambda: os.getenv("OPENAI_API_KEY", "")
    )
    anthropic_api_key: str = field(
        default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", "")
    )
    deepseek_api_key: str = field(
        default_factory=lambda: os.getenv("DEEPSEEK_API_KEY", "")
    )
    deepseek_base_url: str = field(
        default_factory=lambda: os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
    )
    openrouter_api_key: str = field(
        default_factory=lambda: os.getenv("OPENROUTER_API_KEY", "")
    )
    smithery_api_key: str = field(
        default_factory=lambda: os.getenv("SMITHERY_API_KEY", "")
    )

    # MCP Servers (Toucan-1.5M dataset)
    mcp_servers_path: str = field(
        default_factory=lambda: os.getenv(
            "MCP_SERVERS_PATH",
            "evaluation/servers/mcp_servers.json"
        )
    )

    # Models under test (per Section C.3)
    models_under_test: list = field(default_factory=lambda: [
        "openai:gpt-5.1",
        "anthropic:claude-sonnet-4-5-20250929",
        "deepseek:deepseek-chat"
    ])

    # Judge model
    # Default OpenAI GPT-5.1
    judge_model: str = field(
        default_factory=lambda: os.getenv("JUDGE_MODEL", "gpt-5.1")
    )

    # Execution settings
    max_tool_steps: int = field(
        default_factory=lambda: int(os.getenv("MAX_TOOL_STEPS", "10"))
    )
    concurrent_evaluations: int = field(
        default_factory=lambda: int(os.getenv("CONCURRENT_EVALUATIONS", "5"))
    )
    openenv_timeout: float = field(
        default_factory=lambda: float(os.getenv("OPENENV_TIMEOUT", "300"))
    )
    openenv_max_retries: int = field(
        default_factory=lambda: int(os.getenv("OPENENV_MAX_RETRIES", "3"))
    )

    def validate(self) -> list[str]:
        """Validate configuration and return list of errors."""
        errors = []

        if not self.openai_api_key:
            errors.append("OPENAI_API_KEY is required for judge evaluation")

        return errors

    def get_model_api_key(self, model_id: str) -> str:
        """Get API key for a model identifier."""
        provider = model_id.split(":")[0] if ":" in model_id else model_id

        if provider == "openai":
            return self.openai_api_key
        elif provider == "anthropic":
            return self.anthropic_api_key
        elif provider == "deepseek":
            return self.deepseek_api_key
        elif provider == "openrouter":
            return self.openrouter_api_key
        else:
            return ""
