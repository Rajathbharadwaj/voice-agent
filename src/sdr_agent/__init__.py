"""
SDR Agent Package

AI-powered outbound sales calling system.

Components:
- scraper: Lead scraping from Google Maps, Yelp
- telephony: Twilio integration for calls
- agent: Sales agent using LangChain + Claude
- campaign: Campaign orchestration
- data: Database and CSV logging
- pipeline: Voice processing pipeline
"""

from .config import Config, load_config
from .cli import cli

__version__ = "0.1.0"

__all__ = [
    "Config",
    "load_config",
    "cli",
]
