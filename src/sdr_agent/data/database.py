"""Minimal stub database module for testing."""
import sqlite3
from pathlib import Path
from contextlib import contextmanager

DB_PATH = Path("/tmp/voice_agent_test.db")

def init_database():
    """Initialize database."""
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS leads (
                id INTEGER PRIMARY KEY,
                name TEXT,
                phone TEXT,
                business_name TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS calls (
                id INTEGER PRIMARY KEY,
                lead_id INTEGER,
                status TEXT,
                recording_url TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS campaigns (
                id INTEGER PRIMARY KEY,
                name TEXT,
                status TEXT
            )
        """)

@contextmanager
def get_connection():
    conn = sqlite3.connect(str(DB_PATH))
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


class LeadRepository:
    @staticmethod
    def get_all(): return []
    @staticmethod
    def get_by_id(id): return None
    @staticmethod
    def create(**kwargs): return 1
    @staticmethod
    def update(id, **kwargs): pass


class CallRepository:
    @staticmethod
    def get_all(): return []
    @staticmethod
    def get_by_id(id): return None
    @staticmethod
    def create(**kwargs): return 1
    @staticmethod
    def update(id, **kwargs): pass
    @staticmethod
    def update_status(call_sid, status): pass
    @staticmethod
    def get_by_lead_id(lead_id): return []


class CampaignRepository:
    @staticmethod
    def get_all(): return []
    @staticmethod
    def get_by_id(id): return None
    @staticmethod
    def create(**kwargs): return 1
    @staticmethod
    def update(id, **kwargs): pass
