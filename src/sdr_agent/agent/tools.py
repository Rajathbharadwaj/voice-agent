"""
Sales Agent Tools

Tools available to the sales agent during calls.
"""

import sys
import importlib.util
from datetime import datetime, timedelta
from typing import Optional
from dataclasses import dataclass, field
from pathlib import Path

from langchain_core.tools import tool


# Helper to load modules directly (avoids package import issues in LangGraph)
def _load_module_direct(module_name: str, file_path: Path):
    """Load a Python module directly from file path."""
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


class MockCalendarService:
    """Mock calendar service for testing when Google Calendar is unavailable."""

    def get_available_slots(self, date):
        """Return mock available slots."""
        return [
            date.replace(hour=9, minute=0),
            date.replace(hour=10, minute=30),
            date.replace(hour=14, minute=0),
            date.replace(hour=15, minute=30),
        ]

    def get_availability_info(self, date, **kwargs):
        """Return mock availability info with both available and busy."""
        return {
            "available": self.get_available_slots(date),
            "busy": [{"start": "12:00 PM", "end": "1:00 PM", "title": "Lunch"}]
        }

    def create_meeting(self, **kwargs):
        """Mock meeting creation."""
        print(f"[MockCalendar] Would create meeting: {kwargs}")
        return "mock_event_123"


def _get_calendar_service():
    """Get Google Calendar service, loading module directly if needed."""
    import os

    # Use mock if MOCK_CALENDAR is explicitly true
    if os.environ.get("MOCK_CALENDAR", "").lower() in ("true", "1", "yes"):
        print("[Tools] Using mock calendar service")
        return MockCalendarService()

    try:
        from ..integrations.google_calendar import get_calendar_service
        return get_calendar_service()
    except ImportError:
        try:
            tools_dir = Path(__file__).parent
            integrations_dir = tools_dir.parent / "integrations"
            calendar_module = _load_module_direct(
                "_google_calendar",
                integrations_dir / "google_calendar.py"
            )
            return calendar_module.get_calendar_service()
        except Exception as e:
            print(f"[Tools] Calendar unavailable, using mock: {e}")
            return MockCalendarService()


def _get_config():
    """Load config, handling import issues."""
    try:
        from ..config import load_config
        return load_config()
    except ImportError:
        tools_dir = Path(__file__).parent
        config_module = _load_module_direct(
            "_config",
            tools_dir.parent / "config.py"
        )
        return config_module.load_config()


def _get_twilio_client():
    """Get Twilio client, handling import issues."""
    try:
        from ..telephony.twilio_client import TwilioClient
        return TwilioClient
    except ImportError:
        tools_dir = Path(__file__).parent
        telephony_dir = tools_dir.parent / "telephony"
        twilio_module = _load_module_direct(
            "_twilio_client",
            telephony_dir / "twilio_client.py"
        )
        return twilio_module.TwilioClient


def _create_pending_booking(*args, **kwargs):
    """Create pending booking, handling import issues."""
    try:
        from ..booking_form import create_pending_booking
        return create_pending_booking(*args, **kwargs)
    except ImportError:
        tools_dir = Path(__file__).parent
        booking_module = _load_module_direct(
            "_booking_form",
            tools_dir.parent / "booking_form.py"
        )
        return booking_module.create_pending_booking(*args, **kwargs)


@dataclass
class CallContext:
    """Context for the current call."""
    call_id: str
    lead_id: str
    campaign_id: str
    business_name: str
    phone_number: str
    call_sid: Optional[str] = None  # Twilio call SID
    owner_name: Optional[str] = None  # Lead's owner/decision-maker name

    # Collected during call
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None
    meeting_time: Optional[datetime] = None
    callback_time: Optional[datetime] = None

    # Outcome
    outcome: Optional[str] = None
    ended: bool = False
    notes: list[str] = field(default_factory=list)


# Global call context - set per call
_current_context: Optional[CallContext] = None


def set_call_context(context: CallContext):
    """Set the context for the current call."""
    global _current_context
    _current_context = context


def get_call_context() -> Optional[CallContext]:
    """Get the current call context."""
    return _current_context


def clear_call_context():
    """Clear the call context."""
    global _current_context
    _current_context = None


