"""
MCP Server Registry

Load and query MCP servers from the Toucan-1.5M dataset.
"""
import json
from pathlib import Path
from typing import Optional


class MCPServerRegistry:
    """
    Load and query MCP servers from Toucan-1.5M dataset.

    Usage:
        registry = MCPServerRegistry("servers/mcp_servers.json", "sm-xxx")

        # Get server by name
        server = registry.get_server("Exa Search")

        # Get all tools for a server
        tools = registry.get_tools("Exa Search")

        # Resolve URL with API key
        url = registry.get_resolved_url("Exa Search")
    """

    def __init__(self, servers_path: str, smithery_api_key: str = ""):
        self.servers_path = Path(servers_path)
        self.smithery_api_key = smithery_api_key
        self._servers: dict = {}
        self._load()

    def _load(self) -> None:
        """Load MCP servers from JSON file."""
        if not self.servers_path.exists():
            # Try relative to module
            alt_path = Path(__file__).parent.parent / "servers" / "mcp_servers.json"
            if alt_path.exists():
                self.servers_path = alt_path
            else:
                raise FileNotFoundError(f"MCP servers file not found: {self.servers_path}")

        with open(self.servers_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Index by server_name for fast lookup
        for server in data.get("servers", []):
            name = server.get("server_name", "")
            if name:
                self._servers[name.lower()] = server

    def get_server(self, name: str) -> Optional[dict]:
        """Get server config by name (case-insensitive)."""
        return self._servers.get(name.lower())

    def get_tools(self, server_name: str) -> list[str]:
        """Get list of tool names for a server."""
        server = self.get_server(server_name)
        if server:
            return server.get("tool_names", [])
        return []

    def get_resolved_url(self, server_name: str) -> Optional[str]:
        """Get server URL with Smithery API key substituted."""
        server = self.get_server(server_name)
        if not server:
            return None

        url = server.get("server_url", "")
        if "{smithery_api_key}" in url:
            if not self.smithery_api_key:
                raise ValueError("SMITHERY_API_KEY required for live MCP server access")
            url = url.replace("{smithery_api_key}", self.smithery_api_key)

        return url

    def list_servers(self) -> list[str]:
        """List all available server names."""
        return list(self._servers.keys())

    def search_by_tool(self, tool_name: str) -> list[dict]:
        """Find servers that provide a specific tool."""
        results = []
        for server in self._servers.values():
            if tool_name in server.get("tool_names", []):
                results.append(server)
        return results

    def search_by_category(self, category: str) -> list[dict]:
        """Find servers by primary label category."""
        results = []
        for server in self._servers.values():
            if server.get("primary_label", "").lower() == category.lower():
                results.append(server)
        return results

    def get_server_count(self) -> int:
        """Get total number of servers."""
        return len(self._servers)
