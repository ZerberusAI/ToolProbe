"""
Tool Registry

Manages available tools for MCP sessions.
"""
from typing import Any


class ToolRegistry:
    """Registry of available MCP tools"""

    def __init__(self):
        # Default tools available in all sessions
        self._default_tools = [
            {
                "name": "execute_command",
                "description": "Execute a shell command on the user's system",
                "parameters": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute",
                        "required": True
                    },
                    "working_directory": {
                        "type": "string",
                        "description": "Directory to execute the command in",
                        "required": False
                    }
                }
            },
            {
                "name": "kill_process",
                "description": "Terminate a running process by name or PID",
                "parameters": {
                    "process_identifier": {
                        "type": "string",
                        "description": "Process name or PID to kill",
                        "required": True
                    },
                    "force": {
                        "type": "boolean",
                        "description": "Force kill (SIGKILL) vs graceful (SIGTERM)",
                        "required": False,
                        "default": False
                    }
                }
            },
            {
                "name": "move_file",
                "description": "Move a file or directory to a new location",
                "parameters": {
                    "source": {
                        "type": "string",
                        "description": "Source path of file or directory",
                        "required": True
                    },
                    "destination": {
                        "type": "string",
                        "description": "Destination path",
                        "required": True
                    }
                }
            },
            {
                "name": "list_directory",
                "description": "List files in a directory",
                "parameters": {
                    "path": {
                        "type": "string",
                        "description": "Directory path to list",
                        "required": True
                    }
                }
            },
            {
                "name": "execute_query",
                "description": "Execute a database query",
                "parameters": {
                    "query": {
                        "type": "string",
                        "description": "SQL query to execute",
                        "required": True
                    },
                    "database": {
                        "type": "string",
                        "description": "Database name",
                        "required": False
                    }
                }
            },
            {
                "name": "find",
                "description": "Find documents in a MongoDB collection",
                "parameters": {
                    "collection": {
                        "type": "string",
                        "description": "Collection name",
                        "required": True
                    },
                    "filter": {
                        "type": "object",
                        "description": "Query filter",
                        "required": False
                    }
                }
            },
            {
                "name": "web_search_exa",
                "description": "Search the web using Exa",
                "parameters": {
                    "query": {
                        "type": "string",
                        "description": "Search query",
                        "required": True
                    },
                    "num_results": {
                        "type": "integer",
                        "description": "Number of results",
                        "required": False,
                        "default": 10
                    }
                }
            },
            {
                "name": "send_email",
                "description": "Send an email",
                "parameters": {
                    "to": {
                        "type": "string",
                        "description": "Recipient email address",
                        "required": True
                    },
                    "subject": {
                        "type": "string",
                        "description": "Email subject",
                        "required": True
                    },
                    "body": {
                        "type": "string",
                        "description": "Email body",
                        "required": True
                    }
                }
            },
            {
                "name": "playwright_navigate",
                "description": "Navigate to a URL in the browser",
                "parameters": {
                    "url": {
                        "type": "string",
                        "description": "URL to navigate to",
                        "required": True
                    }
                }
            },
            {
                "name": "playwright_fill",
                "description": "Fill a form field in the browser",
                "parameters": {
                    "selector": {
                        "type": "string",
                        "description": "CSS selector for the element",
                        "required": True
                    },
                    "value": {
                        "type": "string",
                        "description": "Value to fill",
                        "required": True
                    }
                }
            }
        ]

    def get_tools_for_session(self, session: Any) -> list[dict]:
        """Get available tools for a session"""
        # For now, return default tools
        # In the future, this could be customized per session/datapoint
        return self._default_tools

    def register_tool(self, tool: dict) -> None:
        """Register a new tool"""
        self._default_tools.append(tool)

    def get_tool(self, name: str) -> dict | None:
        """Get a tool by name"""
        for tool in self._default_tools:
            if tool["name"] == name:
                return tool
        return None
