"""
OpenEnv Server for MCP Tool-Calling Safety Evaluation

Implements the OpenEnv API specification for tool execution simulation.
Routes tool calls to either Smithery MCP servers or mock handlers.
"""
import json
import logging
import mimetypes
import os
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query, Request
from pydantic import BaseModel

# Configure logging
log_level = os.getenv("LOG_LEVEL", "INFO")
logging.basicConfig(
    level=getattr(logging, log_level),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

# Add datapoints path for server registry import (works both locally and in container)
datapoints_path = os.getenv("DATAPOINTS_PATH", str(Path(__file__).parent.parent))
sys.path.insert(0, datapoints_path)

from servers.registry import MCPServerRegistry
from tools.registry import ToolRegistry
from tools.executor import ToolExecutor
from db.state import StateManager
from files.registry import FileRegistry

logger = logging.getLogger(__name__)


# --- Models ---

class StepRequest(BaseModel):
    action_type: str  # "ListToolsAction" | "ToolCallAction"
    tool_name: str | None = None
    arguments: dict[str, Any] | None = None
    mcp_server: str | None = None  # MCP server identifier for routing
    is_poisoned: bool = False  # Whether the tool is poisoned (attack simulation)
    mock_server: str | None = None  # Mock MCP server name (e.g., 'payment')
    attack_scenario: str | None = None  # Attack scenario to use (e.g., 's3_return_injection')


class ActionRequest(BaseModel):
    """Alternative nested format"""
    action: StepRequest | None = None


class ToolResult(BaseModel):
    text: str
    status_code: int
    isError: bool


class Observation(BaseModel):
    success: bool
    error_message: str | None
    tools_list: list[dict] | None
    tool_result: ToolResult | None


class StepResponse(BaseModel):
    observation: Observation
    reward: float
    done: bool


class ResetResponse(BaseModel):
    done: bool
    database_reset: dict
    metadata: dict


class StateResponse(BaseModel):
    episode_id: str
    step_count: int
    database_id: str
    verification_result: list[dict] | None = None
    verification_query: str | None = None
    db_error: str | None = None


class ArtifactListRequest(BaseModel):
    """Request to list artifacts in sandbox directories"""
    paths: list[str] | None = None


class ArtifactGetRequest(BaseModel):
    """Request to retrieve artifact content"""
    path: str
    max_size: int = 50000


class ArtifactInfo(BaseModel):
    """Information about a single artifact"""
    path: str
    size_bytes: int
    content_type: str


class ArtifactListResponse(BaseModel):
    """Response containing list of artifacts"""
    artifacts: list[ArtifactInfo]


class ArtifactContentResponse(BaseModel):
    """Response containing artifact content"""
    content: str
    is_binary: bool
    truncated: bool


class MountFileRequest(BaseModel):
    """Request to mount virtual file content for U2 XPIA scenarios"""
    mount_path: str
    content: str
    file_type: str = "txt"


class MountFileResponse(BaseModel):
    """Response from mounting a file"""
    success: bool
    mount_path: str
    message: str | None = None


class ListMountedFilesResponse(BaseModel):
    """Response listing mounted files"""
    files: list[dict]


# --- Application ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize resources on startup"""
    # Load configuration from environment
    smithery_api_key = os.getenv("SMITHERY_API_KEY", "")

    # Load additional Smithery API keys for rotation on rate limits
    other_smithery_keys_raw = os.getenv("OTHER_SMITHERY_API_KEYS", "")
    other_smithery_api_keys = None
    if other_smithery_keys_raw:
        try:
            other_smithery_api_keys = json.loads(other_smithery_keys_raw)
            logger.info(f"Loaded {len(other_smithery_api_keys)} additional Smithery API keys for rotation")
        except json.JSONDecodeError:
            logger.warning("Failed to parse OTHER_SMITHERY_API_KEYS as JSON")
    mcp_servers_path = os.getenv(
        "MCP_SERVERS_PATH",
        str(Path(__file__).parent.parent / "servers" / "mcp_servers.json")
    )

    # Initialize MCP server registry
    mcp_registry = None
    if smithery_api_key and Path(mcp_servers_path).exists():
        try:
            mcp_registry = MCPServerRegistry(
                servers_path=mcp_servers_path,
                smithery_api_key=smithery_api_key
            )
            logger.info(
                f"Loaded MCP registry with {mcp_registry.server_count} servers, "
                f"{mcp_registry.tool_count} tools"
            )
        except Exception as e:
            logger.warning(f"Failed to load MCP registry: {e}")
    else:
        if not smithery_api_key:
            logger.warning("SMITHERY_API_KEY not set, using mock handlers only")
        if not Path(mcp_servers_path).exists():
            logger.warning(f"MCP servers file not found: {mcp_servers_path}")

    app.state.tool_registry = ToolRegistry()
    app.state.tool_executor = ToolExecutor(
        registry=mcp_registry,
        smithery_api_key=smithery_api_key,
        other_smithery_api_keys=other_smithery_api_keys
    )
    app.state.state_manager = StateManager()
    app.state.mcp_registry = mcp_registry
    app.state.file_registry = FileRegistry()

    logger.info(
        f"OpenEnv server ready: smithery={'enabled' if smithery_api_key else 'DISABLED (no key)'}, "
        f"mcp_registry={'loaded' if mcp_registry else 'none'}"
    )

    yield

    # Cleanup
    await app.state.tool_executor.close()
    app.state.state_manager.close_all()


app = FastAPI(
    title="OpenEnv MCP Tool-Calling Server",
    description="Tool execution simulation for LLM safety evaluation",
    version="1.0.0",
    lifespan=lifespan
)


# --- Endpoints ---

@app.get("/health")
async def health():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "mcp-openenv-server",
        "timestamp": datetime.utcnow().isoformat() + "Z"
    }


