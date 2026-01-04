#!/usr/bin/env python3
"""Make a test call to the voice agent."""

import subprocess
import json
import sys
from dotenv import load_dotenv
load_dotenv('.env')
import os
from twilio.rest import Client

# Get ngrok URL
result = subprocess.run(['curl', '-s', 'localhost:4040/api/tunnels'], capture_output=True, text=True)
data = json.loads(result.stdout)
ngrok_url = data['tunnels'][0]['public_url']

# Phone number (use argument or default)
phone = sys.argv[1] if len(sys.argv) > 1 else '+19029310062'

client = Client(os.environ['TWILIO_ACCOUNT_SID'], os.environ['TWILIO_AUTH_TOKEN'])
call = client.calls.create(
    url=f'{ngrok_url}/voice/outbound',
    to=phone,
    from_=os.environ['TWILIO_PHONE_NUMBER'],
    status_callback=f'{ngrok_url}/voice/status',
    status_callback_event=['initiated', 'ringing', 'answered', 'completed']
)
print(f'Call initiated to {phone}: {call.sid}')
