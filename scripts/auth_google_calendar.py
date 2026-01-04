#!/usr/bin/env python3
"""
Google Calendar OAuth Authentication Script

Run this once to generate the OAuth token for Google Calendar access.
The token will be saved to data/google_token.pickle and reused by the voice agent.

Usage:
    python scripts/auth_google_calendar.py
"""

import pickle
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# Scopes required for calendar access
SCOPES = ['https://www.googleapis.com/auth/calendar']

# Paths
DATA_DIR = Path(__file__).parent.parent / "data"
TOKEN_PATH = DATA_DIR / "google_token.pickle"
CREDENTIALS_PATH = DATA_DIR / "google_credentials.json"


def authenticate():
    """Run OAuth flow and save credentials."""
    print("=" * 50)
    print("Google Calendar Authentication")
    print("=" * 50)

    # Check credentials file exists
    if not CREDENTIALS_PATH.exists():
        print(f"\n❌ ERROR: Credentials file not found at:")
        print(f"   {CREDENTIALS_PATH}")
        print("\nTo fix this:")
        print("1. Go to https://console.cloud.google.com/")
        print("2. Create OAuth 2.0 credentials (Desktop app)")
        print("3. Download and save as 'data/google_credentials.json'")
        return False

    print(f"\n✓ Found credentials at: {CREDENTIALS_PATH}")

    # Check if token already exists and is valid
    creds = None
    if TOKEN_PATH.exists():
        print(f"✓ Found existing token at: {TOKEN_PATH}")
        with open(TOKEN_PATH, 'rb') as token:
            creds = pickle.load(token)

        if creds and creds.valid:
            print("✓ Token is still valid!")
            _test_calendar_access(creds)
            return True

        if creds and creds.expired and creds.refresh_token:
            print("⟳ Token expired, attempting refresh...")
            try:
                creds.refresh(Request())
                with open(TOKEN_PATH, 'wb') as token:
                    pickle.dump(creds, token)
                print("✓ Token refreshed successfully!")
                _test_calendar_access(creds)
                return True
            except Exception as e:
                print(f"✗ Refresh failed: {e}")
                print("  Will re-authenticate...")
                creds = None

    # Need to authenticate
    print("\n" + "-" * 50)
    print("Starting OAuth flow...")
    print("-" * 50 + "\n")

    try:
        flow = InstalledAppFlow.from_client_secrets_file(
            str(CREDENTIALS_PATH),
            SCOPES
        )

        port = 18091
        print(f"Open this URL in your browser:")
        print(f"http://localhost:{port}")
        print("\nWaiting for authorization...")

        creds = flow.run_local_server(
            port=port,
            open_browser=True,
            prompt='consent'
        )

        # Save the token
        with open(TOKEN_PATH, 'wb') as token:
            pickle.dump(creds, token)

        print("\n" + "=" * 50)
        print("✓ Authentication successful!")
        print(f"✓ Token saved to: {TOKEN_PATH}")
        print("=" * 50)

        _test_calendar_access(creds)
        return True

    except Exception as e:
        print(f"\n❌ Authentication failed: {e}")
        return False


def _test_calendar_access(creds):
    """Test that we can access the calendar."""
    print("\nTesting calendar access...")
    try:
        service = build('calendar', 'v3', credentials=creds)
        calendar = service.calendars().get(calendarId='primary').execute()
        print(f"✓ Connected to calendar: {calendar.get('summary', 'Primary')}")

        # Get upcoming events
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        events_result = service.events().list(
            calendarId='primary',
            timeMin=now,
            maxResults=3,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        events = events_result.get('items', [])

        if events:
            print(f"✓ Found {len(events)} upcoming event(s):")
            for event in events:
                start = event['start'].get('dateTime', event['start'].get('date'))
                print(f"  - {event.get('summary', 'Untitled')} ({start})")
        else:
            print("✓ No upcoming events found (calendar is accessible)")

    except Exception as e:
        print(f"✗ Calendar test failed: {e}")


if __name__ == "__main__":
    success = authenticate()

    if success:
        print("\n" + "=" * 50)
        print("NEXT STEPS:")
        print("=" * 50)
        print("1. Update .env: set MOCK_CALENDAR=false (or remove the line)")
        print("2. Restart the LangGraph server")
        print("3. Test by asking for availability")
    else:
        print("\n❌ Authentication incomplete. Please try again.")