@app.post("/reset", response_model=ResetResponse)
async def reset(
    x_database_id: str = Header(default="default"),
    x_access_token: str | None = Header(default=None)
):
    """Reset environment to initial state"""
    state_mgr: StateManager = app.state.state_manager

    # Reset database for this tenant
    state_mgr.reset_database(x_database_id)

    return ResetResponse(
        done=False,
        database_reset={
            "success": True,
            "message": "Database reset to clean state and seeded with sample data",
            "database_id": x_database_id,
            "seeded": True
        },
        metadata={
            "database_refreshed": True,
            "database_id": x_database_id
        }
    )


@app.post("/step", response_model=StepResponse)
async def step(
    request: StepRequest | ActionRequest,
    x_database_id: str = Header(default="default"),
    x_access_token: str | None = Header(default=None)
):
    """Execute an action in the environment"""
    # Handle nested action format
    if isinstance(request, ActionRequest) and request.action:
        request = request.action

    state_mgr: StateManager = app.state.state_manager
    tool_registry: ToolRegistry = app.state.tool_registry
    tool_executor: ToolExecutor = app.state.tool_executor

    # Get or create session
    session = state_mgr.get_session(x_database_id)

    if request.action_type == "ListToolsAction":
        # Return available tools
        tools = tool_registry.get_tools_for_session(session)

        state_mgr.increment_step(x_database_id)

        return StepResponse(
            observation=Observation(
                success=True,
                error_message=None,
                tools_list=tools,
                tool_result=None
            ),
            reward=0.0,
            done=False
        )

    elif request.action_type == "ToolCallAction":
        if not request.tool_name:
            return StepResponse(
                observation=Observation(
                    success=False,
                    error_message="tool_name is required for ToolCallAction",
                    tools_list=None,
                    tool_result=None
                ),
                reward=0.0,
                done=False
            )

        # Get file registry for U2 XPIA scenarios
        file_registry: FileRegistry = app.state.file_registry

        # Execute tool (with routing to Mock MCP, Smithery, or local mock)
        result = await tool_executor.execute(
            tool_name=request.tool_name,
            arguments=request.arguments or {},
            session=session,
            mcp_server=request.mcp_server or "",
            is_poisoned=request.is_poisoned,
            mock_server=request.mock_server or "",
            attack_scenario=request.attack_scenario or "",
            file_registry=file_registry
        )

        # Record action
        state_mgr.record_action(
            database_id=x_database_id,
            tool_name=request.tool_name,
            arguments=request.arguments or {},
            result=result
        )

        state_mgr.increment_step(x_database_id)

        return StepResponse(
            observation=Observation(
                success=not result.get("is_error", False),
                error_message=result.get("error_message"),
                tools_list=None,
                tool_result=ToolResult(
                    text=result.get("text", ""),
                    status_code=result.get("status_code", 200),
                    isError=result.get("is_error", False)
                )
            ),
            reward=result.get("reward", 1.0),
            done=result.get("done", False)
        )

    else:
        return StepResponse(
            observation=Observation(
                success=False,
                error_message=f"Unknown action_type: {request.action_type}",
                tools_list=None,
                tool_result=None
            ),
            reward=0.0,
            done=False
        )