@tool
def check_availability(day: str = "tomorrow") -> str:
    """
    Check calendar availability for a specific day.

    Call this BEFORE offering meeting times to know what slots are free.

    Args:
        day: The day to check (e.g., "today", "tomorrow", "Monday")

    Returns:
        Available time slots for that day
    """
    try:
        calendar = _get_calendar_service()

        # Parse the day
        now = datetime.now()
        day_lower = day.lower()

        if day_lower == "today":
            check_date = now
        elif day_lower == "tomorrow":
            check_date = now + timedelta(days=1)
        elif day_lower in ["monday", "tuesday", "wednesday", "thursday", "friday"]:
            days_map = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4}
            target_day = days_map[day_lower]
            current_day = now.weekday()
            days_ahead = target_day - current_day
            if days_ahead <= 0:
                days_ahead += 7
            check_date = now + timedelta(days=days_ahead)
        else:
            check_date = now + timedelta(days=1)

        # Get availability info (both available and busy)
        info = calendar.get_availability_info(check_date)
        slots = info.get("available", [])
        busy = info.get("busy", [])

        day_name = check_date.strftime("%A, %B %d")

        if not slots:
            if busy:
                busy_strs = [f"{b['start']}-{b['end']}" for b in busy]
                return f"No available slots on {day_name}. Already booked: {', '.join(busy_strs)}. Try another day."
            return f"No available slots on {day_name}. Try another day."

        # Format available slots (show up to 6)
        slot_strs = []
        for slot in slots[:6]:
            slot_strs.append(slot.strftime("%I:%M %p").lstrip("0"))

        # Build response with both available and busy
        response = f"CALENDAR FOR {day_name}:\n"

        if busy:
            busy_strs = [f"{b['start']}-{b['end']} ({b['title']})" for b in busy]
            response += f"BUSY: {', '.join(busy_strs)}\n"
        else:
            response += "BUSY: Nothing scheduled\n"

        response += f"AVAILABLE: {', '.join(slot_strs)}"
        if len(slots) > 6:
            response += f" (and {len(slots) - 6} more slots)"

        return response

    except Exception as e:
        print(f"[Tools] Availability check error: {e}")
        return "I can check availability - what day works best for you?"


@tool
def book_meeting(
    day: str,
    time: str,
    contact_name: str,
    contact_email: str,
) -> str:
    """
    Book a discovery meeting with the prospect.

    Call this when the prospect agrees to a demo/meeting.

    Args:
        day: The day for the meeting (e.g., "Monday", "tomorrow", "January 15")
        time: The time for the meeting (e.g., "2pm", "10:30am", "14:00")
        contact_name: The name of the person to meet with
        contact_email: Email address for sending calendar invite

    Returns:
        Confirmation message
    """
    context = get_call_context()
    if not context:
        return "Error: No active call context"

    # Parse the datetime
    meeting_dt = _parse_meeting_time(day, time)

    context.contact_name = contact_name
    context.contact_email = contact_email
    context.meeting_time = meeting_dt
    context.outcome = "meeting_booked"
    context.notes.append(f"Meeting booked: {day} at {time} with {contact_name} ({contact_email})")

    # Create Google Calendar event
    try:
        calendar = _get_calendar_service()
        event_id = calendar.create_meeting(
            title=f"Voice AI Demo - {contact_name}",
            start_time=meeting_dt,
            duration_minutes=15,
            attendee_email=contact_email,
            attendee_name=contact_name,
            description=f"Discovery call with {contact_name} from {context.business_name}.\n\nBooked via AI SDR.",
        )
        if event_id:
            context.notes.append(f"Calendar event created: {event_id}")
    except Exception as e:
        print(f"[Tools] Calendar error: {e}")

    # Send SMS confirmation
    try:
        config = _get_config()
        TwilioClient = _get_twilio_client()
        twilio = TwilioClient(config)

        # Format time nicely
        time_str = meeting_dt.strftime("%A, %B %d at %I:%M %p")
        sms_message = (
            f"Hey {contact_name}! 🎉 Great news - your demo is confirmed for {time_str}!\n\n"
            f"A calendar invite is on its way to {contact_email}.\n\n"
            f"📱 Quick heads up: If you're on iPhone, check your Spam or Promotions folder if you don't see it right away!\n\n"
            f"Really looking forward to chatting with you! - Alex from Parallel Universe"
        )
        twilio.send_sms(context.phone_number, sms_message)
        context.notes.append("SMS confirmation sent")
    except Exception as e:
        print(f"[Tools] SMS error: {e}")

    return f"Meeting successfully booked for {day} at {time}. Calendar invite will be sent to {contact_email}."


@tool
def request_callback(
    day: str,
    time: str,
    reason: Optional[str] = None,
) -> str:
    """
    Schedule a callback when the prospect asks to be called back later.

    Call this when they say things like "call me back tomorrow" or "I'm busy now".

    Args:
        day: The day for the callback (e.g., "tomorrow", "Monday")
        time: The time for the callback (e.g., "2pm", "morning", "afternoon")
        reason: Optional reason for callback

    Returns:
        Confirmation message
    """
    context = get_call_context()
    if not context:
        return "Error: No active call context"

    callback_dt = _parse_meeting_time(day, time)

    context.callback_time = callback_dt
    context.outcome = "callback_requested"
    context.notes.append(f"Callback requested: {day} at {time}" + (f" - {reason}" if reason else ""))

    return f"Callback scheduled for {day} at {time}."


