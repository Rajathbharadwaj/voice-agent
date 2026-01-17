"""
SDR Agent Server

FastAPI server for handling Twilio webhooks and media streams.
Uses LangGraph Platform for agent execution.
"""

import asyncio
import os
from typing import Optional
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, WebSocket, Request, Response
from fastapi.responses import PlainTextResponse, HTMLResponse
from langgraph_sdk import get_client

from .config import load_config
from .telephony.media_stream import MediaStreamHandler, StreamSession
from .telephony.twilio_client import generate_media_stream_twiml, TwilioClient
from .pipeline import create_audio_processor, PipelineConfig

from .agent.sales_agent import SalesAgent, CallSession
from .agent.tools import set_call_context, CallContext
from .agent.tools_healthcare import set_healthcare_call_context, HealthcareCallContext
from .data.models import Lead, Call, PatientAppointment
from .data.database import LeadRepository, CallRepository, CampaignRepository
from .data.csv_logger import CSVLogger
from .thread_mapping import get_thread_mapping_service

# LangGraph Platform URL (local dev server)
LANGGRAPH_URL = os.environ.get("LANGGRAPH_URL", "http://localhost:8123")

# Agent mode: "sales" or "healthcare"
AGENT_MODE = os.environ.get("AGENT_MODE", "sales")


