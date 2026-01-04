"""
Twilio Media Stream Handler

WebSocket handler for bidirectional audio streaming with Twilio.
Receives audio from the phone call, processes through our voice pipeline,
and sends synthesized audio back.
"""

import asyncio
import audioop
import base64
import json
from typing import AsyncIterator, Optional, Callable, Awaitable
from dataclasses import dataclass

from fastapi import WebSocket, WebSocketDisconnect


@dataclass
class StreamSession:
    """Holds state for a media stream session."""
    stream_sid: str
    call_sid: str
    account_sid: str
    lead_id: Optional[str] = None
    campaign_id: Optional[str] = None
    business_name: Optional[str] = None
    owner_name: Optional[str] = None   # Lead's owner/decision-maker name
    from_number: Optional[str] = None  # Caller's phone number
    to_number: Optional[str] = None    # Called phone number


class MediaStreamHandler:
    """
    Handles Twilio Media Streams WebSocket connection.

    Twilio sends audio as mulaw (G.711 u-law) at 8kHz, mono.
    We convert to PCM for our voice pipeline, then convert back to mulaw for Twilio.
    """

    # Twilio audio format
    TWILIO_SAMPLE_RATE = 8000
    TWILIO_CHANNELS = 1

    # Our pipeline format
    PIPELINE_SAMPLE_RATE = 16000

    def __init__(
        self,
        on_transcript: Optional[Callable[[str, StreamSession], Awaitable[None]]] = None,
        on_call_start: Optional[Callable[[StreamSession], Awaitable[None]]] = None,
        on_call_end: Optional[Callable[[StreamSession], Awaitable[None]]] = None,
    ):
        self.on_transcript = on_transcript
        self.on_call_start = on_call_start
        self.on_call_end = on_call_end

    async def handle_connection(
        self,
        websocket: WebSocket,
        audio_processor: Callable[[AsyncIterator[bytes]], AsyncIterator[bytes]],
    ):
        """
        Handle a Twilio Media Stream WebSocket connection.

        Args:
            websocket: The WebSocket connection from Twilio
            audio_processor: Async generator that takes input audio and yields output audio
        """
        await websocket.accept()

        session: Optional[StreamSession] = None
        audio_queue: asyncio.Queue[bytes] = asyncio.Queue()
        send_queue: asyncio.Queue[bytes] = asyncio.Queue()

        async def receive_from_twilio():
            """Receive audio from Twilio and queue for processing."""
            nonlocal session

            try:
                while True:
                    message = await websocket.receive_text()
                    data = json.loads(message)
                    event = data.get("event")

                    if event == "connected":
                        # Connection established
                        print("[MediaStream] Connected to Twilio")

                    elif event == "start":
                        # Stream started - extract metadata
                        start_data = data.get("start", {})
                        session = StreamSession(
                            stream_sid=start_data.get("streamSid", ""),
                            call_sid=start_data.get("callSid", ""),
                            account_sid=start_data.get("accountSid", ""),
                        )

                        # Extract custom parameters (lead_id, campaign_id, etc.)
                        custom_params = start_data.get("customParameters", {})
                        session.lead_id = custom_params.get("lead_id")
                        session.campaign_id = custom_params.get("campaign_id")
                        session.business_name = custom_params.get("business_name")
                        session.owner_name = custom_params.get("owner_name")
                        session.from_number = custom_params.get("from_number")
                        session.to_number = custom_params.get("to_number")

                        print(f"[MediaStream] Stream started: {session.stream_sid}")

                        if self.on_call_start:
                            await self.on_call_start(session)

                    elif event == "media":
                        # Audio data from Twilio
                        media = data.get("media", {})
                        payload = media.get("payload", "")

                        if payload:
                            # Decode base64 mulaw audio
                            mulaw_audio = base64.b64decode(payload)

                            # Convert mulaw to PCM (16-bit)
                            pcm_audio = audioop.ulaw2lin(mulaw_audio, 2)

                            # Upsample from 8kHz to 16kHz for whisper
                            pcm_upsampled, _ = audioop.ratecv(
                                pcm_audio, 2, 1,
                                self.TWILIO_SAMPLE_RATE,
                                self.PIPELINE_SAMPLE_RATE,
                                None
                            )

                            await audio_queue.put(pcm_upsampled)

                    elif event == "stop":
                        # Stream stopped
                        print("[MediaStream] Stream stopped")
                        await audio_queue.put(None)  # Signal end

                        if self.on_call_end and session:
                            await self.on_call_end(session)
                        break

                    elif event == "mark":
                        # Mark event (audio playback completed)
                        mark_name = data.get("mark", {}).get("name")
                        print(f"[MediaStream] Mark: {mark_name}")

            except WebSocketDisconnect:
                print("[MediaStream] WebSocket disconnected")
                await audio_queue.put(None)

        async def audio_input_stream() -> AsyncIterator[bytes]:
            """Yield audio chunks from the queue."""
            while True:
                chunk = await audio_queue.get()
                if chunk is None:
                    break
                yield chunk

        async def process_and_send():
            """Process audio through pipeline and send back to Twilio."""
            try:
                chunk_count = 0
                async for output_audio in audio_processor(audio_input_stream()):
                    if session and output_audio:
                        chunk_count += 1
                        print(f"[MediaStream] Sending audio chunk {chunk_count}: {len(output_audio)} bytes")
                        await self._send_audio(websocket, session.stream_sid, output_audio)
                print(f"[MediaStream] Done sending audio, total chunks: {chunk_count}")
            except Exception as e:
                print(f"[MediaStream] Processing error: {e}")
                import traceback
                traceback.print_exc()

        # Run receive and process concurrently
        await asyncio.gather(
            receive_from_twilio(),
            process_and_send(),
        )

    async def _send_audio(self, websocket: WebSocket, stream_sid: str, pcm_audio: bytes):
        """
        Send audio back to Twilio.

        Converts PCM audio to mulaw format expected by Twilio.
        Sends in 20ms chunks as Twilio expects.
        """
        # Downsample from TTS sample rate (24kHz) to Twilio's 8kHz
        TTS_SAMPLE_RATE = 24000

        pcm_downsampled, _ = audioop.ratecv(
            pcm_audio, 2, 1,
            TTS_SAMPLE_RATE,
            self.TWILIO_SAMPLE_RATE,
            None
        )

        # Convert PCM to mulaw
        mulaw_audio = audioop.lin2ulaw(pcm_downsampled, 2)

        # Send in 20ms chunks (160 bytes at 8kHz mulaw)
        CHUNK_SIZE = 160  # 20ms at 8kHz
        for i in range(0, len(mulaw_audio), CHUNK_SIZE):
            chunk = mulaw_audio[i:i+CHUNK_SIZE]
            if chunk:
                payload = base64.b64encode(chunk).decode("ascii")
                message = {
                    "event": "media",
                    "streamSid": stream_sid,
                    "media": {
                        "payload": payload
                    }
                }
                await websocket.send_text(json.dumps(message))
                await asyncio.sleep(0.02)  # 20ms between chunks

    async def send_clear(self, websocket: WebSocket, stream_sid: str):
        """Clear the audio buffer (interrupt current playback)."""
        message = {
            "event": "clear",
            "streamSid": stream_sid
        }
        await websocket.send_text(json.dumps(message))

    async def send_mark(self, websocket: WebSocket, stream_sid: str, name: str):
        """Send a mark event to track playback position."""
        message = {
            "event": "mark",
            "streamSid": stream_sid,
            "mark": {
                "name": name
            }
        }
        await websocket.send_text(json.dumps(message))


