"""
SDR Agent Configuration

Load configuration from environment variables and .env file.
"""

import os
from pathlib import Path
from dataclasses import dataclass
from dotenv import load_dotenv

# Load .env file
load_dotenv()

# Project paths
PROJECT_ROOT = Path(__file__).parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"
EXPORTS_DIR = DATA_DIR / "exports"
DATABASE_PATH = DATA_DIR / "leads.db"

# Ensure directories exist
DATA_DIR.mkdir(exist_ok=True)
EXPORTS_DIR.mkdir(exist_ok=True)


@dataclass
class Config:
    """Application configuration."""

    # Twilio
    twilio_account_sid: str
    twilio_auth_token: str
    twilio_phone_number: str  # Your Twilio phone number

    # Anthropic
    anthropic_api_key: str

    # Server
    webhook_host: str = "localhost"
    webhook_port: int = 8080

    # Campaign defaults
    default_calls_per_hour: int = 20
    default_max_concurrent: int = 3
    default_max_retries: int = 2

    # Scraping
    scrape_delay_seconds: float = 2.0

    @classmethod
    def from_env(cls) -> "Config":
        """Load configuration from environment variables."""
        return cls(
            twilio_account_sid=os.getenv("TWILIO_ACCOUNT_SID", ""),
            twilio_auth_token=os.getenv("TWILIO_AUTH_TOKEN", ""),
            twilio_phone_number=os.getenv("TWILIO_PHONE_NUMBER", ""),
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
            webhook_host=os.getenv("WEBHOOK_HOST", "localhost"),
            webhook_port=int(os.getenv("WEBHOOK_PORT", "8080")),
            default_calls_per_hour=int(os.getenv("DEFAULT_CALLS_PER_HOUR", "20")),
            default_max_concurrent=int(os.getenv("DEFAULT_MAX_CONCURRENT", "3")),
            default_max_retries=int(os.getenv("DEFAULT_MAX_RETRIES", "2")),
            scrape_delay_seconds=float(os.getenv("SCRAPE_DELAY_SECONDS", "2.0")),
        )

    def validate(self) -> list[str]:
        """Validate required configuration. Returns list of missing fields."""
        missing = []
        if not self.twilio_account_sid:
            missing.append("TWILIO_ACCOUNT_SID")
        if not self.twilio_auth_token:
            missing.append("TWILIO_AUTH_TOKEN")
        if not self.twilio_phone_number:
            missing.append("TWILIO_PHONE_NUMBER")
        if not self.anthropic_api_key:
            missing.append("ANTHROPIC_API_KEY")
        return missing


def load_config() -> Config:
    """Load and return the application configuration."""
    return Config.from_env()
