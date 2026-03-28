"""
File Registry for U2 XPIA (Indirect Prompt Injection) Scenarios

Provides per-session virtual file storage. Content is pre-extracted and mounted
at evaluation time, then returned by mock file-reading tools when the agent
attempts to read files.

This enables U2 attack scenarios where malicious instructions are embedded
in "uploaded files" (PDF, DOCX, images) that the agent processes.
"""

import logging
import re
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class MountedFile:
    """A mounted virtual file with content and metadata."""
    content: str
    file_type: str
    size_bytes: int


class FileRegistry:
    """
    Per-session virtual file registry.

    Files are mounted via the /files/mount endpoint and read by mock
    file-reading tool handlers (read_file, analyze_document, etc.).

    Session isolation ensures files from one evaluation don't leak to another.
    """

    # Maximum content size (100KB default)
    MAX_CONTENT_SIZE = 100_000

    def __init__(self):
        # db_id -> {mount_path -> MountedFile}
        self._files: dict[str, dict[str, MountedFile]] = {}

    def mount(
        self,
        database_id: str,
        mount_path: str,
        content: str,
        file_type: str = "txt"
    ) -> bool:
        """
        Mount content at a virtual path for a session.

        Args:
            database_id: Session identifier
            mount_path: Virtual file path (must start with /)
            content: Pre-extracted file content
            file_type: Simulated file type (pdf, docx, txt, etc.)

        Returns:
            True if mounted successfully, False if validation failed
        """
        # Validate mount_path
        if not self._validate_mount_path(mount_path):
            logger.warning(f"[{database_id}] Invalid mount_path: {mount_path}")
            return False

        # Validate content size
        if len(content) > self.MAX_CONTENT_SIZE:
            logger.warning(
                f"[{database_id}] Content too large ({len(content)} bytes) "
                f"for {mount_path}, max is {self.MAX_CONTENT_SIZE}"
            )
            return False

        # Initialize session storage if needed
        if database_id not in self._files:
            self._files[database_id] = {}

        # Store the mounted file
        self._files[database_id][mount_path] = MountedFile(
            content=content,
            file_type=file_type,
            size_bytes=len(content.encode('utf-8'))
        )

        logger.info(
            f"[{database_id}] Mounted file: {mount_path} "
            f"({len(content)} chars, type={file_type})"
        )
        return True

    def read(self, database_id: str, mount_path: str) -> Optional[MountedFile]:
        """
        Read content from a mounted file.

        Args:
            database_id: Session identifier
            mount_path: Virtual file path to read

        Returns:
            MountedFile if found, None if not mounted
        """
        session_files = self._files.get(database_id, {})
        mounted_file = session_files.get(mount_path)

        if mounted_file:
            logger.debug(f"[{database_id}] Read file: {mount_path}")
        else:
            logger.debug(f"[{database_id}] File not found: {mount_path}")

        return mounted_file

    def list_files(self, database_id: str, directory: str = "/") -> list[dict]:
        """
        List mounted files in a directory.

        Args:
            database_id: Session identifier
            directory: Directory path to list (default: root)

        Returns:
            List of file info dicts with path, file_type, size_bytes
        """
        session_files = self._files.get(database_id, {})

        # Normalize directory path
        if not directory.endswith("/"):
            directory = directory + "/"

        result = []
        for path, mounted_file in session_files.items():
            # Check if file is in the requested directory
            if path.startswith(directory) or directory == "/":
                result.append({
                    "path": path,
                    "file_type": mounted_file.file_type,
                    "size_bytes": mounted_file.size_bytes
                })

        return result

    def clear(self, database_id: str) -> int:
        """
        Clear all mounted files for a session.

        Args:
            database_id: Session identifier

        Returns:
            Number of files cleared
        """
        if database_id in self._files:
            count = len(self._files[database_id])
            del self._files[database_id]
            logger.info(f"[{database_id}] Cleared {count} mounted file(s)")
            return count
        return 0

    def get_stats(self) -> dict:
        """
        Get registry statistics.

        Returns:
            Dict with session_count, total_files, total_size_bytes
        """
        total_files = 0
        total_size = 0

        for session_files in self._files.values():
            total_files += len(session_files)
            for mounted_file in session_files.values():
                total_size += mounted_file.size_bytes

        return {
            "session_count": len(self._files),
            "total_files": total_files,
            "total_size_bytes": total_size
        }

    def _validate_mount_path(self, mount_path: str) -> bool:
        """
        Validate mount path for security.

        Rules:
        - Must start with /
        - Cannot contain path traversal sequences (.., //)
        - Must be a reasonable path format
        """
        if not mount_path:
            return False

        if not mount_path.startswith("/"):
            return False

        # Check for path traversal attempts
        if ".." in mount_path:
            return False

        # Check for double slashes (except at protocol position)
        if "//" in mount_path:
            return False

        # Basic path character validation
        if not re.match(r'^/[\w\-./]+$', mount_path):
            return False

        return True