@app.get("/state", response_model=StateResponse)
async def get_state(
    verify_query: str | None = Query(default=None),
    x_database_id: str = Header(default="default")
):
    """Get current environment state"""
    state_mgr: StateManager = app.state.state_manager

    session = state_mgr.get_session(x_database_id)

    response = StateResponse(
        episode_id=session.episode_id,
        step_count=session.step_count,
        database_id=x_database_id
    )

    # Execute verification query if provided
    if verify_query:
        try:
            result = state_mgr.execute_verify_query(x_database_id, verify_query)
            response.verification_result = result
            response.verification_query = verify_query
        except Exception as e:
            response.db_error = str(e)

    return response


@app.post("/artifacts/list", response_model=ArtifactListResponse)
async def list_artifacts(
    request: ArtifactListRequest,
    x_database_id: str = Header(default="default")
):
    """
    List files created in sandbox directories.

    Scans specified paths (default: /tmp, /workspace, /output, /results) for files
    created during evaluation. Returns metadata about each file found.
    Note: With real MCP behavior, artifacts are created on remote servers,
    so this endpoint may return empty results.
    """
    # Default paths to scan for artifacts
    paths = request.paths or ["/tmp", "/workspace", "/output", "/results"]
    artifacts = []

    # Directories to skip (system directories that shouldn't be scanned)
    skip_dirs = {
        ".git", "__pycache__", "node_modules", ".venv", "venv",
        ".cache", ".npm", ".pip", "db"  # Skip database directories
    }

    # File patterns to skip (OpenEnv internal files)
    skip_patterns = {"openenv_", ".db"}

    for base_path in paths:
        if not os.path.exists(base_path):
            continue

        try:
            for root, dirs, files in os.walk(base_path):
                # Skip system directories
                dirs[:] = [d for d in dirs if d not in skip_dirs]

                for filename in files:
                    # Skip internal OpenEnv files
                    if any(pattern in filename for pattern in skip_patterns):
                        continue

                    filepath = os.path.join(root, filename)
                    try:
                        stat = os.stat(filepath)
                        content_type = mimetypes.guess_type(filepath)[0] or "application/octet-stream"
                        artifacts.append(ArtifactInfo(
                            path=filepath,
                            size_bytes=stat.st_size,
                            content_type=content_type
                        ))
                    except OSError as e:
                        logger.warning(f"Failed to stat {filepath}: {e}")
                        continue
        except PermissionError as e:
            logger.warning(f"Permission denied scanning {base_path}: {e}")
            continue

    logger.info(f"[{x_database_id}] Found {len(artifacts)} artifact(s)")
    return ArtifactListResponse(artifacts=artifacts)


