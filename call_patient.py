#!/usr/bin/env python3
"""
Make a healthcare appointment reminder call to a patient.

The voice agent will know the patient's name and appointment details.

Usage:
    # List all patients from the CSV
    python call_patient.py --list

    # Call a patient by index from the CSV
    python call_patient.py 0  # First patient
    python call_patient.py 1  # Second patient

    # Call a specific phone with patient context
    python call_patient.py +19029310062 --patient "John Smith" --date "January 17 2026" --time "2:30 PM" --provider "Dr. Williams" --clinic "Downtown Medical Center"
"""

import subprocess
import json
import sys
import csv
import argparse
from urllib.parse import urlencode
from dotenv import load_dotenv
load_dotenv('.env')
import os
from twilio.rest import Client


def get_ngrok_url():
    """Get the current ngrok public URL."""
    result = subprocess.run(['curl', '-s', 'localhost:4040/api/tunnels'], capture_output=True, text=True)
    data = json.loads(result.stdout)
    return data['tunnels'][0]['public_url']


def load_patients(csv_file='patients_appointments.csv'):
    """Load patients from CSV file."""
    patients = []
    with open(csv_file, newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            patients.append(row)
    return patients


def make_call(
    phone: str,
    patient_name: str = None,
    appointment_date: str = None,
    appointment_time: str = None,
    provider_name: str = None,
    clinic_name: str = None,
    appointment_type: str = None,
):
    """
    Make a healthcare reminder call to a patient.

    The context (patient info, appointment details) is passed to the voice agent
    via URL parameters, so it knows the appointment details.
    """
    ngrok_url = get_ngrok_url()

    # Build URL with patient context
    # Use owner_name for patient_name since that's what server.py looks for
    params = {}
    if patient_name:
        params['owner_name'] = patient_name  # Server uses owner_name field
    if clinic_name:
        params['business_name'] = clinic_name  # Server uses business_name field
    if appointment_date:
        params['appointment_date'] = appointment_date
    if appointment_time:
        params['appointment_time'] = appointment_time
    if provider_name:
        params['provider_name'] = provider_name
    if appointment_type:
        params['appointment_type'] = appointment_type

    # URL encode the parameters
    query_string = urlencode(params) if params else ""
    webhook_url = f"{ngrok_url}/voice/outbound"
    if query_string:
        webhook_url += f"?{query_string}"

    print(f"\n{'='*70}")
    print(f"INITIATING HEALTHCARE APPOINTMENT REMINDER CALL")
    print(f"{'='*70}")
    print(f"Patient:      {patient_name or 'N/A'}")
    print(f"Phone:        {phone}")
    print(f"Appointment:  {appointment_date or 'N/A'} at {appointment_time or 'N/A'}")
    print(f"Provider:     {provider_name or 'N/A'}")
    print(f"Clinic:       {clinic_name or 'N/A'}")
    print(f"Type:         {appointment_type or 'N/A'}")
    print(f"{'='*70}")
    print(f"Webhook: {webhook_url}")
    print(f"{'='*70}\n")

    # Check AGENT_MODE
    agent_mode = os.environ.get('AGENT_MODE', 'sales')
    if agent_mode != 'healthcare':
        print(f"WARNING: AGENT_MODE is '{agent_mode}', not 'healthcare'")
        print("Set AGENT_MODE=healthcare when starting the server for healthcare calls.")
        print()

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
        description="Make healthcare appointment reminder calls",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python call_patient.py --list                    # List all patients
  python call_patient.py 0                         # Call first patient
  python call_patient.py 1                         # Call second patient
  python call_patient.py +19029310062 --patient "John Smith" --provider "Dr. Williams"

Note: Make sure to start the server with AGENT_MODE=healthcare:
  AGENT_MODE=healthcare TTS_ENGINE=orpheus python -m sdr_agent serve
        """
    )

    parser.add_argument('target', nargs='?', help='Patient index (0-9) or phone number')
    parser.add_argument('--list', '-l', action='store_true', help='List all patients')
    parser.add_argument('--patient', '-p', help='Patient name (for manual calls)')
    parser.add_argument('--date', '-d', help='Appointment date (e.g., "January 17 2026")')
    parser.add_argument('--time', '-t', help='Appointment time (e.g., "2:30 PM")')
    parser.add_argument('--provider', help='Provider name (e.g., "Dr. Williams")')
    parser.add_argument('--clinic', '-c', help='Clinic name')
    parser.add_argument('--type', dest='appt_type', help='Appointment type (e.g., "Annual checkup")')
    parser.add_argument('--csv', default='patients_appointments.csv', help='CSV file with patients')

    args = parser.parse_args()

    # Load patients
    try:
        patients = load_patients(args.csv)
    except FileNotFoundError:
        print(f"Error: {args.csv} not found.")
        print("Create the file with columns: patient_name,phone,appointment_date,appointment_time,provider_name,clinic_name,appointment_type")
        sys.exit(1)

    # List mode
    if args.list:
        print(f"\n{'='*100}")
        print(f"PATIENTS FOR APPOINTMENT REMINDERS ({len(patients)} total)")
        print(f"{'='*100}")
        for i, patient in enumerate(patients):
            print(f"  [{i}] {patient['patient_name']:<20} | {patient['appointment_date']:<18} {patient['appointment_time']:<10} | {patient['provider_name']:<15} | {patient['phone']}")
        print(f"{'='*100}")
        print("\nTo call a patient: python call_patient.py <index>")
        print("Example: python call_patient.py 0\n")
        return

    if not args.target:
        parser.print_help()
        return

    # Determine if target is index or phone number
    if args.target.isdigit() and int(args.target) < 100:
        # Likely an index - check if valid
        index = int(args.target)
        if 0 <= index < len(patients):
            patient = patients[index]
            make_call(
                patient['phone'],
                patient_name=patient['patient_name'],
                appointment_date=patient['appointment_date'],
                appointment_time=patient['appointment_time'],
                provider_name=patient['provider_name'],
                clinic_name=patient['clinic_name'],
                appointment_type=patient['appointment_type'],
            )
        else:
            print(f"Error: Index {index} out of range. Use --list to see available patients.")
            sys.exit(1)
    elif args.target.startswith('+') or (args.target.isdigit() and len(args.target) >= 10):
        # It's a phone number
        if args.target.isdigit():
            # Just digits, add +1
            phone = f"+1{args.target}"
        else:
            phone = args.target

        make_call(
            phone,
            patient_name=args.patient,
            appointment_date=args.date,
            appointment_time=args.time,
            provider_name=args.provider,
            clinic_name=args.clinic,
            appointment_type=args.appt_type,
        )
    else:
        print(f"Error: '{args.target}' is not a valid index or phone number.")
        print("Use --list to see available patients, or provide a full phone number.")
        sys.exit(1)


if __name__ == "__main__":
    main()
