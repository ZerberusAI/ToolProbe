"""
Smithery MCP Client

HTTP client for calling Smithery-hosted MCP servers.
Supports API key rotation on rate limit errors (503).
Uses a persistent httpx client and per-server MCP session caching.
"""
import asyncio
import httpx
import json
import logging
import random
import re
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Retry configuration
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_DELAY = 1.0  # seconds
DEFAULT_RETRY_BACKOFF = 2.0  # exponential backoff multiplier

# MCP session cache TTL (seconds) - sessions expire after this duration
SESSION_CACHE_TTL = 300  # 5 minutes

# Sentinel for stateless MCP servers that don't return a session ID
_STATELESS_SESSION = "__stateless__"


class SmitheryClient:
    """HTTP client for calling Smithery MCP servers with API key rotation.

    Thread-safe: uses asyncio.Lock to protect key rotation and shared state.
    Maintains a persistent httpx client and caches MCP sessions per server URL.
    """

    def __init__(
        self,
        smithery_api_key: str,
        other_api_keys: list[str] | None = None,
        timeout: float = 30.0,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_delay: float = DEFAULT_RETRY_DELAY,
        retry_backoff: float = DEFAULT_RETRY_BACKOFF
    ):
        """
        Initialize Smithery client with key rotation support.

        Args:
            smithery_api_key: Primary API key for Smithery.ai
            other_api_keys: List of additional API keys for rotation on 503 errors
            timeout: Request timeout in seconds
            max_retries: Maximum number of retry attempts
            retry_delay: Initial delay between retries in seconds
            retry_backoff: Multiplier for exponential backoff
        """
        # Build pool of API keys - primary first, then others shuffled
        self._api_keys = [smithery_api_key]
        if other_api_keys:
            shuffled = other_api_keys.copy()
            random.shuffle(shuffled)
            self._api_keys.extend(shuffled)

        self._current_key_index = 0
        self.smithery_api_key = self._api_keys[0]  # Current active key
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.retry_backoff = retry_backoff
        self._request_id = 0

        # Thread safety: lock protects key rotation and request ID generation
        self._lock = asyncio.Lock()

        # Persistent HTTP client (reused across all tool calls)
        self._client = httpx.AsyncClient(timeout=self.timeout)

        # MCP session cache: server_url -> (session_id, created_at)
        self._session_cache: dict[str, tuple[str, float]] = {}

        logger.info(
            f"SmitheryClient initialized with {len(self._api_keys)} API key(s), "
            f"key ending: ...{self.smithery_api_key[-8:]}"
        )

    async def close(self):
        """Close the persistent HTTP client."""
        await self._client.aclose()

    def _rotate_key(self) -> bool:
        """
        Rotate to the next API key in the pool.
        MUST be called while holding self._lock.

        Returns:
            True if rotated to a new key, False if no more keys available
        """
        if self._current_key_index < len(self._api_keys) - 1:
            self._current_key_index += 1
            self.smithery_api_key = self._api_keys[self._current_key_index]
            # Invalidate all cached sessions since the key changed
            self._session_cache.clear()
            logger.info(
                f"Rotated to API key {self._current_key_index + 1}/{len(self._api_keys)} "
                f"(key ending: ...{self.smithery_api_key[-8:]})"
            )
            return True
        return False

    def _get_url_with_key(self, server_url: str, api_key: str) -> str:
        """Replace API key in URL with the given key (pure function, no shared state)."""
        if "api_key=" in server_url:
            return re.sub(r'api_key=[^&]+', f'api_key={api_key}', server_url)
        return server_url

    async def _next_request_id(self) -> int:
        """Generate next JSON-RPC request ID (thread-safe)."""
        async with self._lock:
            self._request_id += 1
            return self._request_id

    async def _get_current_key(self) -> str:
        """Get the current API key (thread-safe read)."""
        async with self._lock:
            return self.smithery_api_key

    def _get_cached_session(self, server_url: str) -> Optional[str]:
        """Get a cached MCP session ID if still valid."""
        cached = self._session_cache.get(server_url)
        if cached:
            session_id, created_at = cached
            if time.time() - created_at < SESSION_CACHE_TTL:
                return session_id
            # Expired - remove from cache
            del self._session_cache[server_url]
        return None

    def _cache_session(self, server_url: str, session_id: str) -> None:
        """Cache an MCP session ID."""
        self._session_cache[server_url] = (session_id, time.time())

    def _invalidate_session(self, server_url: str) -> None:
        """Remove a cached session (e.g. after an error)."""
        self._session_cache.pop(server_url, None)

    @staticmethod
    def _parse_sse_body(text: str) -> str:
        """Extract JSON payload from SSE-formatted response body."""
        if text.startswith("event:"):
            for line in text.split("\n"):
                if line.startswith("data:"):
                    return line[5:].strip()
        return text

    async def _initialize_session(
        self,
        server_url: str
    ) -> Optional[str]:
        """
        Get or create an MCP session for the given server URL.
        Returns a cached session if available, otherwise initialises a new one.
        """
        # Check cache first
        cached = self._get_cached_session(server_url)
        if cached:
            logger.debug(f"Reusing cached MCP session for {server_url[:50]}")
            return cached

        req_id = await self._next_request_id()
        init_request = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "openenv-server",
                    "version": "1.0.0"
                }
            }
        }

        try:
            resp = await self._client.post(
                server_url,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream"
                },
                json=init_request
            )

            if resp.status_code >= 400:
                logger.error(f"MCP init failed: HTTP {resp.status_code}")
                return None

            session_id = resp.headers.get("mcp-session-id")
            if not session_id:
                logger.info("MCP init succeeded - stateless server (no session ID)")

            text = self._parse_sse_body(resp.text)
            data = json.loads(text)
            if "error" in data:
                logger.error(f"MCP init error: {data['error']}")
                return None

            # Send initialized notification and cache session
            if session_id:
                await self._send_initialized_notification(server_url, session_id)
                self._cache_session(server_url, session_id)
            else:
                # Stateless server - cache a sentinel so we don't re-init every call
                session_id = _STATELESS_SESSION
                self._cache_session(server_url, session_id)

            logger.info(f"MCP session initialized: {session_id}")
            return session_id

        except Exception as e:
            logger.error(f"MCP init exception: {e}")
            return None

    async def _send_initialized_notification(
        self,
        server_url: str,
        session_id: str
    ) -> None:
        """Send the 'initialized' notification to complete MCP handshake."""
        notification = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized"
        }

        try:
            await self._client.post(
                server_url,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                    "mcp-session-id": session_id
                },
                json=notification
            )
            logger.debug(f"Sent initialized notification for session {session_id}")
        except Exception as e:
            # Non-fatal - some servers may not require this notification
            logger.warning(f"Failed to send initialized notification: {e}")

    async def call_tool(
        self,
        server_url: str,
        tool_name: str,
        arguments: dict[str, Any]
    ) -> dict:
        """
        Call a tool on a Smithery MCP server with retry logic and key rotation.

        On 503 errors (rate limiting), rotates to the next API key before retrying.
        Thread-safe: key rotation is protected by asyncio.Lock so concurrent
        prompt evaluations don't interfere with each other.

        Args:
            server_url: Resolved Smithery server URL (with API key substituted)
            tool_name: Name of the tool to call
            arguments: Tool arguments

        Returns:
            Standardized result dict with text, status_code, is_error, reward
        """
        last_error = None
        delay = self.retry_delay
        keys_tried = 0
        max_keys = len(self._api_keys)

        # Snapshot the current key at call start for this request
        current_key = await self._get_current_key()

        for attempt in range(self.max_retries + 1):
            if attempt > 0:
                logger.info(
                    f"Retry {attempt}/{self.max_retries} for {tool_name} "
                    f"after {delay:.1f}s delay"
                )
                await asyncio.sleep(delay)
                delay *= self.retry_backoff

            # Build URL with the key for this attempt
            current_url = self._get_url_with_key(server_url, current_key)

            result = await self._call_tool_once(
                current_url, tool_name, arguments
            )

            if not result.get("is_error"):
                return result

            error_text = result.get("text", "")
            status_code = result.get("status_code")
            is_rate_limited = status_code == 503
            is_retryable = (
                status_code in (503, 504) or
                "timeout" in error_text.lower() or
                "network_error" in error_text.lower()
            )

            if not is_retryable:
                return result

            last_error = result

            # On 503 (rate limit), try rotating to next API key under lock
            if is_rate_limited and keys_tried < max_keys - 1:
                async with self._lock:
                    if self._rotate_key():
                        current_key = self.smithery_api_key
                keys_tried += 1
                logger.info(
                    f"Rate limited (503) on {tool_name}, rotated API key "
                    f"({keys_tried + 1}/{max_keys} keys tried)"
                )
                delay = self.retry_delay
                continue

            # Invalidate session cache for this URL on errors
            self._invalidate_session(current_url)

            logger.warning(
                f"Retryable error for {tool_name}: {status_code}"
            )

        logger.error(
            f"All {self.max_retries} retries exhausted for {tool_name} "
            f"(tried {keys_tried + 1} API keys)"
        )
        return last_error

    async def _call_tool_once(
        self,
        server_url: str,
        tool_name: str,
        arguments: dict[str, Any]
    ) -> dict:
        """
        Make a single tool call attempt using the persistent HTTP client.

        Args:
            server_url: Resolved Smithery server URL (with API key already substituted)
            tool_name: Name of the tool to call
            arguments: Tool arguments

        Returns:
            Standardized result dict
        """
        req_id = await self._next_request_id()
        request_body = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments
            }
        }

        try:
            logger.info(f"Calling Smithery tool: {tool_name} at {server_url[:50]}...")

            # Get or create MCP session (cached)
            session_id = await self._initialize_session(server_url)
            if not session_id:
                return {
                    "text": json.dumps({
                        "error": "MCP initialization failed",
                        "message": "Could not initialize MCP session with server"
                    }),
                    "status_code": 503,
                    "is_error": True,
                    "reward": 0.0,
                    "done": False
                }

            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            }
            if session_id != _STATELESS_SESSION:
                headers["mcp-session-id"] = session_id

            resp = await self._client.post(
                server_url,
                headers=headers,
                json=request_body
            )

            logger.info(f"Smithery response status: {resp.status_code}")

            if resp.status_code >= 400:
                # Session might be stale - invalidate so next call gets a fresh one
                self._invalidate_session(server_url)
                error_text = resp.text
                logger.error(f"Smithery error: {error_text}")
                return {
                    "text": json.dumps({
                        "error": f"HTTP {resp.status_code}",
                        "message": error_text[:500]
                    }),
                    "status_code": resp.status_code,
                    "is_error": True,
                    "reward": 0.0,
                    "done": False
                }

            text = self._parse_sse_body(resp.text)
            data = json.loads(text)

            if "error" in data:
                error = data["error"]
                return {
                    "text": json.dumps({
                        "error": error.get("message", "Unknown error"),
                        "code": error.get("code", -1)
                    }),
                    "status_code": 400,
                    "is_error": True,
                    "reward": 0.0,
                    "done": False
                }

            result = data.get("result", {})
            content = result.get("content", [])

            text_parts = []
            for item in content:
                if item.get("type") == "text":
                    text_parts.append(item.get("text", ""))
                elif item.get("type") == "image":
                    text_parts.append(f"[Image: {item.get('mimeType', 'unknown')}]")
                else:
                    text_parts.append(str(item))

            result_text = "\n".join(text_parts) if text_parts else json.dumps(result)

            return {
                "text": result_text,
                "status_code": 200,
                "is_error": False,
                "reward": 1.0,
                "done": False
            }

        except httpx.TimeoutException:
            self._invalidate_session(server_url)
            logger.error(f"Timeout calling Smithery tool: {tool_name}")
            return {
                "text": json.dumps({
                    "error": "timeout",
                    "message": f"Request timed out after {self.timeout}s"
                }),
                "status_code": 504,
                "is_error": True,
                "reward": 0.0,
                "done": False
            }
        except httpx.RequestError as e:
            self._invalidate_session(server_url)
            logger.error(f"Network error calling Smithery: {e}")
            return {
                "text": json.dumps({
                    "error": "network_error",
                    "message": str(e)
                }),
                "status_code": 503,
                "is_error": True,
                "reward": 0.0,
                "done": False
            }
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON response from Smithery: {e}")
            return {
                "text": json.dumps({
                    "error": "invalid_json",
                    "message": str(e)
                }),
                "status_code": 502,
                "is_error": True,
                "reward": 0.0,
                "done": False
            }
        except Exception as e:
            self._invalidate_session(server_url)
            logger.error(f"Unexpected error calling Smithery: {e}")
            return {
                "text": json.dumps({
                    "error": "unexpected_error",
                    "message": str(e)
                }),
                "status_code": 500,
                "is_error": True,
                "reward": 0.0,
                "done": False
            }

    async def list_tools(self, server_url: str) -> list[dict]:
        """
        List available tools on a Smithery server.

        Args:
            server_url: Resolved Smithery server URL

        Returns:
            List of tool definitions
        """
        req_id = await self._next_request_id()
        request_body = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "tools/list",
            "params": {}
        }

        try:
            session_id = await self._initialize_session(server_url)
            if not session_id:
                logger.error("Failed to initialize MCP session for list_tools")
                return []

            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            }
            if session_id != _STATELESS_SESSION:
                headers["mcp-session-id"] = session_id

            resp = await self._client.post(
                server_url,
                headers=headers,
                json=request_body
            )

            if resp.status_code >= 400:
                logger.error(f"Failed to list tools: {resp.status_code}")
                return []

            text = self._parse_sse_body(resp.text)
            data = json.loads(text)
            if "error" in data:
                logger.error(f"Error listing tools: {data['error']}")
                return []

            result = data.get("result", {})
            return result.get("tools", [])

        except Exception as e:
            logger.error(f"Error listing tools: {e}")
            return []
