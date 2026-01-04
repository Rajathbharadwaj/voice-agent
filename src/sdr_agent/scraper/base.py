"""
Base Scraper

Abstract base class for lead scrapers.
"""

from abc import ABC, abstractmethod
from typing import Optional
import re

import phonenumbers
from phonenumbers import PhoneNumberFormat

from ..data.models import ScrapedLead


class BaseScraper(ABC):
    """Abstract base class for lead scrapers."""

    SOURCE_NAME = "unknown"

    def __init__(self, city: str = "Calgary", province: str = "AB"):
        self.city = city
        self.province = province

    @abstractmethod
    def scrape(self, category: str, limit: int = 50) -> list[ScrapedLead]:
        """
        Scrape leads for a category.

        Args:
            category: Business category to search (e.g., "dental clinics")
            limit: Maximum number of leads to scrape

        Returns:
            List of scraped leads
        """
        pass

    def normalize_phone(self, phone: Optional[str]) -> Optional[str]:
        """
        Normalize phone number to E.164 format.

        Args:
            phone: Raw phone number string

        Returns:
            Phone number in E.164 format (+14035551234) or None if invalid
        """
        if not phone:
            return None

        # Clean the phone string
        phone = phone.strip()

        # Remove common formatting
        phone = re.sub(r'[^\d+]', '', phone)

        try:
            # Parse with CA region default
            parsed = phonenumbers.parse(phone, "CA")

            if phonenumbers.is_valid_number(parsed):
                return phonenumbers.format_number(parsed, PhoneNumberFormat.E164)
        except phonenumbers.NumberParseException:
            pass

        return None

    def normalize_category(self, category: str) -> str:
        """
        Normalize category name for database storage.

        Args:
            category: Raw category name

        Returns:
            Normalized category (lowercase, underscores)
        """
        # Convert to lowercase and replace spaces with underscores
        normalized = category.lower().strip()
        normalized = re.sub(r'\s+', '_', normalized)
        normalized = re.sub(r'[^a-z0-9_]', '', normalized)
        return normalized

    def clean_business_name(self, name: str) -> str:
        """Clean up business name."""
        # Remove extra whitespace
        name = ' '.join(name.split())
        # Remove common suffixes that add noise
        suffixes = [' - Yelp', ' | Yelp', ' - Google Maps']
        for suffix in suffixes:
            if name.endswith(suffix):
                name = name[:-len(suffix)]
        return name.strip()
