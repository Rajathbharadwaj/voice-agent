"""
Business Hours Checker

Determines when businesses are likely to be open and available to receive calls.
"""

from datetime import datetime, time, timedelta
from typing import Optional, Tuple
from dataclasses import dataclass
from enum import Enum
import pytz


class DayOfWeek(Enum):
    """Days of the week."""
    MONDAY = 0
    TUESDAY = 1
    WEDNESDAY = 2
    THURSDAY = 3
    FRIDAY = 4
    SATURDAY = 5
    SUNDAY = 6


@dataclass
class TimeRange:
    """A range of time."""
    start: time
    end: time

    def contains(self, t: time) -> bool:
        """Check if time falls within range."""
        return self.start <= t <= self.end


@dataclass
class BusinessHours:
    """Business hours configuration."""
    weekday_hours: TimeRange  # Monday-Friday
    saturday_hours: Optional[TimeRange] = None
    sunday_hours: Optional[TimeRange] = None
    timezone: str = "America/Edmonton"  # Calgary timezone

    def is_open(self, dt: Optional[datetime] = None) -> bool:
        """Check if business is open at given time."""
        if dt is None:
            dt = datetime.now(pytz.timezone(self.timezone))
        elif dt.tzinfo is None:
            dt = pytz.timezone(self.timezone).localize(dt)

        day = dt.weekday()
        current_time = dt.time()

        if day < 5:  # Weekday
            return self.weekday_hours.contains(current_time)
        elif day == 5:  # Saturday
            if self.saturday_hours:
                return self.saturday_hours.contains(current_time)
            return False
        else:  # Sunday
            if self.sunday_hours:
                return self.sunday_hours.contains(current_time)
            return False

    def next_open_time(self, dt: Optional[datetime] = None) -> datetime:
        """Get the next time the business will be open."""
        tz = pytz.timezone(self.timezone)

        if dt is None:
            dt = datetime.now(tz)
        elif dt.tzinfo is None:
            dt = tz.localize(dt)

        # Check up to 7 days ahead
        for days_ahead in range(8):
            check_date = dt + timedelta(days=days_ahead)
            day = check_date.weekday()

            # Get hours for this day
            hours = None
            if day < 5:
                hours = self.weekday_hours
            elif day == 5:
                hours = self.saturday_hours
            else:
                hours = self.sunday_hours

            if hours is None:
                continue

            # If same day and still time left, check current time
            if days_ahead == 0:
                if check_date.time() < hours.start:
                    # Before opening - return opening time today
                    return datetime.combine(check_date.date(), hours.start, tzinfo=tz)
                elif check_date.time() <= hours.end:
                    # Currently open
                    return dt
            else:
                # Future day - return opening time
                return datetime.combine(check_date.date(), hours.start, tzinfo=tz)

        # Fallback - shouldn't reach here
        return dt + timedelta(days=1)


# Default business hours by category
DEFAULT_HOURS = BusinessHours(
    weekday_hours=TimeRange(time(9, 0), time(17, 0)),  # 9am-5pm
    saturday_hours=None,
    sunday_hours=None,
)

# Category-specific hours (Calgary businesses)
CATEGORY_HOURS = {
    # Healthcare - often have limited phone hours
    "dental_clinic": BusinessHours(
        weekday_hours=TimeRange(time(8, 0), time(17, 0)),
        saturday_hours=TimeRange(time(9, 0), time(14, 0)),  # Some open Sat morning
    ),
    "medical_clinic": BusinessHours(
        weekday_hours=TimeRange(time(8, 30), time(16, 30)),
        saturday_hours=None,
    ),
    "veterinary": BusinessHours(
        weekday_hours=TimeRange(time(8, 0), time(18, 0)),
        saturday_hours=TimeRange(time(9, 0), time(15, 0)),
    ),
    "pharmacy": BusinessHours(
        weekday_hours=TimeRange(time(9, 0), time(21, 0)),
        saturday_hours=TimeRange(time(9, 0), time(18, 0)),
        sunday_hours=TimeRange(time(10, 0), time(17, 0)),
    ),

    # Food & Hospitality - call before lunch/dinner rush
    "restaurant": BusinessHours(
        weekday_hours=TimeRange(time(10, 0), time(11, 30)),  # Before lunch
        saturday_hours=TimeRange(time(10, 0), time(11, 30)),
    ),
    "cafe": BusinessHours(
        weekday_hours=TimeRange(time(7, 0), time(10, 0)),  # Early morning
        saturday_hours=TimeRange(time(8, 0), time(10, 0)),
    ),
    "bar": BusinessHours(
        weekday_hours=TimeRange(time(14, 0), time(16, 0)),  # Afternoon before busy
    ),

    # Professional Services
    "law_firm": BusinessHours(
        weekday_hours=TimeRange(time(9, 0), time(17, 0)),
    ),
    "accounting": BusinessHours(
        weekday_hours=TimeRange(time(9, 0), time(17, 0)),
    ),
    "real_estate": BusinessHours(
        weekday_hours=TimeRange(time(9, 0), time(18, 0)),
        saturday_hours=TimeRange(time(10, 0), time(16, 0)),
    ),

    # Retail
    "retail": BusinessHours(
        weekday_hours=TimeRange(time(10, 0), time(12, 0)),  # Before busy
        saturday_hours=TimeRange(time(10, 0), time(12, 0)),
    ),
    "salon": BusinessHours(
        weekday_hours=TimeRange(time(9, 0), time(11, 0)),  # Before appointments
        saturday_hours=TimeRange(time(9, 0), time(10, 0)),
    ),
    "spa": BusinessHours(
        weekday_hours=TimeRange(time(9, 0), time(11, 0)),
    ),

    # Auto
    "auto_repair": BusinessHours(
        weekday_hours=TimeRange(time(8, 0), time(17, 0)),
        saturday_hours=TimeRange(time(9, 0), time(13, 0)),
    ),
    "car_dealership": BusinessHours(
        weekday_hours=TimeRange(time(9, 0), time(18, 0)),
        saturday_hours=TimeRange(time(10, 0), time(17, 0)),
    ),

    # Home Services
    "plumber": BusinessHours(
        weekday_hours=TimeRange(time(8, 0), time(17, 0)),
    ),
    "electrician": BusinessHours(
        weekday_hours=TimeRange(time(8, 0), time(17, 0)),
    ),
    "hvac": BusinessHours(
        weekday_hours=TimeRange(time(8, 0), time(17, 0)),
    ),
    "cleaning_service": BusinessHours(
        weekday_hours=TimeRange(time(8, 0), time(17, 0)),
    ),

    # Fitness
    "gym": BusinessHours(
        weekday_hours=TimeRange(time(9, 0), time(11, 0)),  # After morning rush
        saturday_hours=TimeRange(time(10, 0), time(12, 0)),
    ),
    "yoga_studio": BusinessHours(
        weekday_hours=TimeRange(time(10, 0), time(14, 0)),  # Between classes
    ),
}


