"""
Thread Mapping Service

Maps external identifiers (phone numbers) to LangGraph thread_ids.
Uses SQLite for development, PostgreSQL for production.
"""

import uuid
import json
import sqlite3
from datetime import datetime
from typing import Optional, Dict, Any
from pathlib import Path


class ThreadMappingService:
    """
    Service to map external identifiers (phone numbers) to LangGraph thread_ids.

    Thread IDs are UUIDs for LangGraph. This service maintains the mapping
    between external identifiers (like phone numbers) and these thread IDs.
    """

    def __init__(self, db_path: str = "data/thread_mappings.db"):
        """
        Initialize the thread mapping service.

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._setup_db()

    def _setup_db(self):
        """Create the mapping table if it doesn't exist."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS thread_mappings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    external_id TEXT NOT NULL,
                    external_type TEXT NOT NULL DEFAULT 'phone',
                    thread_id TEXT NOT NULL UNIQUE,
                    call_sid TEXT,
                    user_name TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    metadata TEXT DEFAULT '{}',
                    is_active INTEGER DEFAULT 1,

                    UNIQUE(external_id, external_type, is_active)
                )
            """)

            # Indexes for fast lookups
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_external
                ON thread_mappings(external_id, external_type, is_active)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_thread_id
                ON thread_mappings(thread_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_call_sid
                ON thread_mappings(call_sid) WHERE call_sid IS NOT NULL
            """)
            conn.commit()

    def get_or_create_thread(
        self,
        external_id: str,
        external_type: str = "phone",
        call_sid: Optional[str] = None,
        user_name: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Get existing thread_id for an external identifier, or create a new one.

        Args:
            external_id: The external identifier (e.g., phone number "+1234567890")
            external_type: Type of identifier ("phone", "email", "session", etc.)
            call_sid: Optional Twilio call SID
            user_name: Optional user/contact name
            metadata: Optional metadata to store

        Returns:
            thread_id: The LangGraph thread_id to use
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            # Try to get existing active mapping
            cursor = conn.execute("""
                SELECT thread_id FROM thread_mappings
                WHERE external_id = ? AND external_type = ? AND is_active = 1
            """, (external_id, external_type))

            existing = cursor.fetchone()

            if existing:
                # Update last accessed time and optional fields
                conn.execute("""
                    UPDATE thread_mappings
                    SET updated_at = ?,
                        call_sid = COALESCE(?, call_sid),
                        user_name = COALESCE(?, user_name)
                    WHERE external_id = ? AND external_type = ? AND is_active = 1
                """, (
                    datetime.utcnow().isoformat(),
                    call_sid,
                    user_name,
                    external_id,
                    external_type
                ))
                conn.commit()
                return existing["thread_id"]

            # Create new thread_id
            thread_id = str(uuid.uuid4())

            conn.execute("""
                INSERT INTO thread_mappings
                (external_id, external_type, thread_id, call_sid, user_name, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                external_id,
                external_type,
                thread_id,
                call_sid,
                user_name,
                json.dumps(metadata or {})
            ))
            conn.commit()

            print(f"[ThreadMapping] Created new thread {thread_id} for {external_type}:{external_id}")
            return thread_id

    def get_thread_by_external_id(
        self,
        external_id: str,
        external_type: str = "phone"
    ) -> Optional[str]:
        """Get thread_id for an external identifier if it exists."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT thread_id FROM thread_mappings
                WHERE external_id = ? AND external_type = ? AND is_active = 1
            """, (external_id, external_type))

            result = cursor.fetchone()
            return result[0] if result else None

    def get_thread_by_call_sid(self, call_sid: str) -> Optional[str]:
        """Get thread_id by Twilio call SID."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT thread_id FROM thread_mappings
                WHERE call_sid = ? AND is_active = 1
            """, (call_sid,))

            result = cursor.fetchone()
            return result[0] if result else None

    def get_mapping_by_thread(self, thread_id: str) -> Optional[Dict[str, Any]]:
        """Reverse lookup: get full mapping data from thread_id."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT external_id, external_type, call_sid, user_name, metadata, created_at, updated_at
                FROM thread_mappings
                WHERE thread_id = ? AND is_active = 1
            """, (thread_id,))

            result = cursor.fetchone()
            if result:
                return {
                    "external_id": result["external_id"],
                    "external_type": result["external_type"],
                    "call_sid": result["call_sid"],
                    "user_name": result["user_name"],
                    "metadata": json.loads(result["metadata"]) if result["metadata"] else {},
                    "created_at": result["created_at"],
                    "updated_at": result["updated_at"],
                }
            return None

    def update_call_sid(self, thread_id: str, call_sid: str):
        """Update the call SID for a thread."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                UPDATE thread_mappings
                SET call_sid = ?, updated_at = ?
                WHERE thread_id = ? AND is_active = 1
            """, (call_sid, datetime.utcnow().isoformat(), thread_id))
            conn.commit()

    def update_metadata(self, thread_id: str, metadata: Dict[str, Any]):
        """Update metadata for a thread (merges with existing)."""
        with sqlite3.connect(self.db_path) as conn:
            # Get existing metadata
            cursor = conn.execute("""
                SELECT metadata FROM thread_mappings
                WHERE thread_id = ? AND is_active = 1
            """, (thread_id,))

            result = cursor.fetchone()
            if result:
                existing = json.loads(result[0]) if result[0] else {}
                existing.update(metadata)

                conn.execute("""
                    UPDATE thread_mappings
                    SET metadata = ?, updated_at = ?
                    WHERE thread_id = ? AND is_active = 1
                """, (json.dumps(existing), datetime.utcnow().isoformat(), thread_id))
                conn.commit()

    def deactivate_thread(
        self,
        external_id: str,
        external_type: str = "phone"
    ) -> bool:
        """Deactivate a thread mapping (soft delete)."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                UPDATE thread_mappings
                SET is_active = 0, updated_at = ?
                WHERE external_id = ? AND external_type = ? AND is_active = 1
            """, (datetime.utcnow().isoformat(), external_id, external_type))
            conn.commit()
            return cursor.rowcount > 0

    def create_new_thread_for_external(
        self,
        external_id: str,
        external_type: str = "phone",
        call_sid: Optional[str] = None,
        user_name: Optional[str] = None
    ) -> str:
        """
        Force create a new thread for an external ID.
        Deactivates old thread and creates new one.
        """
        self.deactivate_thread(external_id, external_type)
        return self.get_or_create_thread(external_id, external_type, call_sid, user_name)


# Global instance
_mapping_service: Optional[ThreadMappingService] = None


def get_thread_mapping_service() -> ThreadMappingService:
    """Get or create the global thread mapping service."""
    global _mapping_service
    if _mapping_service is None:
        _mapping_service = ThreadMappingService()
    return _mapping_service