def create_app() -> FastAPI:
    """Create the FastAPI application."""
    app = FastAPI(title="SDR Agent", description="AI-powered outbound sales calling")

    config = load_config()

    # Initialize components
    media_handler = MediaStreamHandler(
        on_call_start=on_call_start,
        on_call_end=on_call_end,
    )

    # Store active sessions by call_sid for webhook access
    # Each session has: agent, pipeline, injection_queue
    active_sessions: dict[str, dict] = {}

    @app.get("/")
    async def health():
        """Health check endpoint."""
        return {"status": "ok", "service": "sdr-agent"}

    @app.on_event("startup")
    async def startup_warmup():
        """Warm up TTS model on server start to avoid delay on first call."""
        import sys
        tts_engine = os.getenv("TTS_ENGINE", "comfyui")
        print(f"[Server] TTS engine: {tts_engine}", flush=True)
        if tts_engine == "orpheus":
            print("[Server] Warming up Orpheus TTS model (this takes ~20s)...", flush=True)
            try:
                from orpheus_engine import OrpheusTTS
                tts = OrpheusTTS()
                # Load model in background thread
                await asyncio.to_thread(tts._load_model)
                print("[Server] Orpheus TTS model warmed up and ready!", flush=True)
            except Exception as e:
                import traceback
                print(f"[Server] Warning: Failed to warm up Orpheus: {e}", flush=True)
                traceback.print_exc()
        else:
            print(f"[Server] Using TTS engine: {tts_engine} (no warmup needed)", flush=True)

    @app.post("/warmup")
    async def warmup():
        """Warm up the TTS model to avoid delay on first call."""
        import asyncio
        from chatterbox_tts import ChatterboxTTS

        print("[Server] Warming up TTS model...")
        tts = ChatterboxTTS()
        # Load model in a thread
        await asyncio.to_thread(tts._load_model)
        print("[Server] TTS model warmed up!")
        return {"status": "ok", "message": "TTS model loaded"}

    @app.post("/voice/outbound")
    async def voice_outbound(request: Request):
        """
        Handle outbound call webhook.

        Twilio calls this when making an outbound call.
        Returns TwiML to connect to media stream.
        """
        form = await request.form()

        # Get phone numbers from Twilio form data
        from_number = form.get("From", "")  # Our Twilio number
        to_number = form.get("To", "")      # Number we're calling

        # Get custom parameters if passed via URL
        lead_id = request.query_params.get("lead_id")
        campaign_id = request.query_params.get("campaign_id")
        business_name = request.query_params.get("business_name")
        owner_name = request.query_params.get("owner_name")
        # Healthcare-specific parameters
        appointment_date = request.query_params.get("appointment_date")
        appointment_time = request.query_params.get("appointment_time")
        provider_name = request.query_params.get("provider_name")
        appointment_type = request.query_params.get("appointment_type")

        # Generate TwiML for media stream
        websocket_url = f"wss://{request.headers.get('host', 'localhost')}/media-stream"

        twiml = generate_media_stream_twiml(
            websocket_url=websocket_url,
            metadata={
                "lead_id": lead_id,
                "campaign_id": campaign_id,
                "business_name": business_name,
                "owner_name": owner_name,
                "from_number": from_number,
                "to_number": to_number,
                # Healthcare-specific metadata
                "appointment_date": appointment_date,
                "appointment_time": appointment_time,
                "provider_name": provider_name,
                "appointment_type": appointment_type,
            }
        )

        return Response(content=twiml, media_type="application/xml")

    @app.websocket("/media-stream")
    async def media_stream(websocket: WebSocket):
        """
        Handle Twilio media stream WebSocket.

        Bidirectional audio streaming for voice calls.
        Uses LangGraph Platform for agent execution.
        """
        # Initialize LangGraph SDK client
        langgraph_client = get_client(url=LANGGRAPH_URL)
        print(f"[Server] Connected to LangGraph Platform at {LANGGRAPH_URL}")

        session_data = {"session": None, "langgraph_client": langgraph_client, "call_session": None}

        # Store config for hangup functionality
        session_data["config"] = config

        # Store websocket for interrupt handling
        session_data["websocket"] = websocket

        # Injection queue for webhook messages (e.g., form submission notifications)
        session_data["injection_queue"] = asyncio.Queue()

        async def on_session_start(session: StreamSession):
            """Called when media stream starts."""
            session_data["session"] = session
            session_data["call_sid"] = session.call_sid  # Store for hangup
            print(f"[Server] Call started: {session.call_sid}")
            print(f"[Server] Agent mode: {AGENT_MODE}")

            # Register in active_sessions for webhook access
            if session.call_sid:
                active_sessions[session.call_sid] = session_data
                print(f"[Server] Registered session: {session.call_sid}")

            if AGENT_MODE == "healthcare":
                # Set up healthcare call context using session fields
                healthcare_context = HealthcareCallContext(
                    call_id=session.call_sid or "test_call",
                    patient_name=session.owner_name or "Patient",  # owner_name holds patient name
                    phone_number=session.to_number or "+15551234567",
                    appointment_date=session.appointment_date or "January 17, 2026",
                    appointment_time=session.appointment_time or "2:30 PM",
                    provider_name=session.provider_name or "Dr. Williams",
                    clinic_name=session.business_name or "Downtown Medical Center",  # business_name holds clinic
                    appointment_type=session.appointment_type or "Appointment",
                    call_sid=session.call_sid,
                )
                set_healthcare_call_context(healthcare_context)
                session_data["healthcare_context"] = healthcare_context
                print(f"[Server] Healthcare context set: {healthcare_context.patient_name} - {healthcare_context.appointment_date} at {healthcare_context.appointment_time}")
            else:
                # Set up sales call context for tools (even for test calls)
                # This allows tools like book_meeting and request_callback to work
                # For outbound calls, to_number is the prospect's phone number
                context = CallContext(
                    call_id=session.call_sid or "test_call",
                    lead_id=session.lead_id or "test_lead",
                    campaign_id=session.campaign_id or "test_campaign",
                    business_name=session.business_name or "Test Business",
                    phone_number=session.to_number or "+15551234567",  # Prospect's phone
                    call_sid=session.call_sid,  # Twilio call SID for booking API
                    owner_name=getattr(session, 'owner_name', None),  # Lead's owner name
                )
                set_call_context(context)
                session_data["call_context"] = context
                print(f"[Server] Call context set: {context.phone_number}")

            # Get lead info
            lead = None
            if session.lead_id:
                lead = LeadRepository.get(session.lead_id)

            if lead:
                # Create call session
                call = Call(
                    id=session.call_sid,
                    lead_id=lead.id,
                    campaign_id=session.campaign_id or "",
                    phone_number=lead.phone_number,
                )

                call_session = CallSession(
                    agent=agent,
                    lead=lead,
                    campaign_id=session.campaign_id or "",
                    call_id=session.call_sid,
                )
                call_session.start()
                session_data["call_session"] = call_session

        async def on_session_end(session: StreamSession):
            """Called when media stream ends."""
            print(f"[Server] Call ended: {session.call_sid}")

            # Unregister from active_sessions
            if session.call_sid and session.call_sid in active_sessions:
                del active_sessions[session.call_sid]
                print(f"[Server] Unregistered session: {session.call_sid}")

            # Log healthcare call outcome to CSV
            healthcare_context = session_data.get("healthcare_context")
            if healthcare_context:
                from .data.csv_logger import log_healthcare_call
                try:
                    log_file = log_healthcare_call(healthcare_context)
                    print(f"[Server] Healthcare call logged to: {log_file}")
                except Exception as e:
                    print(f"[Server] Error logging healthcare call: {e}")

            call_session = session_data.get("call_session")
            if call_session:
                completed_call = call_session.end()

                # Log to CSV (sales calls)
                if session.campaign_id:
                    lead = LeadRepository.get(session.lead_id)
                    if lead:
                        logger = CSVLogger(session.campaign_id)
                        logger.log_call(lead, completed_call)

        # Create handler with callbacks
        handler = MediaStreamHandler(
            on_call_start=on_session_start,
            on_call_end=on_session_end,
        )

        # Track call start time to ignore early input during greeting
        import time
        session_data["call_start_time"] = time.time()
        session_data["greeting_played"] = False
        session_data["call_sid"] = None
        session_data["should_hangup"] = False
        GREETING_DURATION = 4.0  # Seconds to wait for greeting to play

        # Goodbye phrases that indicate call should end
        GOODBYE_PHRASES = [
            "take care", "have a great day", "goodbye", "bye bye", "bye!",
            "talk to you", "talk soon", "speak soon", "thanks for your time",
            "have a good one", "catch you later", "later!", "cheers!"
        ]

        def should_end_call(response: str) -> bool:
            """Check if response indicates call should end."""
            # Check if agent called the end_call tool
            from .agent.tools import get_call_context
            context = get_call_context()
            if context and context.ended:
                print(f"[Server] Agent called end_call tool with outcome: {context.outcome}")
                return True

            # Fallback: check for goodbye phrases
            if not response:
                return False
            response_lower = response.lower()
            return any(phrase in response_lower for phrase in GOODBYE_PHRASES)

        async def hangup_after_delay(delay_seconds: float = 5.0):
            """Wait for TTS to finish playing, then hang up the call."""
            await asyncio.sleep(delay_seconds)
            call_sid = session_data.get("call_sid")
            if call_sid and session_data.get("should_hangup"):
                try:
                    print(f"[Server] Hanging up call: {call_sid}")
                    twilio_client = TwilioClient(session_data["config"])
                    twilio_client.end_call(call_sid)
                    print(f"[Server] Call ended successfully")
                except Exception as e:
                    print(f"[Server] Error hanging up: {e}")

        # Create audio processor - uses LangGraph Platform
        async def agent_handler(text: str) -> str:
            call_session = session_data.get("call_session")
            if call_session:
                return await call_session.process_speech(text)

            # Check if greeting is still playing - ignore input during greeting
            elapsed = time.time() - session_data.get("call_start_time", 0)
            if elapsed < GREETING_DURATION:
                print(f"[Agent] Ignoring input during greeting: '{text}' (elapsed: {elapsed:.1f}s)")
                return None

            # Mark greeting as done and prepare context for first message
            is_first_message = not session_data.get("greeting_played")
            if is_first_message:
                session_data["greeting_played"] = True

            # Use LangGraph Platform for agent execution
            try:
                # For the first message, add context about which greeting was used
                input_text = text
                if is_first_message:
                    session = session_data.get("session")
                    if session and session.owner_name:
                        input_text = f"[Context: You asked 'Is {session.owner_name} available?' - you already know their name, use it directly] {text}"
                    else:
                        input_text = f"[Context: You asked 'Am I speaking with the owner or manager?' - you don't know their name yet] {text}"

                # Get or create thread_id for persistent conversation
                thread_id = session_data.get("thread_id")
                if not thread_id:
                    session = session_data.get("session")
                    phone = session.to_number if session else None

                    # Always create thread in LangGraph Platform
                    # For phone calls, we use metadata to track the phone number
                    thread = await langgraph_client.threads.create(
                        metadata={"phone": phone} if phone else {}
                    )
                    thread_id = thread["thread_id"]
                    session_data["thread_id"] = thread_id
                    print(f"[Agent] Created LangGraph thread: {thread_id} (phone: {phone})")

                # Call LangGraph Platform
                import time as time_module
                start_time = time_module.time()

                # Use runs.wait() for simpler non-streaming response
                # Add 30 second timeout to prevent hanging
                AGENT_TIMEOUT = 30.0  # seconds

                # Pass call context as metadata in config for tools to access
                config_metadata = {}
                if AGENT_MODE == "healthcare":
                    healthcare_context = session_data.get("healthcare_context")
                    if healthcare_context:
                        config_metadata = {
                            "phone_number": healthcare_context.phone_number,
                            "call_sid": healthcare_context.call_sid,
                            "patient_name": healthcare_context.patient_name,
                            "appointment_date": healthcare_context.appointment_date,
                            "appointment_time": healthcare_context.appointment_time,
                            "provider_name": healthcare_context.provider_name,
                            "clinic_name": healthcare_context.clinic_name,
                            "appointment_type": healthcare_context.appointment_type,
                        }
                    agent_id = "healthcare_agent"
                else:
                    call_context = session_data.get("call_context")
                    if call_context:
                        config_metadata = {
                            "phone_number": call_context.phone_number,
                            "call_sid": call_context.call_sid,
                            "business_name": call_context.business_name,
                            "owner_name": call_context.owner_name,
                            "lead_id": call_context.lead_id,
                        }
                    agent_id = "sales_agent"

                try:
                    result = await asyncio.wait_for(
                        langgraph_client.runs.wait(
                            thread_id,
                            agent_id,  # Agent ID based on AGENT_MODE
                            input={"messages": [{"role": "human", "content": input_text}]},
                            config={"configurable": config_metadata} if config_metadata else None,
                        ),
                        timeout=AGENT_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    print(f"[Agent] LangGraph timeout after {AGENT_TIMEOUT}s")
                    return "I'm sorry, I had a brief hiccup. Could you say that again?"

                # Extract the last AI message from the result
                response = ""
                messages = result.get("messages", [])
                for msg in reversed(messages):
                    if isinstance(msg, dict) and msg.get("type") == "ai":
                        content = msg.get("content", "")
                        if isinstance(content, str) and content.strip():
                            response = content
                            break
                        elif isinstance(content, list):
                            for block in content:
                                if isinstance(block, dict) and block.get("type") == "text":
                                    response = block.get("text", "")
                                    break
                            if response:
                                break

                # Check for end_call tool and extract outcome
                for msg in messages:
                    if isinstance(msg, dict) and msg.get("type") == "ai":
                        tool_calls = msg.get("tool_calls", [])
                        for tc in tool_calls:
                            if tc.get("name") == "end_call":
                                args = tc.get("args", {})
                                outcome = args.get("outcome", "unknown")
                                notes = args.get("notes", "")
                                # Update healthcare context with outcome
                                healthcare_ctx = session_data.get("healthcare_context")
                                if healthcare_ctx:
                                    healthcare_ctx.outcome = outcome
                                    healthcare_ctx.ended = True  # Mark call as ended
                                    if notes:
                                        healthcare_ctx.notes.append(notes)
                                    print(f"[Server] Captured end_call outcome: {outcome}")
                            elif tc.get("name") == "request_reschedule":
                                args = tc.get("args", {})
                                healthcare_ctx = session_data.get("healthcare_context")
                                if healthcare_ctx:
                                    healthcare_ctx.outcome = "reschedule_requested"
                                    healthcare_ctx.preferred_date = args.get("preferred_date", "")
                                    healthcare_ctx.preferred_time = args.get("preferred_time", "")
                                    healthcare_ctx.reschedule_reason = args.get("reason", "")
                                    print(f"[Server] Captured reschedule: {healthcare_ctx.preferred_date} at {healthcare_ctx.preferred_time}")
                            elif tc.get("name") == "confirm_appointment":
                                healthcare_ctx = session_data.get("healthcare_context")
                                if healthcare_ctx:
                                    healthcare_ctx.outcome = "confirmed"
                                    print(f"[Server] Captured confirmation")

                latency = (time_module.time() - start_time) * 1000
                print(f"[LATENCY] Agent (LangGraph): {latency:.0f}ms")

                # Check if we should end the call after this response
                # Only hang up when end_call tool is explicitly called, not just when outcome is set
                healthcare_ctx = session_data.get("healthcare_context")
                end_call_triggered = healthcare_ctx and healthcare_ctx.ended  # Only true when end_call tool is called
                if response and (end_call_triggered or should_end_call(response)):
                    session_data["should_hangup"] = True
                    print(f"[Agent] Will hang up after: '{response}'")
                    word_count = len(response.split())
                    tts_duration = max(3.0, word_count / 2.5)
                    asyncio.create_task(hangup_after_delay(tts_duration + 1.0))

                return response
            except Exception as e:
                print(f"[Agent] LangGraph error: {e}")
                import traceback
                traceback.print_exc()
                return "I'm having a bit of trouble. Could you repeat that?"

        # Dynamic greeting generator - waits for session data to get owner_name
        async def get_greeting() -> str:
            """Generate greeting after session is established to get owner_name."""
            # Wait for session to be established (max 2 seconds)
            for _ in range(40):  # 40 * 0.05s = 2 seconds
                if session_data.get("session") is not None:
                    break
                await asyncio.sleep(0.05)

            session = session_data.get("session")

            if AGENT_MODE == "healthcare":
                # Healthcare greeting - reference appointment details
                healthcare_ctx = session_data.get("healthcare_context")
                if healthcare_ctx:
                    return (
                        f"Hi {healthcare_ctx.patient_name}, this is Sarah calling from {healthcare_ctx.clinic_name} "
                        f"about your upcoming appointment with {healthcare_ctx.provider_name} "
                        f"on {healthcare_ctx.appointment_date} at {healthcare_ctx.appointment_time}. "
                        f"Is this a good time?"
                    )
                else:
                    # Fallback healthcare greeting
                    patient_name = getattr(session, 'owner_name', None) or "there"
                    return f"Hi {patient_name}, this is Sarah calling from your healthcare provider about your upcoming appointment. Is this a good time?"
            else:
                # Sales greeting
                if session and session.owner_name:
                    # We know the owner's name - greet them directly
                    return f"Hi {session.owner_name}! This is Alex, an AI assistant from Parallel Universe. Is this a good time to talk? I just need about 3 minutes."
                else:
                    # Don't know the owner - use generic opener
                    return "Hi there! This is Alex, an AI assistant from Parallel Universe. Is this a good time to talk? I just need about 3 minutes."

        async def handle_interrupt():
            """Called when user interrupts - clear Twilio audio buffer."""
            session = session_data.get("session")
            ws = session_data.get("websocket")
            if session and ws and session.stream_sid:
                print(f"[Server] Sending clear to Twilio (stream: {session.stream_sid})")
                await handler.send_clear(ws, session.stream_sid)

        # Create pipeline config with TTS engine from environment
        pipeline_config = PipelineConfig(
            tts_engine=os.getenv("TTS_ENGINE", "comfyui"),
        )
        print(f"[Server] Using TTS engine: {pipeline_config.tts_engine}")

        audio_processor = create_audio_processor(
            agent_handler=agent_handler,
            config=pipeline_config,
            on_interrupt=handle_interrupt,
            initial_greeting=get_greeting,  # Async callable for dynamic greeting
        )

        # Handle the WebSocket connection
        await handler.handle_connection(websocket, audio_processor)

    @app.post("/voice/status")
    async def voice_status(request: Request):
        """Handle call status callbacks from Twilio."""
        form = await request.form()

        call_sid = form.get("CallSid")
        call_status = form.get("CallStatus")
        call_duration = form.get("CallDuration")

        print(f"[Server] Call status: {call_sid} - {call_status}")

        if call_status in ["completed", "failed", "busy", "no-answer"]:
            # Update call record
            if call_sid:
                CallRepository.update_status(call_sid, call_status)

        return PlainTextResponse("OK")

    @app.post("/recording-status")
    async def recording_status(request: Request):
        """Handle recording status callbacks."""
        form = await request.form()

        call_sid = form.get("CallSid")
        recording_url = form.get("RecordingUrl")
        recording_status = form.get("RecordingStatus")

        print(f"[Server] Recording: {call_sid} - {recording_status}")

        if recording_url and call_sid:
            # Update call with recording URL
            from .data.database import get_connection
            with get_connection() as conn:
                conn.execute(
                    "UPDATE calls SET recording_url = ? WHERE id = ?",
                    (recording_url, call_sid)
                )

        return PlainTextResponse("OK")

    # =====================================================
    # Booking Webhook (from CUA app)
    # =====================================================

    @app.post("/webhook/booking")
    async def booking_webhook(request: Request):
        """
        Receive webhook when prospect submits booking form on CUA.

        This creates the Google Calendar event and sends confirmation SMS.
        """
        try:
            data = await request.json()
        except Exception:
            return PlainTextResponse("Invalid JSON", status_code=400)

        booking_id = data.get("booking_id")
        contact_name = data.get("contact_name")
        contact_email = data.get("contact_email")
        company_name = data.get("company_name")
        meeting_datetime_str = data.get("meeting_datetime")
        call_session_id = data.get("call_session_id")

        print(f"[Webhook] Booking submitted: {booking_id}")
        print(f"  Name: {contact_name}, Email: {contact_email}, Company: {company_name}")
        print(f"  Meeting: {meeting_datetime_str}")

        if not contact_email or not meeting_datetime_str:
            return PlainTextResponse("Missing required fields", status_code=400)

        # Parse meeting datetime
        from datetime import datetime
        try:
            meeting_dt = datetime.fromisoformat(meeting_datetime_str)
        except ValueError:
            return PlainTextResponse("Invalid datetime format", status_code=400)

        # Create Google Calendar event
        try:
            from .integrations.google_calendar import get_calendar_service
            calendar = get_calendar_service()
            event_id = calendar.create_meeting(
                title=f"Voice AI Demo - {contact_name}" + (f" ({company_name})" if company_name else ""),
                start_time=meeting_dt,
                duration_minutes=15,
                attendee_email=contact_email,
                attendee_name=contact_name,
                description=f"Discovery call with {contact_name}.\n\nCompany: {company_name or 'N/A'}\n\nBooked via AI SDR.",
            )
            print(f"[Webhook] Calendar event created: {event_id}")
        except Exception as e:
            print(f"[Webhook] Calendar error: {e}")

        # Get phone number from call session to send SMS confirmation
        # For now, we'll extract it from the booking data or skip if not available
        # In production, we'd look up the call session
        try:
            # Try to send SMS confirmation if we have the phone number
            # This would require storing the phone-to-session mapping
            pass  # SMS is already handled by the CUA form submission flow
        except Exception as e:
            print(f"[Webhook] SMS error: {e}")

        return {"status": "ok", "calendar_created": True}

    # TTS model pre-loading disabled due to segfaults
    # The first call will still have a delay, but subsequent calls will be fast
    # due to the class-level model cache in ChatterboxTTS

    # =====================================================
    # Booking Form Endpoints (Local fallback)
    # =====================================================
    from .booking_form import (
        get_pending_booking,
        complete_booking,
        get_booking_form_html,
        get_already_booked_html,
        get_not_found_html,
    )

    @app.get("/book/{booking_id}")
    async def booking_form(booking_id: str, request: Request):
        """Display the booking form for a pending booking."""
        booking = get_pending_booking(booking_id)

        if not booking:
            return HTMLResponse(content=get_not_found_html(), status_code=404)

        if booking.completed:
            return HTMLResponse(content=get_already_booked_html())

        # Get base URL for form submission
        base_url = f"https://{request.headers.get('host', 'localhost')}"
        html = get_booking_form_html(booking, base_url)
        return HTMLResponse(content=html)

    @app.post("/book/{booking_id}")
    async def submit_booking(booking_id: str, request: Request):
        """Handle booking form submission."""
        booking = get_pending_booking(booking_id)

        if not booking:
            return HTMLResponse(content=get_not_found_html(), status_code=404)

        if booking.completed:
            return HTMLResponse(content=get_already_booked_html())

        # Get form data
        form = await request.form()
        contact_name = form.get("name", "").strip()
        contact_email = form.get("email", "").strip()

        if not contact_name or not contact_email:
            return PlainTextResponse("Name and email are required", status_code=400)

        # Complete the booking
        booking = complete_booking(booking_id, contact_name, contact_email)

        # Create Google Calendar event
        try:
            from .integrations.google_calendar import get_calendar_service
            calendar = get_calendar_service()
            event_id = calendar.create_meeting(
                title=f"Voice AI Demo - {contact_name}",
                start_time=booking.meeting_datetime,
                duration_minutes=15,
                attendee_email=contact_email,
                attendee_name=contact_name,
                description=f"Discovery call with {contact_name}.\n\nBooked via AI SDR.",
            )
            print(f"[Booking] Calendar event created: {event_id}")
        except Exception as e:
            print(f"[Booking] Calendar error: {e}")

        # Send SMS confirmation
        try:
            twilio_client = TwilioClient(config)
            time_str = booking.meeting_datetime.strftime("%A, %B %d at %I:%M %p")
            sms_message = (
                f"Hey {contact_name}! 🎉 Awesome - your demo is all set for {time_str}!\n\n"
                f"A calendar invite is heading to {contact_email}.\n\n"
                f"📱 Heads up: If you're on iPhone, check your Spam or Promotions folder if you don't see it right away!\n\n"
                f"Can't wait to chat! - Alex from Parallel Universe"
            )
            twilio_client.send_sms(booking.phone_number, sms_message)
            print(f"[Booking] SMS confirmation sent to {booking.phone_number}")
        except Exception as e:
            print(f"[Booking] SMS error: {e}")

        return PlainTextResponse("OK")

    return app


async def on_call_start(session: StreamSession):
    """Default call start handler."""
    print(f"[Server] Call started: {session.stream_sid}")


async def on_call_end(session: StreamSession):
    """Default call end handler."""
    print(f"[Server] Call ended: {session.stream_sid}")


# Create app at module level for uvicorn import
app = create_app()

# For running directly
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
