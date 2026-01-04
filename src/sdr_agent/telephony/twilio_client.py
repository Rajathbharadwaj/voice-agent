"""
Twilio Client for SDR Agent

Handles outbound call initiation using Twilio's Programmable Voice API.
Uses Media Streams for bidirectional audio streaming.
"""

from typing import Optional
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse, Connect, Stream

from ..config import Config


class TwilioClient:
    """Client for making outbound calls via Twilio."""

    def __init__(self, config: Config):
        self.config = config
        self.client = Client(config.twilio_account_sid, config.twilio_auth_token)
        self.from_number = config.twilio_phone_number

    def make_call(
        self,
        to_number: str,
        webhook_url: str,
        metadata: Optional[dict] = None,
    ) -> str:
        """
        Initiate an outbound call with Media Streams.

        Args:
            to_number: Phone number to call (E.164 format: +14035551234)
            webhook_url: URL for the WebSocket media stream endpoint
            metadata: Optional metadata to pass through (lead_id, campaign_id, etc.)

        Returns:
            Call SID (unique identifier for the call)
        """
        # Include phone numbers in metadata for CallContext
        call_metadata = metadata.copy() if metadata else {}
        call_metadata["from_number"] = self.from_number
        call_metadata["to_number"] = to_number

        # Generate TwiML that connects to our media stream
        twiml = self._generate_stream_twiml(webhook_url, call_metadata)

        # Make the call
        call = self.client.calls.create(
            to=to_number,
            from_=self.from_number,
            twiml=str(twiml),
            # Record the call for later review
            record=True,
            recording_status_callback=f"{webhook_url.rsplit('/', 1)[0]}/recording-status",
        )

        return call.sid

    def _generate_stream_twiml(
        self,
        webhook_url: str,
        metadata: Optional[dict] = None,
    ) -> VoiceResponse:
        """
        Generate TwiML for bidirectional media streaming.

        Uses <Connect><Stream> for real-time audio streaming.
        """
        response = VoiceResponse()

        # Connect to our WebSocket for bidirectional audio
        connect = Connect()

        # Convert HTTP URL to WebSocket URL for media streaming
        # https://example.com/voice/outbound -> wss://example.com/media-stream
        ws_url = webhook_url.replace("https://", "wss://").replace("http://", "ws://")
        # Extract the base URL (protocol + host) and add /media-stream
        parts = ws_url.split("://", 1)
        if len(parts) == 2:
            host = parts[1].split("/")[0]  # Get just the host
            ws_url = f"{parts[0]}://{host}/media-stream"
        else:
            ws_url = ws_url + "/media-stream"

        stream = Stream(url=ws_url)

        # Add metadata as custom parameters
        if metadata:
            for key, value in metadata.items():
                stream.parameter(name=key, value=str(value))

        connect.append(stream)
        response.append(connect)

        return response

    def get_call(self, call_sid: str):
        """Get details about a call."""
        return self.client.calls(call_sid).fetch()

    def end_call(self, call_sid: str):
        """End an in-progress call."""
        return self.client.calls(call_sid).update(status="completed")

    def get_recording(self, call_sid: str):
        """Get recordings for a call."""
        recordings = self.client.recordings.list(call_sid=call_sid)
        if recordings:
            return recordings[0]
        return None

    def send_sms(self, to_number: str, message: str) -> str:
        """
        Send an SMS message.

        Args:
            to_number: Phone number to send to (E.164 format: +14035551234)
            message: The message text to send

        Returns:
            Message SID
        """
        msg = self.client.messages.create(
            to=to_number,
            from_=self.from_number,
            body=message
        )
        return msg.sid


def generate_media_stream_twiml(websocket_url: str, metadata: Optional[dict] = None) -> str:
    """
    Generate TwiML string for media streaming.

    This is useful for returning from a webhook endpoint.
    """
    response = VoiceResponse()
    connect = Connect()
    stream = Stream(url=websocket_url)

    if metadata:
        for key, value in metadata.items():
            stream.parameter(name=key, value=str(value))

    connect.append(stream)
    response.append(connect)

    return str(response)
