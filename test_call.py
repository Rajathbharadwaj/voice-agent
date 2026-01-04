#!/usr/bin/env python3
"""
Quick Test Call Script

Run this to make a test call to the configured number.
Usage: python test_call.py
"""

import sys
sys.path.insert(0, 'src')

# Test configuration
TEST_PHONE_NUMBER = "+19029310062"  # Rajat's number

def make_test_call():
    """Make a test call using Twilio."""
    import requests

    # Get ngrok URL
    try:
        resp = requests.get("http://localhost:4040/api/tunnels", timeout=2)
        tunnels = resp.json().get("tunnels", [])
        if not tunnels:
            print("ERROR: No ngrok tunnel found. Start ngrok first: ngrok http 8080")
            return
        ngrok_url = tunnels[0]["public_url"]
        print(f"Ngrok URL: {ngrok_url}")
    except Exception as e:
        print(f"ERROR: Can't connect to ngrok API: {e}")
        print("Make sure ngrok is running: ngrok http 8080")
        return

    # Check server is running
    try:
        resp = requests.get("http://localhost:8080/", timeout=2)
        if resp.status_code != 200:
            print("ERROR: Server not responding. Start it first.")
            return
        print("Server: OK")
    except Exception as e:
        print(f"ERROR: Server not running: {e}")
        return

    # Make the call
    from sdr_agent.config import load_config
    from sdr_agent.telephony.twilio_client import TwilioClient

    config = load_config()
    twilio = TwilioClient(config)

    # Webhook URL for TwiML (which then connects to WebSocket)
    webhook_url = ngrok_url + "/voice/outbound"
    print(f"Webhook URL: {webhook_url}")
    print(f"Calling: {TEST_PHONE_NUMBER}")
    print("-" * 40)

    try:
        call_sid = twilio.make_call(
            to_number=TEST_PHONE_NUMBER,
            webhook_url=webhook_url,
            metadata={"test_call": "true"}
        )
        print(f"Call initiated! SID: {call_sid}")
        print("Pick up your phone!")
    except Exception as e:
        print(f"ERROR making call: {e}")

if __name__ == "__main__":
    make_test_call()