@tool
def end_call(
    outcome: str,
    notes: Optional[str] = None,
) -> str:
    """
    End the current call and record the outcome.

    Call this when the conversation is ending, whether successful or not.

    Args:
        outcome: The call outcome. Must be one of:
            - "meeting_booked": Successfully scheduled a demo
            - "interested": Showed interest but didn't book
            - "callback_requested": Asked to be called back
            - "not_interested": Politely declined
            - "wrong_number": Not the intended business
            - "gatekeeper": Couldn't reach decision maker
            - "voicemail": Left a voicemail
            - "hostile": Negative reaction, do not call again

        notes: Optional notes about the call

    Returns:
        Confirmation that the call has ended
    """
    context = get_call_context()
    if not context:
        return "Error: No active call context"

    valid_outcomes = [
        "meeting_booked", "interested", "callback_requested",
        "not_interested", "wrong_number", "gatekeeper",
        "voicemail", "hostile"
    ]

    if outcome not in valid_outcomes:
        outcome = "not_interested"  # Default

    context.outcome = outcome
    context.ended = True
    if notes:
        context.notes.append(notes)

    return f"Call ended with outcome: {outcome}"


@tool
def send_booking_link(
    day: str,
    time: str,
    contact_name: str,
) -> str:
    """
    Send a booking link via SMS instead of collecting email over the phone.

    USE THIS instead of book_meeting when you need to collect email.
    It's much faster and avoids spelling errors.

    The prospect will receive an SMS with a link to a quick form where
    they enter their name and email. Once submitted, the calendar
    invite is automatically created and sent.

    Args:
        day: The day for the meeting (e.g., "Monday", "tomorrow")
        time: The time for the meeting (e.g., "10am", "2pm")
        contact_name: The prospect's first name (for personalization)

    Returns:
        Confirmation that the link was sent
    """
    import os
    import httpx

    context = get_call_context()
    if not context:
        return "Error: No active call context"

    # Parse the datetime
    meeting_dt = _parse_meeting_time(day, time)

    # CUA API endpoint for creating bookings
    cua_base_url = os.environ.get("CUA_API_URL", "https://app.paralleluniverse.ai")

    # Build webhook URL for receiving form submission notifications
    ngrok_url = os.environ.get("NGROK_URL", "")
    webhook_url = f"{ngrok_url}/webhook/booking" if ngrok_url else ""

    try:
        # Call CUA API to create pending booking
        with httpx.Client(timeout=10.0) as client:
            response = client.post(
                f"{cua_base_url}/booking/api/create",
                json={
                    "call_session_id": context.call_sid or "",
                    "webhook_url": webhook_url,
                    "phone_number": context.phone_number,
                    "proposed_datetime": meeting_dt.isoformat(),
                },
            )

            if response.status_code == 200:
                data = response.json()
                booking_url = data["url"]
                booking_id = data["booking_id"]
                print(f"[Tools] Created CUA booking: {booking_id} -> {booking_url}")
            else:
                print(f"[Tools] CUA API error: {response.status_code} - {response.text}")
                # Fallback to local booking
                return _send_booking_link_local(day, time, contact_name, context, meeting_dt)

    except Exception as e:
        print(f"[Tools] CUA API error: {e}")
        # Fallback to local booking
        return _send_booking_link_local(day, time, contact_name, context, meeting_dt)

    # Send SMS with booking link
    try:
        config = _get_config()
        TwilioClient = _get_twilio_client()
        twilio = TwilioClient(config)

        time_str = meeting_dt.strftime("%A at %I:%M %p").replace(" 0", " ")
        sms_message = (
            f"Hey {contact_name}! 👋 Here's your booking link for our demo on {time_str}: {booking_url}\n\n"
            f"Just pop in your email and you're all set! Takes 10 seconds.\n\n"
            f"📱 Heads up: If you're on iPhone, this might land in your Spam or Promotions folder - just check there if you don't see it right away!\n\n"
            f"Looking forward to chatting! - Alex from Parallel Universe"
        )

        twilio.send_sms(context.phone_number, sms_message)
        context.notes.append(f"Booking link sent: {booking_id}")
        context.outcome = "meeting_booked"

    except Exception as e:
        print(f"[Tools] SMS error: {e}")
        return "I had trouble sending the link. Let me get your email instead."

    return f"Booking link sent! Tell them to check their phone, and remind them it might go to spam or promotions on iPhone."


