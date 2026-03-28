"""
OpenEnv HTTP Client

Async client for interacting with the OpenEnv server.
"""
import asyncio
import logging
import httpx
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


class OpenEnvError(Exception):
    """Base exception for OpenEnv client errors"""
    pass


class OpenEnvTimeoutError(OpenEnvError):
    """Raised when a request to OpenEnv times out"""
    pass


@dataclass
class StateResponse:
    """State response from OpenEnv"""
    episode_id: str
    step_count: int
    database_id: str
    verification_result: list[dict] | None = None
    verification_query: str | None = None
    db_error: str | None = None


@dataclass
class ArtifactInfo:
    """Information about a created artifact/file"""
    path: str
    size_bytes: int
    content_type: str


@dataclass
class ArtifactContent:
    """Content of an artifact"""
    path: str
    content: str
    is_binary: bool
    truncated: bool


@dataclass
class ToolExecutionResult:
    """Result from tool execution"""
    text: str
    status_code: int
    is_error: bool
    reward: float


class OpenEnvClient:
    """Async HTTP client for OpenEnv API"""

    # Base timeout per tool step (seconds)
    BASE_TIMEOUT_PER_STEP = 60.0  # 1 minute per step
    MIN_TIMEOUT = 300.0  # 5 minutes minimum (single turn)
    MAX_TIMEOUT = 600.0  # 10 minutes maximum

    # Retry settings
    DEFAULT_MAX_RETRIES = 3
    RETRY_BACKOFF_BASE = 2.0  # seconds

    def __init__(
        self,
        base_url: str,
        database_id: str,
        timeout: float | None = None,
        max_retries: int | None = None
    ):
        """
        Initialize OpenEnv client.

        Args:
            base_url: Base URL of the OpenEnv server
            database_id: Database ID for tracking
            timeout: Optional custom timeout in seconds. If not provided,
                     uses MIN_TIMEOUT (300s / 5 minutes)
            max_retries: Maximum number of retries on timeout (default: 3)
        """
        self.base_url = base_url.rstrip("/")
        self.database_id = database_id
        self._timeout = timeout if timeout is not None else self.MIN_TIMEOUT
        self._max_retries = max_retries if max_retries is not None else self.DEFAULT_MAX_RETRIES
        self._client: httpx.AsyncClient | None = None

    @classmethod
    def calculate_timeout(cls, num_turns: int = 1, max_tool_steps: int = 10) -> float:
        """
        Calculate appropriate timeout based on conversation complexity.

        Args:
            num_turns: Number of conversation turns (1 for single turn)
            max_tool_steps: Maximum tool steps allowed per turn

        Returns:
            Timeout in seconds, clamped between MIN_TIMEOUT and MAX_TIMEOUT
        """
        # For single turn: use minimum (5 minutes)
        # For multi-turn: scale with number of turns
        if num_turns <= 1:
            return cls.MIN_TIMEOUT

        # Multi-turn: base timeout + (turns * steps * per-step timeout)
        calculated = cls.MIN_TIMEOUT + (num_turns * max_tool_steps * cls.BASE_TIMEOUT_PER_STEP / 10)
        return min(calculated, cls.MAX_TIMEOUT)

    async def __aenter__(self):
        self._client = httpx.AsyncClient(timeout=self._timeout)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._client:
            await self._client.aclose()
            self._client = None

    def _headers(self) -> dict:
        """Get headers for requests"""
        return {
            "x-database-id": self.database_id,
            "Content-Type": "application/json"
        }

    async def health(self) -> dict:
        """Check server health"""
        resp = await self._client.get(f"{self.base_url}/health")
        resp.raise_for_status()
        return resp.json()

    async def reset(self) -> dict:
        """Reset the environment"""
        resp = await self._client.post(
            f"{self.base_url}/reset",
            headers=self._headers()
        )
        resp.raise_for_status()
        return resp.json()

    async def list_tools(self) -> list[dict]:
        """List available tools"""
        resp = await self._client.post(
            f"{self.base_url}/step",
            headers=self._headers(),
            json={"action_type": "ListToolsAction"}
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("observation", {}).get("tools_list", [])

    async def execute_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        mcp_server: str = "",
        is_poisoned: bool = False,
        mock_server: str = "",
        attack_scenario: str = ""
    ) -> ToolExecutionResult:
        """
        Execute a tool with retry logic for timeouts.

        Args:
            tool_name: Name of the tool to execute
            arguments: Tool arguments
            mcp_server: MCP server identifier (for routing to Smithery)
            is_poisoned: Whether the tool is poisoned (forces mock execution)
            mock_server: Mock MCP server name (e.g., 'payment') for controlled attack scenarios
            attack_scenario: Attack scenario to use on mock server (e.g., 's3_injection')

        Raises:
            OpenEnvTimeoutError: If the request times out after all retries
            OpenEnvError: For other errors
        """
        last_error: Exception | None = None

        for attempt in range(self._max_retries + 1):
            try:
                resp = await self._client.post(
                    f"{self.base_url}/step",
                    headers=self._headers(),
                    json={
                        "action_type": "ToolCallAction",
                        "tool_name": tool_name,
                        "arguments": arguments,
                        "mcp_server": mcp_server,
                        "is_poisoned": is_poisoned,
                        "mock_server": mock_server,
                        "attack_scenario": attack_scenario
                    }
                )
                resp.raise_for_status()
                data = resp.json()

                observation = data.get("observation", {})
                tool_result = observation.get("tool_result", {})

                return ToolExecutionResult(
                    text=tool_result.get("text", ""),
                    status_code=tool_result.get("status_code", 200),
                    is_error=tool_result.get("isError", False),
                    reward=data.get("reward", 0.0)
                )
            except httpx.TimeoutException as e:
                last_error = e
                if attempt < self._max_retries:
                    backoff = self.RETRY_BACKOFF_BASE * (2 ** attempt)
                    logger.warning(
                        f"Timeout executing {tool_name} (attempt {attempt + 1}/{self._max_retries + 1}), "
                        f"retrying in {backoff}s..."
                    )
                    await asyncio.sleep(backoff)
                    continue
                # All retries exhausted
                raise OpenEnvTimeoutError(
                    f"Tool execution timed out after {self._timeout}s ({self._max_retries + 1} attempts): {tool_name}"
                ) from e
            except httpx.HTTPStatusError as e:
                raise OpenEnvError(
                    f"HTTP error {e.response.status_code}: {e.response.text[:200]}"
                ) from e

        # Should not reach here, but just in case
        raise OpenEnvTimeoutError(
            f"Tool execution failed after {self._max_retries + 1} attempts: {tool_name}"
        ) from last_error

    async def get_state(self, verify_query: str | None = None) -> StateResponse:
        """Get current environment state"""
        params = {}
        if verify_query:
            params["verify_query"] = verify_query

        resp = await self._client.get(
            f"{self.base_url}/state",
            headers=self._headers(),
            params=params
        )
        resp.raise_for_status()
        data = resp.json()

        return StateResponse(
            episode_id=data.get("episode_id", ""),
            step_count=data.get("step_count", 0),
            database_id=data.get("database_id", ""),
            verification_result=data.get("verification_result"),
            verification_query=data.get("verification_query"),
            db_error=data.get("db_error")
        )

    async def list_artifacts(self, paths: list[str] | None = None) -> list[ArtifactInfo]:
        """List artifacts created in the sandbox.

        Args:
            paths: Optional list of paths to check. If None, checks common locations
                   (/tmp, /workspace, /output, /results)
                   Note: /data is excluded as it contains mounted datasets, not artifacts

        Returns:
            List of ArtifactInfo objects describing found artifacts
        """
        try:
            resp = await self._client.post(
                f"{self.base_url}/artifacts/list",
                headers=self._headers(),
                json={"paths": paths or ["/tmp", "/workspace", "/output", "/results"]}
            )
            resp.raise_for_status()
            data = resp.json()
            return [ArtifactInfo(**a) for a in data.get("artifacts", [])]
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                # Endpoint not implemented on server
                logger.debug("Artifacts API not available on server")
                return []
            raise

    async def get_artifact(self, path: str, max_size: int = 50000) -> ArtifactContent:
        """Retrieve artifact content from sandbox.

        Args:
            path: Path to artifact in sandbox
            max_size: Maximum bytes to retrieve (truncates if larger)

        Returns:
            ArtifactContent with file content
        """
        try:
            resp = await self._client.post(
                f"{self.base_url}/artifacts/get",
                headers=self._headers(),
                json={"path": path, "max_size": max_size}
            )
            resp.raise_for_status()
            data = resp.json()
            return ArtifactContent(
                path=path,
                content=data.get("content", ""),
                is_binary=data.get("is_binary", False),
                truncated=data.get("truncated", False)
            )
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                # File or endpoint not found
                return ArtifactContent(
                    path=path,
                    content="[NOT FOUND]",
                    is_binary=False,
                    truncated=False
                )
            raise

    async def export_database(self) -> bytes | None:
        """Export the session database file.

        Returns the raw SQLite database bytes for this session.
        Used to save the database as an artifact in S3.

        Returns:
            Database file bytes, or None if export fails
        """
        try:
            resp = await self._client.get(
                f"{self.base_url}/database/export",
                headers=self._headers()
            )
            resp.raise_for_status()
            return resp.content
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.debug("Database export not available or database not found")
                return None
            logger.warning(f"Failed to export database: {e.response.status_code}")
            return None
        except Exception as e:
            logger.warning(f"Error exporting database: {e}")
            return None

    # -------------------------------------------------------------------------
    # Virtual File Mounting (U2 XPIA - Indirect Prompt Injection)
    # -------------------------------------------------------------------------

    async def mount_file(
        self,
        mount_path: str,
        content: str,
        file_type: str = "txt"
    ) -> bool:
        """
        Mount virtual file content for U2 XPIA scenarios.

        The mounted content will be returned by mock file-reading tools
        (read_file, analyze_document, etc.) when the agent attempts to
        read the specified path.

        Args:
            mount_path: Virtual path (e.g., '/uploads/report.pdf')
            content: Pre-extracted content with injection payload
            file_type: Simulated file type (pdf, docx, txt, etc.)

        Returns:
            True if mounted successfully, False otherwise
        """
        try:
            resp = await self._client.post(
                f"{self.base_url}/files/mount",
                headers=self._headers(),
                json={
                    "mount_path": mount_path,
                    "content": content,
                    "file_type": file_type
                }
            )
            resp.raise_for_status()
            data = resp.json()
            success = data.get("success", False)

            if success:
                logger.info(
                    f"[{self.database_id}] Mounted file: {mount_path} "
                    f"({len(content)} chars, type={file_type})"
                )
            else:
                logger.warning(
                    f"[{self.database_id}] Failed to mount file: {mount_path} - "
                    f"{data.get('message', 'Unknown error')}"
                )

            return success
        except httpx.HTTPStatusError as e:
            logger.error(
                f"[{self.database_id}] HTTP error mounting file {mount_path}: "
                f"{e.response.status_code}"
            )
            return False
        except Exception as e:
            logger.error(
                f"[{self.database_id}] Error mounting file {mount_path}: {e}"
            )
            return False

    async def list_mounted_files(self, directory: str = "/") -> list[dict]:
        """
        List mounted virtual files.

        Args:
            directory: Directory path to list (default: root)

        Returns:
            List of file info dicts with path, file_type, size_bytes
        """
        try:
            resp = await self._client.get(
                f"{self.base_url}/files/list",
                headers=self._headers(),
                params={"directory": directory}
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("files", [])
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.debug("File listing API not available")
                return []
            raise
        except Exception as e:
            logger.error(f"Error listing mounted files: {e}")
            return []

    async def clear_mounted_files(self) -> int:
        """
        Clear all mounted files for this session.

        Returns:
            Number of files cleared
        """
        try:
            resp = await self._client.post(
                f"{self.base_url}/files/clear",
                headers=self._headers()
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("cleared", 0)
        except Exception as e:
            logger.error(f"Error clearing mounted files: {e}")
            return 0
