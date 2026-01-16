# Healthcare Appointment Reminder Voice Agent

A proactive outbound voice agent that contacts patients with upcoming appointments, confirms or reschedules them, and captures outcomes - built for the ThinkDTM challenge.

## Demo Video

[Link to demo recording - if available]

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              PATIENT CALL FLOW                               │
└─────────────────────────────────────────────────────────────────────────────┘

  ┌──────────┐        ┌──────────┐        ┌──────────────────┐
  │  Patient │◄──────►│  Twilio  │◄──────►│  Voice Server    │
  │  Phone   │  PSTN  │  (Voice) │  WSS   │  (FastAPI:8080)  │
  └──────────┘        └──────────┘        └────────┬─────────┘
                            │                      │
                            │ SMS                  │ Audio Pipeline
                            ▼                      ▼
                      ┌──────────┐        ┌──────────────────┐
                      │ Patient  │        │ InteractivePipeline│
                      │ receives │        │  ┌─────┐ ┌─────┐ │
                      │ SMS      │        │  │ STT │ │ TTS │ │
                      └──────────┘        │  │Whisp│ │Orph.│ │
                                          │  └──┬──┘ └──▲──┘ │
                                          └─────┼───────┼────┘
                                                │       │
                                                ▼       │
                                          ┌─────────────┴────┐
                                          │ LangGraph Platform│
                                          │    (Port 8123)    │
                                          │  ┌─────────────┐  │
                                          │  │ Healthcare  │  │
                                          │  │   Agent     │  │
                                          │  │  (Claude)   │  │
                                          │  └──────┬──────┘  │
                                          │         │         │
                                          │  ┌──────▼──────┐  │
                                          │  │   Tools     │  │
                                          │  │ • confirm   │  │
                                          │  │ • reschedule│  │
                                          │  │ • calendar  │  │
                                          │  │ • SMS       │  │
                                          │  │ • transfer  │  │
                                          │  └─────────────┘  │
                                          └──────────────────┬┘
                                                             │
                                          ┌──────────────────▼┐
                                          │  Google Calendar  │
                                          │  (availability)   │
                                          └───────────────────┘
```

---

## Features

### Core Requirements (All Implemented)

| Requirement | Implementation |
|-------------|----------------|
| Outbound voice call to patient | `call_patient.py` + Twilio Voice API |
| Agent clearly identifies itself | "Hi [name], this is Sarah from [clinic]..." |
| Reference upcoming appointment | Date, time, provider, clinic in greeting |
| Ask to keep or reschedule | Natural conversation flow |
| Capture call outcome | `confirmed`, `reschedule_requested`, `declined`, `voicemail`, `transferred` |

### Bonus Features (All Implemented)

| Feature | Implementation |
|---------|----------------|
| Human handoff | `transfer_to_staff(reason)` tool |
| Voicemail logic | Detects voicemail, leaves HIPAA-compliant message |
| Calendar integration | `check_reschedule_availability(day)` queries Google Calendar |
| SMS notifications | Sends appointment details and reschedule confirmations |

---

## Technology Stack

| Layer | Technology | Purpose |
|-------|------------|---------|
| **Telephony** | Twilio Voice + Media Streams | Outbound calls, WebSocket audio |
| **Server** | FastAPI + uvicorn | Webhooks, WebSocket handler |
| **STT** | Whisper (streaming) | Real-time speech-to-text |
| **TTS** | Orpheus (vLLM) | Low-latency text-to-speech (~200ms) |
| **Agent** | LangGraph + Claude Opus 4.5 | Conversational AI with tool use |
| **Calendar** | Google Calendar API | Real availability lookup |
| **Notifications** | Twilio SMS | Appointment confirmations |

---

## Conversation Flow

### Confirmation Flow
```
Agent: "Hi John, this is Sarah from Downtown Medical Center about your
        appointment with Dr. Williams on January 17th at 2:30 PM.
        Is this a good time?"

Patient: "Yes, go ahead"

Agent: "I'm calling to confirm your appointment. Would you like to
        keep this time, or would you prefer to reschedule?"

Patient: "I'll keep it"

Agent: "Perfect! I'll confirm that for you. I'm sending you a text
        with all the appointment details including the address and
        what to bring."

[SMS sent with appointment details]

Agent: "Is there anything else I can help you with?"

Patient: "No, that's all"

Agent: "Thank you, John! We look forward to seeing you. Have a great day!"
```

### Reschedule Flow (with Calendar Lookup)
```
Patient: "I need to reschedule actually"

Agent: "No problem at all! What day works better for you?"

Patient: "How about Wednesday?"

Agent: "Let me check what's available..."

[Calls check_reschedule_availability("Wednesday")]
[Returns: 9:00 AM, 10:30 AM, 2:00 PM, 3:30 PM]

Agent: "I see we have openings at 9 AM, 10:30 AM, 2 PM, and 3:30 PM.
        Which works best?"

Patient: "2 PM sounds good"

Agent: "I've scheduled you for Wednesday at 2 PM. You'll receive a
        text with the details. Is there anything else?"

