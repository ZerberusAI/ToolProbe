"""
MCP Server Registry

Manages the mapping of tools to Smithery MCP servers and resolves server URLs.
"""
import base64
import json
from pathlib import Path
from typing import Optional


class MCPServerRegistry:
    """Registry for Smithery MCP servers from Toucan dataset"""

    def __init__(self, servers_path: str, smithery_api_key: str = ""):
        self.servers_path = Path(servers_path)
        self.smithery_api_key = smithery_api_key
        self._servers: dict[str, dict] = {}  # server_name -> server_info
        self._tool_to_server: dict[str, str] = {}  # tool_name -> server_name
        self._author_to_server: dict[str, str] = {}  # author -> server_name
        self._load()

    def _load(self):
        """Load servers from JSON file"""
        if not self.servers_path.exists():
            raise FileNotFoundError(f"MCP servers file not found: {self.servers_path}")

        with open(self.servers_path) as f:
            data = json.load(f)

        servers = data.get("servers", [])
        for server in servers:
            name = server.get("server_name", "")
            author = server.get("author", "")
            if not name:
                continue

            self._servers[name] = server
            if author:
                self._author_to_server[author] = name

            # Map each tool to its server
            for tool_name in server.get("tool_names", []):
                self._tool_to_server[tool_name] = name

    def get_server(self, server_name: str) -> Optional[dict]:
        """Get server info by name"""
        return self._servers.get(server_name)

    def get_server_by_author(self, author: str) -> Optional[dict]:
        """Get server info by author (mcp_server field often uses author format)"""
        server_name = self._author_to_server.get(author)
        if server_name:
            return self._servers.get(server_name)
        return None

    def get_server_for_tool(self, tool_name: str) -> Optional[dict]:
        """Get server info for a tool name"""
        server_name = self._tool_to_server.get(tool_name)
        if server_name:
            return self._servers.get(server_name)
        return None

    def get_resolved_url(self, server_name: str) -> Optional[str]:
        """Get server URL with placeholders substituted"""
        server = self.get_server(server_name)
        if not server:
            return None

        url = server.get("server_url", "")
        if not url:
            return None

        # Substitute Smithery API key
        if "{smithery_api_key}" in url:
            if not self.smithery_api_key:
                raise ValueError(
                    f"SMITHERY_API_KEY required for server: {server_name}"
                )
            url = url.replace("{smithery_api_key}", self.smithery_api_key)

        # Substitute config placeholder with empty config
        if "{config_b64}" in url:
            empty_config = base64.b64encode(b"{}").decode("utf-8")
            url = url.replace("{config_b64}", empty_config)

        return url

    def get_resolved_url_for_tool(self, tool_name: str) -> Optional[str]:
        """Get resolved server URL for a tool name"""
        server_name = self._tool_to_server.get(tool_name)
        if server_name:
            return self.get_resolved_url(server_name)
        return None

    def is_smithery_server(self, mcp_server: str) -> bool:
        """
        Check if mcp_server identifier is a known Smithery server.

        mcp_server can be in formats:
        - Server name: "Exa Search"
        - Author format: "@author/package" or "author"
        - Tool name (fallback)
        """
        # Check by server name
        if mcp_server in self._servers:
            return True

        # Check by author
        if mcp_server in self._author_to_server:
            return True

        # Check if it's a tool name
        if mcp_server in self._tool_to_server:
            return True

        return False

    def resolve_mcp_server(self, mcp_server: str) -> Optional[dict]:
        """
        Resolve mcp_server identifier to server info.

        Tries multiple resolution strategies:
        1. Direct server name match
        2. Author match
        3. Tool name match (if mcp_server is actually a tool name)
        """
        # Try server name first
        if mcp_server in self._servers:
            return self._servers[mcp_server]

        # Try author
        server_name = self._author_to_server.get(mcp_server)
        if server_name:
            return self._servers.get(server_name)

        # Try as tool name (fallback)
        server_name = self._tool_to_server.get(mcp_server)
        if server_name:
            return self._servers.get(server_name)

        return None

    @property
    def server_count(self) -> int:
        """Number of registered servers"""
        return len(self._servers)

    @property
    def tool_count(self) -> int:
        """Number of registered tools"""
        return len(self._tool_to_server)

    def list_servers(self) -> list[str]:
        """List all server names"""
        return list(self._servers.keys())

    def list_tools(self) -> list[str]:
        """List all tool names"""
        return list(self._tool_to_server.keys())
