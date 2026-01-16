"""
Healthcare Agent Tools

Tools available to the healthcare appointment reminder agent during calls.
"""

import os
import pickle
from datetime import datetime, timedelta
from typing import Optional
from dataclasses import dataclass, field
from pathlib import Path

from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig


# ============================================================================
# Calendar Service Classes (from tools.py)
# ============================================================================

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


class HttpxCalendarService:
    """Calendar service using httpx directly (bypasses Google SDK recursion in LangGraph)."""

    def __init__(self, access_token: str):
        self.access_token = access_token
        self.base_url = "https://www.googleapis.com/calendar/v3"

    def _make_request(self, method: str, endpoint: str, params: dict = None, json_data: dict = None):
        """Make authenticated request to Google Calendar API."""
        import httpx
        url = f"{self.base_url}{endpoint}"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=30.0) as client:
            if method == "GET":
                response = client.get(url, headers=headers, params=params)
            elif method == "POST":
                response = client.post(url, headers=headers, json=json_data, params=params)
            else:
                raise ValueError(f"Unsupported method: {method}")
            response.raise_for_status()
            return response.json()

    def get_availability_info(self, date, slot_duration_minutes=15, start_hour=9, end_hour=17):
        """Get both available slots and busy periods for a given date."""
        from zoneinfo import ZoneInfo
        local_tz = ZoneInfo('America/Edmonton')
        day_start = date.replace(hour=start_hour, minute=0, second=0, microsecond=0, tzinfo=local_tz)
        day_end = date.replace(hour=end_hour, minute=0, second=0, microsecond=0, tzinfo=local_tz)

        try:
            events_result = self._make_request("GET", "/calendars/primary/events", params={
                "timeMin": day_start.isoformat(),
                "timeMax": day_end.isoformat(),
                "singleEvents": "true",
                "orderBy": "startTime",
                "timeZone": "America/Edmonton",
            })
            events = events_result.get('items', [])
        except Exception as e:
            print(f"[Calendar] Error fetching events: {e}")
            return {"available": [], "busy": []}

        busy_times = []
        busy_info = []
        for event in events:
            start = event['start'].get('dateTime', event['start'].get('date'))
            end = event['end'].get('dateTime', event['end'].get('date'))
            summary = event.get('summary', 'Busy')
            if 'T' in start:
                try:
                    start_dt = datetime.fromisoformat(start.replace('Z', '+00:00') if start.endswith('Z') else start)
                    end_dt = datetime.fromisoformat(end.replace('Z', '+00:00') if end.endswith('Z') else end)
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

        available = []
        current = day_start
        slot_delta = timedelta(minutes=slot_duration_minutes)
        while current + slot_delta <= day_end:
            slot_end = current + slot_delta
            is_free = all(slot_end <= bs or current >= be for bs, be in busy_times)
            if is_free:
                available.append(current)
            current += slot_delta

        return {"available": available, "busy": busy_info}


def _get_calendar_service():
    """Get Google Calendar service using httpx (bypasses SDK recursion issues)."""
    # Use mock if MOCK_CALENDAR is explicitly true
    if os.environ.get("MOCK_CALENDAR", "").lower() in ("true", "1", "yes"):
        print("[Healthcare Tools] Using mock calendar service")
        return MockCalendarService()

    # Try to load token from pickle file
    token_path = Path(__file__).parent.parent.parent.parent / "data" / "google_token.pickle"
    if not token_path.exists():
        print("[Healthcare Calendar] No token file found - using mock data")
        return MockCalendarService()

    try:
        with open(token_path, 'rb') as f:
            creds = pickle.load(f)

        # Check if token is expired
        if creds.expired:
            # Try to refresh using httpx directly (bypass google SDK)
            if creds.refresh_token:
                import httpx
                client_id = creds.client_id
                client_secret = creds.client_secret
                refresh_token = creds.refresh_token

                try:
                    with httpx.Client(timeout=10.0) as client:
                        response = client.post(
                            "https://oauth2.googleapis.com/token",
                            data={
                                "client_id": client_id,
                                "client_secret": client_secret,
                                "refresh_token": refresh_token,
                                "grant_type": "refresh_token",
                            },
                        )
                        response.raise_for_status()
                        token_data = response.json()
                        new_access_token = token_data["access_token"]
                        print("[Healthcare Calendar] Token refreshed via httpx")
                        return HttpxCalendarService(new_access_token)
                except Exception as e:
                    print(f"[Healthcare Calendar] Token refresh failed: {e}")
                    return MockCalendarService()
            else:
                print("[Healthcare Calendar] Token expired and no refresh token")
                return MockCalendarService()

        # Token is valid, use it directly
        return HttpxCalendarService(creds.token)

    except Exception as e:
        print(f"[Healthcare Calendar] Error loading credentials: {e}")
        return MockCalendarService()