[SMS sent confirming reschedule request]
```

---

## Healthcare Tools

| Tool | Purpose |
|------|---------|
| `get_appointment_details` | Retrieve patient info at call start |
| `confirm_appointment` | Record confirmation |
| `check_reschedule_availability` | Query Google Calendar for available slots |
| `request_reschedule` | Record new preference + send SMS |
| `send_appointment_sms` | Send details (address, parking, what to bring) |
| `provide_clinic_info` | Share clinic logistics |
| `transfer_to_staff` | Human handoff for complex requests |
| `end_call` | Record outcome and terminate |

---

## Project Structure

```
voice-agent/
├── call_patient.py                    # Initiate outbound calls
├── patients_appointments.csv          # Patient data
├── langgraph.json                     # LangGraph agent registration
├── pyproject.toml                     # Dependencies
│
├── src/sdr_agent/
│   ├── server.py                      # FastAPI + WebSocket server
│   ├── pipeline.py                    # Audio processing pipeline
│   ├── whisper_stt.py                 # Streaming speech-to-text
│   │
│   ├── agent/
│   │   ├── graph_healthcare.py        # LangGraph healthcare agent
│   │   ├── tools_healthcare.py        # 8 healthcare tools
│   │   └── prompts_healthcare.py      # System prompt + templates
│   │
│   └── telephony/
│       ├── media_stream.py            # Twilio WebSocket handler
│       └── twilio_client.py           # Twilio API wrapper
│
├── data/
│   └── google_token.pickle            # Google Calendar OAuth token
│
└── scripts/
    └── auth_google_calendar.py        # Google Calendar OAuth setup
```

---

## Setup & Installation

### Prerequisites
- Python 3.11+
- Twilio account with Voice capabilities
- Anthropic API key (for Claude)
- ngrok (for exposing local server)
- NVIDIA GPU (for Orpheus TTS) or use alternative TTS

### Environment Variables

```bash
# Required
TWILIO_ACCOUNT_SID=your_account_sid
TWILIO_AUTH_TOKEN=your_auth_token
TWILIO_PHONE_NUMBER=+1234567890
ANTHROPIC_API_KEY=your_api_key

# Optional
GOOGLE_CALENDAR_CREDENTIALS=path/to/credentials.json  # For real calendar
MOCK_CALENDAR=true  # Use mock data instead
```

### Installation

```bash
# Clone and setup
cd voice-agent
conda create -n voice-agent python=3.12
conda activate voice-agent
pip install -r requirements.txt

# Google Calendar setup (optional - mock data works without this)
python scripts/auth_google_calendar.py
```

---

## Running the Demo

### Terminal 1: Ngrok (expose local server)
```bash
ngrok http 8080
# Note the https URL (e.g., https://abc123.ngrok-free.app)
```

### Terminal 2: LangGraph Server
```bash
conda activate voice-agent
langgraph dev --port 8123
```

### Terminal 3: Voice Server
```bash
conda activate voice-agent
AGENT_MODE=healthcare TTS_ENGINE=orpheus PYTHONPATH=$(pwd)/src \
  uvicorn sdr_agent.server:app --host 0.0.0.0 --port 8080
```

### Terminal 4: Make a Call
```bash
conda activate voice-agent

# List patients
python call_patient.py --list

# Call first patient
python call_patient.py 0

# Call specific number
python call_patient.py +1234567890 \
  --patient "John Smith" \
  --date "January 20" \
  --time "2:30 PM" \
  --provider "Dr. Williams" \
  --clinic "Downtown Medical Center"
```

---

## Design Decisions

| Decision | Rationale |
|----------|-----------|
| **LangGraph over raw LLM** | Stateful multi-turn conversations, tool orchestration, automatic checkpointing |
| **Orpheus TTS** | ~200ms first-chunk latency vs 1-2s for cloud TTS - critical for natural conversation |
| **Whisper streaming STT** | Local processing avoids network latency, works reliably |
| **httpx for Google Calendar** | Bypasses Google SDK recursion issues in LangGraph runtime |
| **Mock calendar fallback** | System works without Google credentials for demos |
| **Context via RunnableConfig** | Passes patient data to tools cleanly without global state |
| **Tool-driven workflow** | Agent decides actions, tools encapsulate business logic |
| **HIPAA-aware prompts** | Agent trained to not share medical info with third parties |

---

## Outcomes Captured

| Outcome | Description |
|---------|-------------|
| `confirmed` | Patient confirmed the appointment |
| `reschedule_requested` | Patient wants a new time |
| `declined` | Patient cancelled appointment |
| `voicemail` | Left voicemail message |
| `transferred` | Handed off to human staff |
| `no_answer` | No one answered |

---

## Sample Patient Data

The system reads from `patients_appointments.csv`:

```csv
patient_name,phone_number,appointment_date,appointment_time,provider_name,clinic_name,appointment_type
John Smith,+19029310062,January 17 2026,2:30 PM,Dr. Williams,Downtown Medical Center,Annual checkup
Jane Doe,+19029310063,January 18 2026,10:00 AM,Dr. Smith,Westside Clinic,Follow-up visit
```

---

## Future Enhancements

- [ ] Real-time calendar booking (not just recording preference)
- [ ] Multi-language support
- [ ] Integration with EHR systems
- [ ] Analytics dashboard for call outcomes
- [ ] A/B testing different conversation flows

---

## Contact

Built by Rajath for ThinkDTM challenge.

Questions? Reach out at [your email]
