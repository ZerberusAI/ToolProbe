"""
JSON-RPC 2.0 Protocol Helpers for MCP

Implements the minimal subset of MCP protocol needed for evaluation:
- initialize
- tools/list
- tools/call
"""

from dataclasses import dataclass, field, asdict
from typing import Any, Optional
from enum import Enum
import uuid


class JSONRPCErrorCode(Enum):
    """Standard JSON-RPC 2.0 error codes"""
    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603


@dataclass
class JSONRPCError:
    """JSON-RPC 2.0 Error object"""
    code: int
    message: str
    data: Optional[Any] = None

    def to_dict(self) -> dict:
        result = {"code": self.code, "message": self.message}
        if self.data is not None:
            result["data"] = self.data
        return result

    @classmethod
    def method_not_found(cls, method: str) -> "JSONRPCError":
        return cls(
            code=JSONRPCErrorCode.METHOD_NOT_FOUND.value,
            message=f"Method not found: {method}"
        )

    @classmethod
    def invalid_params(cls, message: str) -> "JSONRPCError":
        return cls(
            code=JSONRPCErrorCode.INVALID_PARAMS.value,
            message=message
        )

    @classmethod
    def internal_error(cls, message: str) -> "JSONRPCError":
        return cls(
            code=JSONRPCErrorCode.INTERNAL_ERROR.value,
            message=message
        )


@dataclass
class JSONRPCRequest:
    """JSON-RPC 2.0 Request object"""
    method: str
    params: Optional[dict] = None
    id: Optional[str] = None
    jsonrpc: str = "2.0"

    @classmethod
    def from_dict(cls, data: dict) -> "JSONRPCRequest":
        return cls(
            method=data.get("method", ""),
            params=data.get("params"),
            id=data.get("id"),
            jsonrpc=data.get("jsonrpc", "2.0")
        )

    def to_dict(self) -> dict:
        result = {"jsonrpc": self.jsonrpc, "method": self.method}
        if self.params is not None:
            result["params"] = self.params
        if self.id is not None:
            result["id"] = self.id
        return result


@dataclass
class JSONRPCResponse:
    """JSON-RPC 2.0 Response object"""
    id: Optional[str]
    result: Optional[Any] = None
    error: Optional[JSONRPCError] = None
    jsonrpc: str = "2.0"

    def to_dict(self) -> dict:
        response = {"jsonrpc": self.jsonrpc, "id": self.id}
        if self.error is not None:
            response["error"] = self.error.to_dict()
        else:
            response["result"] = self.result
        return response

    @classmethod
    def success(cls, id: Optional[str], result: Any) -> "JSONRPCResponse":
        return cls(id=id, result=result)

    @classmethod
    def failure(cls, id: Optional[str], error: JSONRPCError) -> "JSONRPCResponse":
        return cls(id=id, error=error)


@dataclass
class MCPToolParameter:
    """MCP Tool Parameter definition"""
    name: str
    type: str
    description: str
    required: bool = False
    enum: Optional[list] = None
    default: Optional[Any] = None

    def to_schema(self) -> dict:
        schema = {"type": self.type, "description": self.description}
        if self.enum:
            schema["enum"] = self.enum
        if self.default is not None:
            schema["default"] = self.default
        return schema


@dataclass
class MCPTool:
    """MCP Tool definition"""
    name: str
    description: str
    parameters: list[MCPToolParameter] = field(default_factory=list)

    def to_dict(self) -> dict:
        properties = {}
        required = []
        for param in self.parameters:
            properties[param.name] = param.to_schema()
            if param.required:
                required.append(param.name)

        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": properties,
                "required": required
            }
        }


@dataclass
class MCPToolCallResult:
    """Result from an MCP tool call"""
    content: list[dict]  # MCP content blocks
    isError: bool = False

    def to_dict(self) -> dict:
        return {
            "content": self.content,
            "isError": self.isError
        }

    @classmethod
    def text_result(cls, text: str, is_error: bool = False) -> "MCPToolCallResult":
        return cls(
            content=[{"type": "text", "text": text}],
            isError=is_error
        )

    @classmethod
    def json_result(cls, data: Any, is_error: bool = False) -> "MCPToolCallResult":
        import json
        return cls(
            content=[{"type": "text", "text": json.dumps(data, indent=2)}],
            isError=is_error
        )


@dataclass
class MCPInitializeResult:
    """Result from MCP initialize call"""
    protocolVersion: str = "2024-11-05"
    serverInfo: dict = field(default_factory=lambda: {
        "name": "mock-mcp-server",
        "version": "1.0.0"
    })
    capabilities: dict = field(default_factory=lambda: {
        "tools": {}
    })

    def to_dict(self) -> dict:
        return {
            "protocolVersion": self.protocolVersion,
            "serverInfo": self.serverInfo,
            "capabilities": self.capabilities
        }


def generate_session_id() -> str:
    """Generate a unique session ID for MCP sessions"""
    return str(uuid.uuid4())