@dataclass
class HealthcareCallContext:
    """Context for the current healthcare call."""
    call_id: str
    patient_name: str
    phone_number: str
    appointment_date: str
    appointment_time: str
    provider_name: str
    clinic_name: str
    appointment_type: str
    call_sid: Optional[str] = None

    # Reschedule preferences
    preferred_date: Optional[str] = None
    preferred_time: Optional[str] = None
    reschedule_reason: Optional[str] = None

    # Outcome
    outcome: Optional[str] = None
    ended: bool = False
    notes: list[str] = field(default_factory=list)


# Global call context - set per call
_current_context: Optional[HealthcareCallContext] = None


def set_healthcare_call_context(context: HealthcareCallContext):
    """Set the context for the current healthcare call."""
    global _current_context
    _current_context = context


def get_healthcare_call_context() -> Optional[HealthcareCallContext]:
    """Get the current healthcare call context."""
    return _current_context


def clear_healthcare_call_context():
    """Clear the healthcare call context."""
    global _current_context
    _current_context = None


def _get_context_from_config(config: RunnableConfig = None) -> Optional[HealthcareCallContext]:
    """Get healthcare call context from RunnableConfig or global fallback."""
    # First try to get from RunnableConfig (when running through LangGraph Platform)
    if config and "configurable" in config:
        cfg = config["configurable"]
        if cfg.get("phone_number") and cfg.get("patient_name"):
            return HealthcareCallContext(
                call_id=cfg.get("call_sid", ""),
                patient_name=cfg.get("patient_name", ""),
                phone_number=cfg.get("phone_number", ""),
                appointment_date=cfg.get("appointment_date", ""),
                appointment_time=cfg.get("appointment_time", ""),
                provider_name=cfg.get("provider_name", ""),
                clinic_name=cfg.get("clinic_name", ""),
                appointment_type=cfg.get("appointment_type", ""),
                call_sid=cfg.get("call_sid"),
            )
    # Fallback to global context (when running directly)
    return get_healthcare_call_context()


