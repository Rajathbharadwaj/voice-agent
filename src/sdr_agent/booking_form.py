"""
Booking Form System

Instead of collecting email over the phone (error-prone with spelling),
send an SMS with a quick booking form link.
"""

import asyncio
import uuid
from datetime import datetime, timedelta
from typing import Optional
from dataclasses import dataclass, field

# In-memory store for pending bookings (use Redis in production)
_pending_bookings: dict[str, "PendingBooking"] = {}


@dataclass
class PendingBooking:
    """A pending booking waiting for form completion."""
    booking_id: str
    phone_number: str
    meeting_day: str
    meeting_time: str
    meeting_datetime: datetime
    created_at: datetime = field(default_factory=datetime.now)

    # Filled by form
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None
    completed: bool = False


def create_pending_booking(
    phone_number: str,
    meeting_day: str,
    meeting_time: str,
    meeting_datetime: datetime,
) -> str:
    """
    Create a pending booking and return the booking ID.

    The user will receive an SMS with a link to complete the booking.
    """
    booking_id = str(uuid.uuid4())[:8]  # Short ID for easy URLs

    booking = PendingBooking(
        booking_id=booking_id,
        phone_number=phone_number,
        meeting_day=meeting_day,
        meeting_time=meeting_time,
        meeting_datetime=meeting_datetime,
    )

    _pending_bookings[booking_id] = booking
    return booking_id


def get_pending_booking(booking_id: str) -> Optional[PendingBooking]:
    """Get a pending booking by ID."""
    return _pending_bookings.get(booking_id)


def complete_booking(
    booking_id: str,
    contact_name: str,
    contact_email: str,
) -> Optional[PendingBooking]:
    """
    Complete a pending booking with contact details.

    Returns the booking if successful, None if not found.
    """
    booking = _pending_bookings.get(booking_id)
    if not booking:
        return None

    booking.contact_name = contact_name
    booking.contact_email = contact_email
    booking.completed = True

    return booking


def get_booking_form_html(booking: PendingBooking, base_url: str) -> str:
    """Generate the HTML for the booking form."""
    time_str = booking.meeting_datetime.strftime("%A, %B %d at %I:%M %p")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Complete Your Booking - Parallel Universe</title>
    <style>
        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }}
        .card {{
            background: white;
            border-radius: 16px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            padding: 40px;
            max-width: 420px;
            width: 100%;
        }}
        .logo {{
            text-align: center;
            margin-bottom: 24px;
        }}
        .logo h1 {{
            font-size: 24px;
            color: #333;
        }}
        .logo span {{
            color: #667eea;
        }}
        h2 {{
            font-size: 20px;
            color: #333;
            margin-bottom: 8px;
            text-align: center;
        }}
        .meeting-time {{
            background: #f0f4ff;
            border-radius: 8px;
            padding: 16px;
            text-align: center;
            margin-bottom: 24px;
        }}
        .meeting-time p {{
            color: #667eea;
            font-weight: 600;
            font-size: 16px;
        }}
        .form-group {{
            margin-bottom: 20px;
        }}
        label {{
            display: block;
            font-weight: 500;
            color: #555;
            margin-bottom: 8px;
        }}
        input {{
            width: 100%;
            padding: 14px 16px;
            border: 2px solid #e0e0e0;
            border-radius: 8px;
            font-size: 16px;
            transition: border-color 0.2s;
        }}
        input:focus {{
            outline: none;
            border-color: #667eea;
        }}
        button {{
            width: 100%;
            padding: 16px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            border-radius: 8px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
            transition: transform 0.2s, box-shadow 0.2s;
        }}
        button:hover {{
            transform: translateY(-2px);
            box-shadow: 0 4px 20px rgba(102, 126, 234, 0.4);
        }}
        button:disabled {{
            opacity: 0.7;
            cursor: not-allowed;
            transform: none;
        }}
        .success {{
            text-align: center;
            padding: 20px;
        }}
        .success h2 {{
            color: #22c55e;
            margin-bottom: 16px;
        }}
        .success p {{
            color: #666;
            line-height: 1.6;
        }}
        .checkmark {{
            font-size: 48px;
            margin-bottom: 16px;
        }}
    </style>
</head>
<body>
    <div class="card">
        <div class="logo">
            <h1>Parallel <span>Universe</span></h1>
        </div>

        <div id="form-container">
            <h2>Complete Your Booking</h2>
            <div class="meeting-time">
                <p>📅 {time_str}</p>
            </div>

            <form id="booking-form" action="{base_url}/book/{booking.booking_id}" method="POST">
                <div class="form-group">
                    <label for="name">Your Name</label>
                    <input type="text" id="name" name="name" required placeholder="John Smith">
                </div>

                <div class="form-group">
                    <label for="email">Email Address</label>
                    <input type="email" id="email" name="email" required placeholder="john@example.com">
                </div>

                <button type="submit" id="submit-btn">Confirm Booking</button>
            </form>
        </div>

        <div id="success-container" style="display: none;">
            <div class="success">
                <div class="checkmark">✅</div>
                <h2>You're All Set!</h2>
                <p>Your demo is confirmed for <strong>{time_str}</strong>.</p>
                <p style="margin-top: 12px;">Check your email for the calendar invite.</p>
            </div>
        </div>
    </div>

    <script>
        document.getElementById('booking-form').addEventListener('submit', async (e) => {{
            e.preventDefault();
            const btn = document.getElementById('submit-btn');
            btn.disabled = true;
            btn.textContent = 'Booking...';

            const formData = new FormData(e.target);

            try {{
                const response = await fetch(e.target.action, {{
                    method: 'POST',
                    body: formData
                }});

                if (response.ok) {{
                    document.getElementById('form-container').style.display = 'none';
                    document.getElementById('success-container').style.display = 'block';
                }} else {{
                    alert('Something went wrong. Please try again.');
                    btn.disabled = false;
                    btn.textContent = 'Confirm Booking';
                }}
            }} catch (err) {{
                alert('Connection error. Please try again.');
                btn.disabled = false;
                btn.textContent = 'Confirm Booking';
            }}
        }});
    </script>
</body>
</html>"""


def get_already_booked_html() -> str:
    """HTML for when booking is already completed."""
    return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Booking Complete - Parallel Universe</title>
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }
        .card {
            background: white;
            border-radius: 16px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            padding: 40px;
            max-width: 420px;
            text-align: center;
        }
        h1 { color: #22c55e; margin-bottom: 16px; }
        p { color: #666; }
        .checkmark { font-size: 48px; margin-bottom: 16px; }
    </style>
</head>
<body>
    <div class="card">
        <div class="checkmark">✅</div>
        <h1>Already Booked!</h1>
        <p>This booking has already been completed. Check your email for the calendar invite.</p>
    </div>
</body>
</html>"""


def get_not_found_html() -> str:
    """HTML for when booking is not found."""
    return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Booking Not Found - Parallel Universe</title>
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }
        .card {
            background: white;
            border-radius: 16px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            padding: 40px;
            max-width: 420px;
            text-align: center;
        }
        h1 { color: #ef4444; margin-bottom: 16px; }
        p { color: #666; }
    </style>
</head>
<body>
    <div class="card">
        <h1>Booking Not Found</h1>
        <p>This booking link has expired or doesn't exist. Please contact us for assistance.</p>
    </div>
</body>
</html>"""
