"""
Base MCP Server Framework

This module provides the foundational components for creating mock MCP servers
that implement the JSON-RPC 2.0 protocol for testing server-side attacks.
"""

from .protocol import (
    JSONRPCRequest,
    JSONRPCResponse,
    JSONRPCError,
    MCPInitializeResult,
    MCPTool,
    MCPToolCallResult,
)
from .injection import ScenarioManager, InjectionEngine
from .mcp_server import BaseMCPServer

__all__ = [
    "JSONRPCRequest",
    "JSONRPCResponse",
    "JSONRPCError",
    "MCPInitializeResult",
    "MCPTool",
    "MCPToolCallResult",
    "ScenarioManager",
    "InjectionEngine",
    "BaseMCPServer",
]
