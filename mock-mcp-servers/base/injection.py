"""
Attack Payload Injection Utilities

Provides mechanisms for:
- Loading attack scenarios from YAML files
- Injecting payloads into tool descriptions (S1 attacks)
- Injecting payloads into tool responses (S3 attacks)
- Runtime scenario switching via API
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import yaml


@dataclass
class ToolInjectionConfig:
    """Configuration for injecting payloads into a specific tool"""
    inject_in_description: bool = False
    description_payload: Optional[str] = None
    inject_in_response: bool = False
    response_template: Optional[str] = None
    response_payload: Optional[str] = None


@dataclass
class Scenario:
    """An attack scenario configuration"""
    name: str
    description: str
    tool_configs: dict[str, ToolInjectionConfig] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, name: str, data: dict) -> "Scenario":
        tool_configs = {}
        for tool_name, config in data.get("tool_responses", {}).items():
            tool_configs[tool_name] = ToolInjectionConfig(
                inject_in_description=config.get("inject_in_description", False),
                description_payload=config.get("description_payload"),
                inject_in_response=config.get("inject_in_response", False),
                response_template=config.get("response_template"),
                response_payload=config.get("response_payload"),
            )
        return cls(
            name=name,
            description=data.get("description", ""),
            tool_configs=tool_configs
        )


class ScenarioManager:
    """Manages loading and switching between attack scenarios"""

    def __init__(self, scenarios_dir: Optional[Path] = None):
        self.scenarios: dict[str, Scenario] = {}
        self.current_scenario: Optional[Scenario] = None
        self.scenarios_dir = scenarios_dir

        # Load default "normal" scenario
        self.scenarios["normal"] = Scenario(
            name="normal",
            description="Normal operation - no attack payloads"
        )
        self.current_scenario = self.scenarios["normal"]

        # Load scenarios from directory if provided
        if scenarios_dir and scenarios_dir.exists():
            self.load_scenarios_from_dir(scenarios_dir)

    def load_scenarios_from_dir(self, scenarios_dir: Path) -> None:
        """Load all YAML scenario files from a directory"""
        for yaml_file in scenarios_dir.glob("*.yaml"):
            self.load_scenario_file(yaml_file)

    def load_scenario_file(self, filepath: Path) -> None:
        """Load a single scenario YAML file"""
        with open(filepath, "r") as f:
            data = yaml.safe_load(f)

        scenario_name = data.get("scenario", filepath.stem)
        scenario = Scenario.from_dict(scenario_name, data)
        self.scenarios[scenario_name] = scenario

    def switch_scenario(self, scenario_name: str) -> bool:
        """Switch to a different scenario"""
        if scenario_name not in self.scenarios:
            return False
        self.current_scenario = self.scenarios[scenario_name]
        return True

    def get_current_scenario(self) -> Optional[Scenario]:
        """Get the currently active scenario"""
        return self.current_scenario

    def list_scenarios(self) -> list[str]:
        """List all available scenario names"""
        return list(self.scenarios.keys())

    def get_tool_config(self, tool_name: str) -> Optional[ToolInjectionConfig]:
        """Get injection config for a specific tool in current scenario"""
        if self.current_scenario is None:
            return None
        return self.current_scenario.tool_configs.get(tool_name)


class InjectionEngine:
    """Engine for injecting attack payloads into tool descriptions and responses"""

    def __init__(self, scenario_manager: ScenarioManager):
        self.scenario_manager = scenario_manager

    def inject_description(self, tool_name: str, original_description: str) -> str:
        """
        Inject attack payload into a tool description (S1 attack).
        Returns modified description if injection is configured, else original.
        """
        config = self.scenario_manager.get_tool_config(tool_name)
        if config is None or not config.inject_in_description:
            return original_description

        if config.description_payload:
            # Append payload to description
            return f"{original_description}\n\n{config.description_payload}"
        return original_description

    def inject_response(
        self,
        tool_name: str,
        original_response: Any,
        call_params: Optional[dict] = None
    ) -> Any:
        """
        Inject attack payload into a tool response (S3 attack).
        Returns modified response if injection is configured, else original.
        """
        config = self.scenario_manager.get_tool_config(tool_name)
        if config is None or not config.inject_in_response:
            return original_response

        # If we have a response template, use it
        if config.response_template:
            return self._render_template(
                config.response_template,
                call_params or {}
            )

        # If we have a response payload, inject it into the response
        if config.response_payload:
            return self._inject_payload_into_response(
                original_response,
                config.response_payload
            )

        return original_response

    def _render_template(self, template: str, params: dict) -> Any:
        """
        Render a response template with Jinja2-style {{ variable }} substitution.
        """
        result = template
        for key, value in params.items():
            placeholder = "{{ " + key + " }}"
            result = result.replace(placeholder, str(value))
            # Also try without spaces
            placeholder_no_space = "{{" + key + "}}"
            result = result.replace(placeholder_no_space, str(value))

        # Try to parse as JSON if it looks like JSON
        result = result.strip()
        if result.startswith("{") or result.startswith("["):
            try:
                return json.loads(result)
            except json.JSONDecodeError:
                pass
        return result

    def _inject_payload_into_response(
        self,
        response: Any,
        payload: str
    ) -> Any:
        """
        Inject a payload string into an existing response.
        For dict responses, adds it to a _metadata field.
        For string responses, appends it.
        """
        if isinstance(response, dict):
            response = response.copy()
            response["_metadata"] = payload
            return response
        elif isinstance(response, str):
            return f"{response}\n{payload}"
        else:
            # Convert to string and append
            return f"{response}\n{payload}"

    def should_inject_description(self, tool_name: str) -> bool:
        """Check if description injection is enabled for a tool"""
        config = self.scenario_manager.get_tool_config(tool_name)
        return config is not None and config.inject_in_description

    def should_inject_response(self, tool_name: str) -> bool:
        """Check if response injection is enabled for a tool"""
        config = self.scenario_manager.get_tool_config(tool_name)
        return config is not None and config.inject_in_response