@app.post("/artifacts/get", response_model=ArtifactContentResponse)
async def get_artifact(
    request: ArtifactGetRequest,
    x_database_id: str = Header(default="default")
):
    """
    Retrieve file content from sandbox.

    Returns the content of the specified file, truncated if larger than max_size.
    Binary files are detected and marked accordingly.
    """
    path = request.path
    max_size = request.max_size

    # Security: prevent path traversal attacks
    # Normalize the path and check it doesn't escape allowed directories
    normalized_path = os.path.normpath(path)
    allowed_prefixes = ["/tmp", "/workspace", "/output", "/results"]

    if not any(normalized_path.startswith(prefix) for prefix in allowed_prefixes):
        raise HTTPException(
            status_code=403,
            detail=f"Access denied: path must be under {allowed_prefixes}"
        )

    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="File not found")

    if not os.path.isfile(path):
        raise HTTPException(status_code=400, detail="Path is not a file")

    try:
        stat = os.stat(path)
        truncated = stat.st_size > max_size

        # Try to read as text first
        try:
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read(max_size)
            is_binary = False
        except UnicodeDecodeError:
            # File is binary
            is_binary = True
            content = "[BINARY]"

        logger.info(f"[{x_database_id}] Retrieved artifact: {path} ({stat.st_size} bytes, binary={is_binary})")

        return ArtifactContentResponse(
            content=content,
            is_binary=is_binary,
            truncated=truncated
        )

    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied")
    except Exception as e:
        logger.error(f"Failed to read artifact {path}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# Virtual File Endpoints (U2 XPIA - Indirect Prompt Injection)
# =============================================================================

@app.post("/files/mount", response_model=MountFileResponse)
async def mount_file(
    request: MountFileRequest,
    x_database_id: str = Header(default="default")
):
    """
    Mount virtual file content for U2 XPIA scenarios.

    Content is stored in the session's FileRegistry and returned by mock
    file-reading tools (read_file, analyze_document, etc.) when the agent
    attempts to read the specified path.

    Args:
        request: Mount request with mount_path, content, and file_type
        x_database_id: Session identifier

    Returns:
        MountFileResponse with success status
    """
    file_registry: FileRegistry = app.state.file_registry

    success = file_registry.mount(
        database_id=x_database_id,
        mount_path=request.mount_path,
        content=request.content,
        file_type=request.file_type
    )

    if success:
        return MountFileResponse(
            success=True,
            mount_path=request.mount_path,
            message=f"File mounted successfully ({len(request.content)} chars)"
        )
    else:
        return MountFileResponse(
            success=False,
            mount_path=request.mount_path,
            message="Failed to mount file - invalid path or content too large"
        )


@app.get("/files/list", response_model=ListMountedFilesResponse)
async def list_mounted_files(
    directory: str = Query(default="/"),
    x_database_id: str = Header(default="default")
):
    """
    List mounted virtual files for a session.

    Args:
        directory: Directory path to list (default: root)
        x_database_id: Session identifier

    Returns:
        List of mounted file info dicts
    """
    file_registry: FileRegistry = app.state.file_registry
    files = file_registry.list_files(x_database_id, directory)
    return ListMountedFilesResponse(files=files)


@app.post("/files/clear")
async def clear_mounted_files(
    x_database_id: str = Header(default="default")
):
    """
    Clear all mounted files for a session.

    Args:
        x_database_id: Session identifier

    Returns:
        Count of files cleared
    """
    file_registry: FileRegistry = app.state.file_registry
    count = file_registry.clear(x_database_id)
    return {"cleared": count, "database_id": x_database_id}


# =============================================================================
# Admin Endpoints
# =============================================================================

@app.get("/admin/database-stats")
async def database_stats(request: Request):
    """
    Get statistics about OpenEnv session database files.

    Returns count, total size, and list of database files.
    """
    state_mgr: StateManager = request.app.state.state_manager
    stats = state_mgr.get_database_stats()
    logger.info(f"[ADMIN] Database stats: {stats['count']} files, {stats['total_size_mb']} MB")
    return stats


@app.post("/admin/cleanup-databases")
async def cleanup_databases(request: Request, max_age_hours: int = 24):
    """
    Delete old session database files to free disk space.

    Args:
        max_age_hours: Delete databases older than this many hours (default: 24)

    Returns:
        Count and list of deleted files.
    """
    state_mgr: StateManager = request.app.state.state_manager
    count, deleted_files = state_mgr.cleanup_old_databases(max_age_hours)
    logger.info(f"[ADMIN] Cleaned up {count} database files older than {max_age_hours} hours")
    return {
        "deleted_count": count,
        "deleted_files": deleted_files,
        "max_age_hours": max_age_hours
    }


@app.get("/database/export")
async def export_database(
    request: Request,
    x_database_id: str = Header(None, alias="X-Database-Id")
):
    """
    Export the session database file as bytes.

    Returns the raw SQLite database file for the specified session.
    Used by the backend to collect database as an artifact for S3 upload.

    Args:
        x_database_id: Session database identifier (from X-Database-Id header)

    Returns:
        Response with database bytes or error if database doesn't exist.
    """
    from fastapi.responses import Response

    if not x_database_id:
        raise HTTPException(status_code=400, detail="X-Database-Id header required")

    state_mgr: StateManager = request.app.state.state_manager
    db_content = state_mgr.get_database_content(x_database_id)

    if db_content is None:
        raise HTTPException(
            status_code=404,
            detail=f"Database not found: {x_database_id}"
        )

    db_path = state_mgr.get_database_path(x_database_id)
    filename = db_path.name if db_path else f"openenv_{x_database_id}.db"

    logger.info(f"[DATABASE] Exporting database: {filename} ({len(db_content)} bytes)")

    return Response(
        content=db_content,
        media_type="application/x-sqlite3",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Database-Size": str(len(db_content))
        }
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8005)
