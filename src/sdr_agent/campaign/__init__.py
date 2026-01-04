"""
Campaign Management Package

Orchestrates outbound calling campaigns.
"""

from .manager import CampaignManager, CampaignStats
from .business_hours import (
    BusinessHoursChecker,
    BusinessHours,
    should_call_lead,
    CATEGORY_HOURS,
)

__all__ = [
    "CampaignManager",
    "CampaignStats",
    "BusinessHoursChecker",
    "BusinessHours",
    "should_call_lead",
    "CATEGORY_HOURS",
]