def _get_twilio_client():
    """Get Twilio client, handling import issues."""
    from dotenv import load_dotenv
    import httpx
    import base64
    load_dotenv()

    class MinimalTwilioClient:
        def __init__(self, config=None):
            self.account_sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
            self.auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "")
            self.from_number = os.environ.get("TWILIO_PHONE_NUMBER", "")

        def send_sms(self, to_number: str, message: str):
            """Send SMS via Twilio REST API directly (bypasses SDK)."""
            url = f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}/Messages.json"
            auth = base64.b64encode(f"{self.account_sid}:{self.auth_token}".encode()).decode()

            with httpx.Client(timeout=30.0) as client:
                response = client.post(
                    url,
                    headers={
                        "Authorization": f"Basic {auth}",
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                    data={
                        "To": to_number,
                        "From": self.from_number,
                        "Body": message,
                    },
                )
                response.raise_for_status()
                return response.json()

    return MinimalTwilioClient


@tool
def confirm_appointment(config: RunnableConfig = None) -> str:
    """
    Confirm the patient's appointment.

    Call this when the patient confirms they want to keep their scheduled appointment.

    Returns:
        Confirmation message
    """
    context = _get_context_from_config(config)
    if not context:
        return "Error: No active call context"

    context.outcome = "confirmed"
    context.notes.append(f"Appointment confirmed: {context.appointment_date} at {context.appointment_time}")

    print(f"[Healthcare] Appointment confirmed for {context.patient_name}")

    return f"Appointment confirmed for {context.appointment_date} at {context.appointment_time} with {context.provider_name}."


@tool
def request_reschedule(
    preferred_date: str,
    preferred_time: str,
    reason: Optional[str] = None,
    config: RunnableConfig = None,
) -> str:
    """
    Record a reschedule request from the patient.

    Call this when the patient wants to reschedule their appointment.
    The scheduling team will follow up within 24 hours.

    Args:
        preferred_date: The patient's preferred date (e.g., "next Monday", "January 20")
        preferred_time: The patient's preferred time (e.g., "morning", "around 9 AM", "afternoon")
        reason: Optional reason for rescheduling

    Returns:
        Confirmation message
    """
    context = _get_context_from_config(config)
    if not context:
        return "Error: No active call context"

    context.outcome = "reschedule_requested"
    context.preferred_date = preferred_date
    context.preferred_time = preferred_time
    context.reschedule_reason = reason

    note = f"Reschedule requested: {preferred_date} at {preferred_time}"
    if reason:
        note += f" (Reason: {reason})"
    context.notes.append(note)

    print(f"[Healthcare] Reschedule requested for {context.patient_name}: {preferred_date} at {preferred_time}")

    # Send SMS confirming reschedule request
    try:
        TwilioClient = _get_twilio_client()
        twilio = TwilioClient()

        sms_message = (
            f"Hi {context.patient_name},\n\n"
            f"We've received your request to reschedule your appointment with {context.provider_name}.\n\n"
            f"Preferred time: {preferred_date} at {preferred_time}\n\n"
            f"Our scheduling team will contact you within 24 hours to confirm your new appointment.\n\n"
            f"Questions? Call us at 555-0123\n\n"
            f"- {context.clinic_name}"
        )

        twilio.send_sms(context.phone_number, sms_message)
        context.notes.append("Reschedule confirmation SMS sent")
        print(f"[Healthcare] Reschedule SMS sent to {context.phone_number}")
    except Exception as e:
        print(f"[Healthcare] SMS error: {e}")

    return f"Reschedule request recorded. Preference: {preferred_date} at {preferred_time}. Our team will call back within 24 hours."


@tool
def send_appointment_sms(config: RunnableConfig = None) -> str:
    """
    Send SMS with full appointment details after confirmation.

    Call this after confirming an appointment to send the patient
    a text with date, time, address, and what to bring.

    Returns:
        Confirmation that SMS was sent
    """
    context = _get_context_from_config(config)
    if not context:
        return "Error: No active call context"

    try:
        TwilioClient = _get_twilio_client()
        twilio = TwilioClient()

        sms_message = f"""APPOINTMENT CONFIRMED

{context.clinic_name}
{context.appointment_date} at {context.appointment_time}
{context.provider_name}

123 Medical Center Dr, Suite 200
Free parking in Lot B

Please bring:
- Photo ID
- Insurance card
- List of current medications

Arrive 15 minutes early for check-in.

Questions? Reply to this message or call 555-0123"""

        twilio.send_sms(context.phone_number, sms_message)
        context.notes.append("Appointment details SMS sent")
        print(f"[Healthcare] Appointment SMS sent to {context.phone_number}")

        return "I've sent you a text message with all the appointment details including the address and what to bring."

    except Exception as e:
        print(f"[Healthcare] SMS error: {e}")
        return "I wasn't able to send the text message, but your appointment is confirmed. Please call us if you need the details."


@tool
def provide_clinic_info(config: RunnableConfig = None) -> str:
    """
    Provide clinic information to the patient.

    Call this when the patient asks about address, parking, or what to bring.

    Returns:
        Clinic information string
    """
    context = _get_context_from_config(config)
    if not context:
        return "Error: No active call context"

    clinic_info = f"""
{context.clinic_name} is located at 123 Medical Center Dr, Suite 200.

Parking: Free parking is available in Lot B, just behind the building.

Please bring:
- A photo ID
- Your insurance card
- A list of current medications
- Any referral paperwork if applicable

We recommend arriving about 15 minutes early for check-in.
"""
    context.notes.append("Provided clinic info")
    return clinic_info


@tool
def transfer_to_staff(
    reason: str,
    config: RunnableConfig = None,
) -> str:
    """
    Transfer the call to human staff for complex requests.

    Call this when the patient needs assistance beyond appointment
    confirmation/rescheduling, such as:
    - Medical questions
    - Insurance inquiries
    - Complaints or concerns
    - Requests for test results

    Args:
        reason: The reason for transferring to staff

    Returns:
        Transfer confirmation message
    """
    context = _get_context_from_config(config)
    if not context:
        return "Error: No active call context"

    context.outcome = "transferred"
    context.notes.append(f"Transferred to staff: {reason}")

    print(f"[Healthcare] Transfer requested for {context.patient_name}: {reason}")

    # In production, this would initiate an actual transfer
    # For demo purposes, we simulate the handoff
    return "I'll connect you with our scheduling team right now. Please hold for just a moment."


@tool
def check_reschedule_availability(day: str = "tomorrow", config: RunnableConfig = None) -> str:
    """
    Check calendar availability for rescheduling an appointment.

    Call this when a patient wants to reschedule to see what slots are available.
    Use the results to offer 2-3 specific times to the patient.

    Args:
        day: The day to check (e.g., "today", "tomorrow", "Monday", "next week")

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
        elif "next week" in day_lower:
            # Default to next Monday
            current_day = now.weekday()
            days_ahead = 7 - current_day  # Days until next Monday
            check_date = now + timedelta(days=days_ahead)
        else:
            # Default to tomorrow if can't parse
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

        print(f"[Healthcare] Checked availability for {day}: {len(slots)} slots available")
        return response

    except Exception as e:
        print(f"[Healthcare Tools] Availability check error: {e}")
        return "I can check availability - what day works best for you?"


@tool
def get_appointment_details(config: RunnableConfig = None) -> str:
    """
    Get the current patient's appointment details.

    Call this FIRST when:
    - The call starts, to know who you're talking to
    - The patient asks about their appointment time, date, or provider
    - You need to confirm any appointment information
    - You're about to reference appointment details

    Returns:
        Full appointment details including patient name, date, time, provider, and clinic.
    """
    context = _get_context_from_config(config)
    if not context:
        return "Error: No active call context - appointment details not available"

    return f"""APPOINTMENT DETAILS:
- Patient: {context.patient_name}
- Date: {context.appointment_date}
- Time: {context.appointment_time}
- Provider: {context.provider_name}
- Clinic: {context.clinic_name}
- Appointment Type: {context.appointment_type}"""


@tool
def end_call(
    outcome: str,
    notes: Optional[str] = None,
    config: RunnableConfig = None,
) -> str:
    """
    End the current call and record the outcome.

    Call this when the conversation is ending, whether successful or not.

    Args:
        outcome: The call outcome. Must be one of:
            - "confirmed": Patient confirmed the appointment
            - "reschedule_requested": Patient requested to reschedule
            - "declined": Patient declined/cancelled appointment
            - "no_answer": No one answered the call
            - "voicemail": Left a voicemail message
            - "transferred": Transferred to human staff

        notes: Optional notes about the call

    Returns:
        Confirmation that the call has ended
    """
    context = _get_context_from_config(config)
    if not context:
        return "Error: No active call context"

    valid_outcomes = [
        "confirmed", "reschedule_requested", "declined",
        "no_answer", "voicemail", "transferred"
    ]

    if outcome not in valid_outcomes:
        outcome = "confirmed" if context.outcome == "confirmed" else "declined"

    context.outcome = outcome
    context.ended = True
    if notes:
        context.notes.append(notes)

    print(f"[Healthcare] Call ended for {context.patient_name}: {outcome}")
    if context.notes:
        print(f"[Healthcare] Notes: {context.notes}")

    return f"Call ended with outcome: {outcome}"


# Export all healthcare tools
HEALTHCARE_TOOLS = [
    get_appointment_details,  # Agent should call this first to know who they're talking to
    confirm_appointment,
    check_reschedule_availability,  # Check calendar before offering reschedule times
    request_reschedule,
    send_appointment_sms,
    provide_clinic_info,
    transfer_to_staff,
    end_call,
]
