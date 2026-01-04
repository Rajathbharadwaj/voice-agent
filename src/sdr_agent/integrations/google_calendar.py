"""
Google Calendar Integration

Creates calendar events and sends email invites.
"""

import os
import pickle
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# Scopes required for calendar access
SCOPES = ['https://www.googleapis.com/auth/calendar']

# Path for storing credentials
CREDENTIALS_DIR = Path(__file__).parent.parent.parent.parent / "data"
TOKEN_PATH = CREDENTIALS_DIR / "google_token.pickle"
CREDENTIALS_PATH = CREDENTIALS_DIR / "google_credentials.json"


class GoogleCalendarService:
    """Service for creating Google Calendar events."""

    def __init__(self):
        self._service = None
        self._credentials = None

    def _get_credentials(self) -> Optional[Credentials]:
        """Get or refresh Google API credentials."""
        creds = None

        # Load existing token
        if TOKEN_PATH.exists():
            with open(TOKEN_PATH, 'rb') as token:
                creds = pickle.load(token)

        # Refresh or get new credentials
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                print(f"[Calendar] Token refresh failed: {e}")
                creds = None

        if not creds or not creds.valid:
            if not CREDENTIALS_PATH.exists():
                print(f"[Calendar] No credentials file found at {CREDENTIALS_PATH}")
                print("[Calendar] Please download OAuth2 credentials from Google Cloud Console")
                return None

            flow = InstalledAppFlow.from_client_secrets_file(
                str(CREDENTIALS_PATH), SCOPES
            )
            # Use console flow for headless environments
            creds = flow.run_local_server(port=8090, open_browser=False)

            # Save for future use
            with open(TOKEN_PATH, 'wb') as token:
                pickle.dump(creds, token)
            print("[Calendar] New credentials saved")

        return creds

    def _get_service(self):
        """Get or create the Calendar API service."""
        if self._service is None:
            creds = self._get_credentials()
            if creds:
                self._service = build('calendar', 'v3', credentials=creds)
        return self._service

    def create_meeting(
        self,
        title: str,
        start_time: datetime,
        duration_minutes: int = 15,
        attendee_email: str = None,
        attendee_name: str = None,
        description: str = None,
    ) -> Optional[str]:
        """
        Create a calendar event and send invite.

        Args:
            title: Event title
            start_time: Start time for the meeting
            duration_minutes: Duration in minutes (default 15)
            attendee_email: Email to invite
            attendee_name: Name of attendee
            description: Event description

        Returns:
            Event ID if successful, None otherwise
        """
        service = self._get_service()
        if not service:
            print("[Calendar] Service not available - check credentials")
            return None

        # Ensure timezone-aware datetimes for Google Calendar API
        local_tz = ZoneInfo('America/Edmonton')
        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=local_tz)
        end_time = start_time + timedelta(minutes=duration_minutes)

        # Build event
        event = {
            'summary': title,
            'description': description or f"Parallel Universe Demo with {attendee_name or 'Prospect'}",
            'start': {
                'dateTime': start_time.isoformat(),
                'timeZone': 'America/Edmonton',  # Calgary timezone
            },
            'end': {
                'dateTime': end_time.isoformat(),
                'timeZone': 'America/Edmonton',
            },
            'reminders': {
                'useDefault': False,
                'overrides': [
                    {'method': 'email', 'minutes': 60},
                    {'method': 'popup', 'minutes': 15},
                ],
            },
        }

        # Add attendee if email provided
        if attendee_email:
            event['attendees'] = [
                {'email': attendee_email, 'displayName': attendee_name or ''}
            ]

        try:
            created_event = service.events().insert(
                calendarId='primary',
                body=event,
                sendUpdates='all'  # Send email invites
            ).execute()

            event_id = created_event.get('id')
            event_link = created_event.get('htmlLink')
            print(f"[Calendar] Event created: {event_link}")
            return event_id

        except Exception as e:
            print(f"[Calendar] Error creating event: {e}")
            return None

    def is_authenticated(self) -> bool:
        """Check if we have valid credentials."""
        creds = self._get_credentials()
        return creds is not None and creds.valid

    def get_available_slots(
        self,
        date: datetime,
        slot_duration_minutes: int = 15,
        start_hour: int = 9,
        end_hour: int = 17,
    ) -> list[datetime]:
        """
        Get available time slots for a given date.

        Args:
            date: The date to check
            slot_duration_minutes: Duration of each slot
            start_hour: Start of business hours (default 9am)
            end_hour: End of business hours (default 5pm)

        Returns:
            List of available start times
        """
        service = self._get_service()
        if not service:
            return []

        # Set time range for the day (in local timezone)
        local_tz = ZoneInfo('America/Edmonton')
        day_start = date.replace(hour=start_hour, minute=0, second=0, microsecond=0, tzinfo=local_tz)
        day_end = date.replace(hour=end_hour, minute=0, second=0, microsecond=0, tzinfo=local_tz)

        # Get existing events for the day
        try:
            # Use RFC3339 format with timezone for Google Calendar API
            events_result = service.events().list(
                calendarId='primary',
                timeMin=day_start.isoformat(),
                timeMax=day_end.isoformat(),
                singleEvents=True,
                orderBy='startTime',
                timeZone='America/Edmonton',  # Interpret times in this timezone
            ).execute()
            events = events_result.get('items', [])
            print(f"[Calendar] Checking {date.strftime('%Y-%m-%d')} {start_hour}:00-{end_hour}:00, found {len(events)} events")
        except Exception as e:
            print(f"[Calendar] Error fetching events: {e}")
            return []

        # Build list of busy times (as naive datetimes in local time)
        busy_times = []
        for event in events:
            start = event['start'].get('dateTime', event['start'].get('date'))
            end = event['end'].get('dateTime', event['end'].get('date'))
            summary = event.get('summary', 'Untitled')
            if 'T' in start:  # It's a datetime, not all-day
                # The API returns times in the timezone we requested (America/Edmonton)
                # Parse the ISO format - handle both Z and offset formats
                try:
                    if start.endswith('Z'):
                        start_dt = datetime.fromisoformat(start.replace('Z', '+00:00'))
                    else:
                        start_dt = datetime.fromisoformat(start)
                    if end.endswith('Z'):
                        end_dt = datetime.fromisoformat(end.replace('Z', '+00:00'))
                    else:
                        end_dt = datetime.fromisoformat(end)

                    # Create timezone-aware datetimes for comparison
                    busy_start = datetime(start_dt.year, start_dt.month, start_dt.day, start_dt.hour, start_dt.minute, tzinfo=local_tz)
                    busy_end = datetime(end_dt.year, end_dt.month, end_dt.day, end_dt.hour, end_dt.minute, tzinfo=local_tz)

                    busy_times.append((busy_start, busy_end))
                    print(f"[Calendar] Busy: {busy_start.strftime('%H:%M')}-{busy_end.strftime('%H:%M')} ({summary})")
                except Exception as e:
                    print(f"[Calendar] Error parsing event time: {e}")

        # Generate available slots
        available = []
        current = day_start
        slot_delta = timedelta(minutes=slot_duration_minutes)

        while current + slot_delta <= day_end:
            slot_end = current + slot_delta
            is_free = True

            for busy_start, busy_end in busy_times:
                # Check for overlap - slot overlaps if NOT (slot ends before busy starts OR slot starts after busy ends)
                if not (slot_end <= busy_start or current >= busy_end):
                    is_free = False
                    print(f"[Calendar] Slot {current.strftime('%H:%M')} conflicts with {busy_start.strftime('%H:%M')}-{busy_end.strftime('%H:%M')}")
                    break

            if is_free:
                available.append(current)

            current += slot_delta

        print(f"[Calendar] {len(available)} available slots")
        return available

    def get_availability_info(
        self,
        date: datetime,
        slot_duration_minutes: int = 15,
        start_hour: int = 9,
        end_hour: int = 17,
    ) -> dict:
        """
        Get both available slots and busy periods for a given date.

        Returns:
            Dict with 'available' (list of datetimes) and 'busy' (list of dicts with start, end, title)
        """
        service = self._get_service()
        if not service:
            return {"available": [], "busy": []}

        local_tz = ZoneInfo('America/Edmonton')
        day_start = date.replace(hour=start_hour, minute=0, second=0, microsecond=0, tzinfo=local_tz)
        day_end = date.replace(hour=end_hour, minute=0, second=0, microsecond=0, tzinfo=local_tz)

        try:
            events_result = service.events().list(
                calendarId='primary',
                timeMin=day_start.isoformat(),
                timeMax=day_end.isoformat(),
                singleEvents=True,
                orderBy='startTime',
                timeZone='America/Edmonton',
            ).execute()
            events = events_result.get('items', [])
        except Exception as e:
            print(f"[Calendar] Error fetching events: {e}")
            return {"available": [], "busy": []}

        # Build busy times list
        busy_times = []
        busy_info = []
        for event in events:
            start = event['start'].get('dateTime', event['start'].get('date'))
            end = event['end'].get('dateTime', event['end'].get('date'))
            summary = event.get('summary', 'Busy')

            if 'T' in start:
                try:
                    if start.endswith('Z'):
                        start_dt = datetime.fromisoformat(start.replace('Z', '+00:00'))
                    else:
                        start_dt = datetime.fromisoformat(start)
                    if end.endswith('Z'):
                        end_dt = datetime.fromisoformat(end.replace('Z', '+00:00'))
                    else:
                        end_dt = datetime.fromisoformat(end)

                    busy_start = datetime(start_dt.year, start_dt.month, start_dt.day, start_dt.hour, start_dt.minute, tzinfo=local_tz)
                    busy_end = datetime(end_dt.year, end_dt.month, end_dt.day, end_dt.hour, end_dt.minute, tzinfo=local_tz)

                    busy_times.append((busy_start, busy_end))
                    busy_info.append({
                        "start": busy_start.strftime("%I:%M %p").lstrip("0"),
                        "end": busy_end.strftime("%I:%M %p").lstrip("0"),
                        "title": summary
                    })
                except Exception:
                    pass

        # Generate available slots
        available = []
        current = day_start
        slot_delta = timedelta(minutes=slot_duration_minutes)

        while current + slot_delta <= day_end:
            slot_end = current + slot_delta
            is_free = True

            for busy_start, busy_end in busy_times:
                if not (slot_end <= busy_start or current >= busy_end):
                    is_free = False
                    break

            if is_free:
                available.append(current)

            current += slot_delta

        return {"available": available, "busy": busy_info}

    def get_next_available_slots(self, num_slots: int = 5) -> list[tuple[datetime, str]]:
        """
        Get the next available slots across upcoming days.

        Returns:
            List of (datetime, formatted_string) tuples
        """
        from datetime import date as date_type

        slots = []
        current_date = datetime.now()

        # Check next 7 days
        for day_offset in range(7):
            check_date = current_date + timedelta(days=day_offset)

            # Skip weekends
            if check_date.weekday() >= 5:
                continue

            # For today, start from next hour
            if day_offset == 0:
                start_hour = max(9, current_date.hour + 1)
            else:
                start_hour = 9

            day_slots = self.get_available_slots(
                check_date,
                slot_duration_minutes=15,
                start_hour=start_hour,
                end_hour=17
            )

            for slot in day_slots:
                day_name = slot.strftime("%A")
                time_str = slot.strftime("%I:%M %p").lstrip("0")
                formatted = f"{day_name} at {time_str}"
                slots.append((slot, formatted))

                if len(slots) >= num_slots:
                    return slots

        return slots


# Global instance
_calendar_service: Optional[GoogleCalendarService] = None


def get_calendar_service() -> GoogleCalendarService:
    """Get or create the global calendar service."""
    global _calendar_service
    if _calendar_service is None:
        _calendar_service = GoogleCalendarService()
    return _calendar_service
