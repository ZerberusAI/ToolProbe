"""
State Manager

Manages session state and action recording using SQLite.
Supports multi-tenancy via database_id.
"""
import json
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class Session:
    """Session state"""
    episode_id: str
    step_count: int
    database_id: str
    created_at: str


class StateManager:
    """Manage environment state with SQLite"""

    def __init__(self, db_path: str | None = None):
        self._db_path = db_path or os.getenv("DATABASE_PATH", "/data/db/openenv.db")
        self._connections: dict[str, sqlite3.Connection] = {}
        self._sessions: dict[str, Session] = {}

        # Ensure db directory exists
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)

    def _get_connection(self, database_id: str) -> sqlite3.Connection:
        """Get or create connection for database_id"""
        if database_id not in self._connections:
            # Each tenant gets their own database file
            db_file = self._db_path.replace(".db", f"_{database_id}.db")
            conn = sqlite3.connect(db_file, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            self._init_schema(conn)
            self._connections[database_id] = conn
        return self._connections[database_id]

    def _init_schema(self, conn: sqlite3.Connection) -> None:
        """Initialize database schema"""
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                episode_id TEXT PRIMARY KEY,
                database_id TEXT NOT NULL,
                step_count INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                episode_id TEXT NOT NULL,
                step_number INTEGER NOT NULL,
                tool_name TEXT NOT NULL,
                arguments TEXT NOT NULL,
                result TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                FOREIGN KEY (episode_id) REFERENCES sessions(episode_id)
            );

            CREATE TABLE IF NOT EXISTS sample_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT NOT NULL,
                value TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_actions_episode ON actions(episode_id);
        """)
        conn.commit()

    def reset_database(self, database_id: str) -> None:
        """Reset database to clean state"""
        conn = self._get_connection(database_id)

        # Clear existing data
        conn.executescript("""
            DELETE FROM actions;
            DELETE FROM sessions;
            DELETE FROM sample_data;
        """)

        # Seed with sample data
        conn.execute(
            "INSERT INTO sample_data (key, value) VALUES (?, ?)",
            ("example_key", "example_value")
        )
        conn.commit()

        # Clear session cache
        if database_id in self._sessions:
            del self._sessions[database_id]

    def get_session(self, database_id: str) -> Session:
        """Get or create session for database_id"""
        if database_id not in self._sessions:
            episode_id = f"ep-{uuid.uuid4().hex[:8]}"
            created_at = datetime.utcnow().isoformat() + "Z"

            conn = self._get_connection(database_id)
            conn.execute(
                "INSERT INTO sessions (episode_id, database_id, step_count, created_at) VALUES (?, ?, ?, ?)",
                (episode_id, database_id, 0, created_at)
            )
            conn.commit()

            self._sessions[database_id] = Session(
                episode_id=episode_id,
                step_count=0,
                database_id=database_id,
                created_at=created_at
            )

        return self._sessions[database_id]

    def increment_step(self, database_id: str) -> int:
        """Increment step count for session"""
        session = self.get_session(database_id)
        session.step_count += 1

        conn = self._get_connection(database_id)
        conn.execute(
            "UPDATE sessions SET step_count = ? WHERE episode_id = ?",
            (session.step_count, session.episode_id)
        )
        conn.commit()

        return session.step_count

    def record_action(
        self,
        database_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        result: dict[str, Any]
    ) -> None:
        """Record an action in the database"""
        session = self.get_session(database_id)
        conn = self._get_connection(database_id)

        conn.execute(
            """
            INSERT INTO actions (episode_id, step_number, tool_name, arguments, result, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                session.episode_id,
                session.step_count,
                tool_name,
                json.dumps(arguments),
                json.dumps(result),
                datetime.utcnow().isoformat() + "Z"
            )
        )
        conn.commit()

    def execute_verify_query(self, database_id: str, query: str) -> list[dict]:
        """Execute a verification query and return results"""
        conn = self._get_connection(database_id)

        # Only allow SELECT queries for safety
        if not query.strip().upper().startswith("SELECT"):
            raise ValueError("Only SELECT queries are allowed for verification")

        cursor = conn.execute(query)
        columns = [desc[0] for desc in cursor.description]
        rows = cursor.fetchall()

        return [dict(zip(columns, row)) for row in rows]

    def get_actions(self, database_id: str) -> list[dict]:
        """Get all actions for a session"""
        session = self.get_session(database_id)
        conn = self._get_connection(database_id)

        cursor = conn.execute(
            "SELECT * FROM actions WHERE episode_id = ? ORDER BY step_number",
            (session.episode_id,)
        )

        return [dict(row) for row in cursor.fetchall()]

    def close_all(self) -> None:
        """Close all database connections"""
        for conn in self._connections.values():
            conn.close()
        self._connections.clear()
        self._sessions.clear()

    def cleanup_old_databases(self, max_age_hours: int = 24) -> tuple[int, list[str]]:
        """
        Delete database files older than max_age_hours.

        Args:
            max_age_hours: Maximum age in hours before a database is deleted

        Returns:
            Tuple of (count deleted, list of deleted filenames)
        """
        import time

        deleted = []
        db_dir = Path(self._db_path).parent
        base_db_name = Path(self._db_path).name
        cutoff = time.time() - (max_age_hours * 3600)

        for db_file in db_dir.glob("openenv_*.db"):
            # Skip base database
            if db_file.name == base_db_name:
                continue

            try:
                # Extract database_id from filename
                db_id = db_file.stem.replace("openenv_", "")

                # SAFETY: Skip if this database has an active connection
                # (means it's being used by a running evaluation)
                if db_id in self._connections or db_id in self._sessions:
                    continue

                if db_file.stat().st_mtime < cutoff:
                    db_file.unlink()
                    deleted.append(db_file.name)
            except OSError:
                continue  # Skip files we can't access

        return len(deleted), deleted

    def get_database_stats(self) -> dict:
        """
        Get statistics about database files.

        Returns:
            Dict with count, total_size_bytes, total_size_mb, files list
        """
        db_dir = Path(self._db_path).parent
        files = []

        for db_file in db_dir.glob("openenv_*.db"):
            try:
                stat = db_file.stat()
                files.append({
                    "name": db_file.name,
                    "size_bytes": stat.st_size,
                    "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat() + "Z"
                })
            except OSError:
                continue

        total_size = sum(f["size_bytes"] for f in files)

        return {
            "count": len(files),
            "total_size_bytes": total_size,
            "total_size_mb": round(total_size / 1024 / 1024, 2),
            "files": files
        }

    def get_database_path(self, database_id: str) -> Path | None:
        """
        Get the file path for a specific database_id.

        Args:
            database_id: The database identifier

        Returns:
            Path to the database file, or None if it doesn't exist
        """
        db_file = Path(self._db_path.replace(".db", f"_{database_id}.db"))
        if db_file.exists():
            return db_file
        return None

    def get_database_content(self, database_id: str) -> bytes | None:
        """
        Get the raw bytes of a database file.

        Flushes any pending writes before reading to ensure consistency.

        Args:
            database_id: The database identifier

        Returns:
            Database file bytes, or None if database doesn't exist
        """
        # Flush pending writes if connection exists
        if database_id in self._connections:
            self._connections[database_id].commit()

        db_path = self.get_database_path(database_id)
        if db_path and db_path.exists():
            return db_path.read_bytes()
        return None
