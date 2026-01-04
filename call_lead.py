#!/usr/bin/env python3
"""
Make a call to a lead with full context (business name, owner name).

The voice agent will know who to ask for when the call connects.

Usage:
    # Call a lead by index from the CSV
    python call_lead.py 0  # First lead
    python call_lead.py 3  # Fourth lead

    # Call a specific phone with context
    python call_lead.py +14032661212 --business "Skyview Ranch Dental" --owner "Dr. Himani Gupta"

    # List all leads
    python call_lead.py --list
"""

import subprocess
import json
import sys
import csv
import argparse
from urllib.parse import urlencode, quote
from dotenv import load_dotenv
load_dotenv('.env')
import os
from twilio.rest import Client


def get_ngrok_url():
    """Get the current ngrok public URL."""
    result = subprocess.run(['curl', '-s', 'localhost:4040/api/tunnels'], capture_output=True, text=True)
    data = json.loads(result.stdout)
    return data['tunnels'][0]['public_url']


def load_leads(csv_file='leads_dental_calgary.csv'):
    """Load leads from CSV file."""
    leads = []
    with open(csv_file, newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            leads.append(row)
    return leads


def make_call(phone: str, business_name: str = None, owner_name: str = None):
    """
    Make a call to a phone number with lead context.

    The context (business_name, owner_name) is passed to the voice agent
    via URL parameters, so it knows who to ask for.
    """
    ngrok_url = get_ngrok_url()

    # Build URL with lead context
    params = {}
    if business_name:
        params['business_name'] = business_name
    if owner_name:
        params['owner_name'] = owner_name

    # URL encode the parameters
    query_string = urlencode(params) if params else ""
    webhook_url = f"{ngrok_url}/voice/outbound"
    if query_string:
        webhook_url += f"?{query_string}"

    print(f"\n{'='*60}")
    print(f"INITIATING CALL")
    print(f"{'='*60}")
    print(f"Phone:    {phone}")
    print(f"Business: {business_name or 'N/A'}")
    print(f"Owner:    {owner_name or 'N/A'}")
    print(f"Webhook:  {webhook_url}")
    print(f"{'='*60}\n")

    client = Client(os.environ['TWILIO_ACCOUNT_SID'], os.environ['TWILIO_AUTH_TOKEN'])
    call = client.calls.create(
        url=webhook_url,
        to=phone,
        from_=os.environ['TWILIO_PHONE_NUMBER'],
        status_callback=f'{ngrok_url}/voice/status',
        status_callback_event=['initiated', 'ringing', 'answered', 'completed']
    )

    print(f"Call initiated: {call.sid}")
    return call.sid


def main():
    parser = argparse.ArgumentParser(
        description="Make calls to leads with context",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python call_lead.py --list           # List all leads
  python call_lead.py 0                # Call first lead
  python call_lead.py 3                # Call fourth lead
  python call_lead.py +14032661212 --business "Skyview Dental" --owner "Dr. Gupta"
        """
    )

    parser.add_argument('target', nargs='?', help='Lead index (0-9) or phone number')
    parser.add_argument('--list', '-l', action='store_true', help='List all leads')
    parser.add_argument('--business', '-b', help='Business name (for manual calls)')
    parser.add_argument('--owner', '-o', help='Owner name (for manual calls)')
    parser.add_argument('--csv', default='leads_dental_calgary.csv', help='CSV file with leads')

    args = parser.parse_args()

    # Load leads
    try:
        leads = load_leads(args.csv)
    except FileNotFoundError:
        print(f"Error: {args.csv} not found. Run the scraper first.")
        sys.exit(1)

    # List mode
    if args.list:
        print(f"\n{'='*80}")
        print(f"AVAILABLE LEADS ({len(leads)} total)")
        print(f"{'='*80}")
        for i, lead in enumerate(leads):
            owner = lead.get('owner_name') or 'N/A'
            print(f"  [{i}] {lead['business_name'][:35]:<35} | {owner[:20]:<20} | {lead['phone']}")
        print(f"{'='*80}\n")
        return

    if not args.target:
        parser.print_help()
        return

    # Determine if target is index or phone number
    # Small numbers (0-99) are likely indices, long digit strings are phone numbers
    if args.target.isdigit() and int(args.target) < 100:
        # Likely an index - check if valid
        index = int(args.target)
        if 0 <= index < len(leads):
            lead = leads[index]
            make_call(
                lead['phone'],
                business_name=lead['business_name'],
                owner_name=lead.get('owner_name')
            )
        else:
            print(f"Error: Index {index} out of range. Use --list to see available leads.")
            sys.exit(1)
    elif args.target.startswith('+') or (args.target.isdigit() and len(args.target) >= 10):
        # It's a phone number
        if args.target.isdigit():
            # Just digits, add +1
            phone = f"+1{args.target}"
        else:
            phone = args.target

        make_call(phone, business_name=args.business, owner_name=args.owner)
    else:
        print(f"Error: '{args.target}' is not a valid index or phone number.")
        print("Use --list to see available leads, or provide a full phone number.")
        sys.exit(1)


if __name__ == "__main__":
    main()