class DTMFSender:
    """
    Sends DTMF tones to an in-progress Twilio call.

    Uses the Twilio REST API since Media Streams don't support DTMF directly.
    """

    def __init__(self, account_sid: str, auth_token: str):
        from twilio.rest import Client
        self.client = Client(account_sid, auth_token)

    def send_dtmf(self, call_sid: str, digits: str) -> bool:
        """
        Send DTMF tones to a call.

        Args:
            call_sid: The Twilio call SID
            digits: Digits to send (0-9, *, #, w for wait)

        Returns:
            True if successful
        """
        try:
            # Use TwiML to play DTMF tones
            # We update the call with new TwiML that plays the tones
            # then reconnects to our stream
            twiml = f'''
            <Response>
                <Play digits="{digits}"/>
                <Pause length="1"/>
                <Connect>
                    <Stream url="wss://your-server/media-stream"/>
                </Connect>
            </Response>
            '''

            # Alternative: Use the Calls resource to send digits
            # This is simpler and doesn't interrupt the stream
            self.client.calls(call_sid).update(
                twiml=f'<Response><Play digits="{digits}w"/></Response>'
            )

            print(f"[DTMF] Sent digits '{digits}' to call {call_sid}")
            return True

        except Exception as e:
            print(f"[DTMF] Error sending digits: {e}")
            return False

    async def send_dtmf_async(self, call_sid: str, digits: str) -> bool:
        """Async wrapper for send_dtmf."""
        import asyncio
        return await asyncio.to_thread(self.send_dtmf, call_sid, digits)