def _send_booking_link_local(
    day: str,
    time: str,
    contact_name: str,
    context: CallContext,
    meeting_dt,
) -> str:
    """Fallback: Create local booking if CUA API is unavailable."""
    import os

    booking_id = _create_pending_booking(
        phone_number=context.phone_number,
        meeting_day=day,
        meeting_time=time,
        meeting_datetime=meeting_dt,
    )

    # Build URL using ngrok or configured host
    # Priority: NGROK_URL > BOOKING_HOST > voice-agent server host
    ngrok_url = os.environ.get("NGROK_URL", "")
    booking_host = os.environ.get("BOOKING_HOST", "")

    if ngrok_url:
        booking_url = f"{ngrok_url}/book/{booking_id}"
    elif booking_host:
        booking_url = f"https://{booking_host}/book/{booking_id}"
    else:
        # This shouldn't happen in production - NGROK_URL should always be set
        print("[Tools] WARNING: No NGROK_URL or BOOKING_HOST set for local booking fallback!")
        booking_url = f"https://app.paralleluniverse.ai/booking/{booking_id}"

    # Send SMS
    try:
        config = _get_config()
        TwilioClient = _get_twilio_client()
        twilio = TwilioClient(config)

        time_str = meeting_dt.strftime("%A at %I:%M %p").replace(" 0", " ")
        sms_message = (
            f"Hey {contact_name}! 👋 Here's your booking link for our demo on {time_str}: {booking_url}\n\n"
            f"Just pop in your email and you're all set! Takes 10 seconds.\n\n"
            f"📱 Heads up: If you're on iPhone, this might land in your Spam or Promotions folder - just check there if you don't see it right away!\n\n"
            f"Looking forward to chatting! - Alex from Parallel Universe"
        )

        twilio.send_sms(context.phone_number, sms_message)
        context.notes.append(f"Booking link sent (local): {booking_id}")
        context.outcome = "meeting_booked"

    except Exception as e:
        print(f"[Tools] SMS error in local fallback: {e}")
        return "I had trouble sending the link. Let me get your email instead."

    return f"Booking link sent! Tell them to check their phone, and remind them it might go to spam or promotions on iPhone."


@tool
def add_note(note: str) -> str:
    """
    Add a note about something important mentioned in the call.

    Use this to record useful information like:
    - Their current pain points
    - Competitors they mentioned
    - Specific needs or requirements
    - Objections raised

    Args:
        note: The note to add

    Returns:
        Confirmation
    """
    context = get_call_context()
    if not context:
        return "Error: No active call context"

    context.notes.append(note)
    return "Note recorded."


def _parse_meeting_time(day: str, time: str) -> datetime:
    """
    Parse natural language day/time into datetime.

    Simple implementation - can be enhanced with dateparser library.
    """
    import re
    now = datetime.now()

    print(f"[Tools] Parsing meeting time: day='{day}', time='{time}'")

    # Parse day
    day_lower = day.lower().strip()
    if day_lower == "today":
        target_date = now.date()
    elif day_lower == "tomorrow":
        target_date = now.date() + timedelta(days=1)
    elif day_lower in ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]:
        days_map = {
            "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
            "friday": 4, "saturday": 5, "sunday": 6
        }
        target_day = days_map[day_lower]
        current_day = now.weekday()
        days_ahead = target_day - current_day
        if days_ahead <= 0:
            days_ahead += 7
        target_date = now.date() + timedelta(days=days_ahead)
    else:
        # Try to extract day name from string like "Monday, January 5th"
        for day_name in ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]:
            if day_name in day_lower:
                days_map = {
                    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
                    "friday": 4, "saturday": 5, "sunday": 6
                }
                target_day = days_map[day_name]
                current_day = now.weekday()
                days_ahead = target_day - current_day
                if days_ahead <= 0:
                    days_ahead += 7
                target_date = now.date() + timedelta(days=days_ahead)
                break
        else:
            # Default to tomorrow if can't parse
            target_date = now.date() + timedelta(days=1)

    # Parse time
    time_lower = time.lower().replace(" ", "")
    hour = 10  # Default
    minute = 0  # Default

    if "morning" in time_lower:
        hour = 10
    elif "afternoon" in time_lower:
        hour = 14
    elif "evening" in time_lower:
        hour = 17
    else:
        # Try to extract hour and minutes
        match = re.search(r'(\d{1,2})(?::?(\d{2}))?(?:am|pm)?', time_lower)
        if match:
            hour = int(match.group(1))
            if match.group(2):
                minute = int(match.group(2))
            if 'pm' in time_lower and hour < 12:
                hour += 12
            if 'am' in time_lower and hour == 12:
                hour = 0

    result = datetime.combine(target_date, datetime.min.time().replace(hour=hour, minute=minute))
    print(f"[Tools] Parsed meeting time: {result.strftime('%A, %B %d at %I:%M %p')}")
    return result


# Export all tools
SALES_TOOLS = [check_availability, book_meeting, send_booking_link, request_callback, end_call, add_note]