class BusinessHoursChecker:
    """
    Checks if it's appropriate to call a business.

    Takes into account:
    - Time of day
    - Day of week
    - Business category (different categories have different optimal call times)
    - Timezone (Calgary = America/Edmonton)
    """

    def __init__(self, timezone: str = "America/Edmonton"):
        self.timezone = timezone
        self.tz = pytz.timezone(timezone)

    def get_hours_for_category(self, category: str) -> BusinessHours:
        """Get business hours for a category."""
        # Normalize category name
        cat_normalized = category.lower().replace(" ", "_").replace("-", "_")

        # Check for exact match
        if cat_normalized in CATEGORY_HOURS:
            return CATEGORY_HOURS[cat_normalized]

        # Check for partial match
        for key, hours in CATEGORY_HOURS.items():
            if key in cat_normalized or cat_normalized in key:
                return hours

        # Default hours
        return DEFAULT_HOURS

    def can_call_now(
        self,
        category: str,
        current_time: Optional[datetime] = None,
    ) -> Tuple[bool, str]:
        """
        Check if it's appropriate to call a business now.

        Args:
            category: Business category
            current_time: Time to check (default: now)

        Returns:
            Tuple of (can_call, reason)
        """
        hours = self.get_hours_for_category(category)

        if current_time is None:
            current_time = datetime.now(self.tz)
        elif current_time.tzinfo is None:
            current_time = self.tz.localize(current_time)

        if hours.is_open(current_time):
            return True, "Business is open"

        # Get next available time
        next_open = hours.next_open_time(current_time)
        wait_minutes = int((next_open - current_time).total_seconds() / 60)

        if wait_minutes < 60:
            return False, f"Opens in {wait_minutes} minutes"
        elif wait_minutes < 1440:  # Less than a day
            hours_wait = wait_minutes // 60
            return False, f"Opens in {hours_wait} hours"
        else:
            return False, f"Opens on {next_open.strftime('%A at %I:%M %p')}"

    def get_next_call_window(
        self,
        category: str,
        after: Optional[datetime] = None,
    ) -> datetime:
        """
        Get the next available calling window.

        Args:
            category: Business category
            after: Start searching after this time (default: now)

        Returns:
            Next datetime when calling is appropriate
        """
        hours = self.get_hours_for_category(category)

        if after is None:
            after = datetime.now(self.tz)
        elif after.tzinfo is None:
            after = self.tz.localize(after)

        return hours.next_open_time(after)

    def get_optimal_call_times(self, category: str) -> dict:
        """
        Get optimal calling times for a category.

        Returns dict with weekday and weekend info.
        """
        hours = self.get_hours_for_category(category)

        result = {
            "weekday": None,
            "saturday": None,
            "sunday": None,
            "best_time": None,
        }

        if hours.weekday_hours:
            result["weekday"] = {
                "start": hours.weekday_hours.start.strftime("%I:%M %p"),
                "end": hours.weekday_hours.end.strftime("%I:%M %p"),
            }
            result["best_time"] = hours.weekday_hours.start.strftime("%I:%M %p")

        if hours.saturday_hours:
            result["saturday"] = {
                "start": hours.saturday_hours.start.strftime("%I:%M %p"),
                "end": hours.saturday_hours.end.strftime("%I:%M %p"),
            }

        if hours.sunday_hours:
            result["sunday"] = {
                "start": hours.sunday_hours.start.strftime("%I:%M %p"),
                "end": hours.sunday_hours.end.strftime("%I:%M %p"),
            }

        return result


def should_call_lead(
    category: str,
    checker: Optional[BusinessHoursChecker] = None,
) -> Tuple[bool, str, Optional[datetime]]:
    """
    Convenience function to check if a lead should be called now.

    Args:
        category: Business category

    Returns:
        Tuple of (should_call, reason, next_available_time)
    """
    if checker is None:
        checker = BusinessHoursChecker()

    can_call, reason = checker.can_call_now(category)

    if can_call:
        return True, reason, None

    next_time = checker.get_next_call_window(category)
    return False, reason, next_time
