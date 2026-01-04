"""
Simple SDR Agent Server - For Testing

Sends a greeting immediately when call connects.
Listens to user via whisper.cpp STT, then responds with Chatterbox TTS.
"""

import asyncio
import audioop
import base64
import json
import time
from pathlib import Path

import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import PlainTextResponse

from .config import load_config

# Fix perth watermarker issue - use DummyWatermarker instead
try:
    import perth
    if hasattr(perth, 'DummyWatermarker'):
        perth.PerthImplicitWatermarker = perth.DummyWatermarker
except Exception:
    pass  # Skip if perth module structure has changed

# Global TTS model (loaded once at startup)
_tts_model = None
TTS_SAMPLE_RATE = 24000  # Chatterbox outputs at 24kHz

# Audio settings
TWILIO_SAMPLE_RATE = 8000
STT_SAMPLE_RATE = 16000
SILENCE_THRESHOLD = 600  # RMS threshold (higher to ignore background noise)
SILENCE_DURATION = 1.2  # Seconds of silence before transcribing (longer to reduce interruptions)


def get_tts_model():
    """Get the loaded TTS model."""
    global _tts_model
    if _tts_model is None:
        from chatterbox.tts import ChatterboxTTS
        model_path = Path("/home/rajathdb/ComfyUI/models/tts/chatterbox/resembleai_default_voice")
        print(f"[TTS] Loading Chatterbox model from {model_path}...")
        _tts_model = ChatterboxTTS.from_local(str(model_path), device="cuda")
        print(f"[TTS] Model loaded. Sample rate: {_tts_model.sr}")
    return _tts_model


def simple_response(user_text: str) -> str:
    """Generate a simple response (for testing)."""
    user_lower = user_text.lower().strip()

    if not user_text or len(user_text) < 2:
        return None  # Ignore empty/noise

    # Simple responses for testing
    if any(word in user_lower for word in ["good", "fine", "great", "well"]):
        return "That's great to hear! I'm calling about our voice AI services. Have you considered automating your phone calls?"
    elif any(word in user_lower for word in ["hello", "hi", "hey"]):
        return "Hello! Nice to hear from you. I'm Alex from Voice AI Solutions."
    elif any(word in user_lower for word in ["no", "not interested", "busy"]):
        return "I understand. Thanks for your time today. Have a great day!"
    elif any(word in user_lower for word in ["yes", "sure", "tell me"]):
        return "Excellent! Our voice AI can handle customer calls, book appointments, and answer questions automatically. Would you like to learn more?"
    else:
        return f"I heard you say: {user_text}. How can I help you today?"


def create_simple_app() -> FastAPI:
    """Create a simple test server."""
    app = FastAPI(title="SDR Agent Test")
    config = load_config()

    # Pre-load TTS model at startup
    @app.on_event("startup")
    async def startup():
        print("[Server] Pre-loading TTS model...")
        get_tts_model()
        print("[Server] TTS model ready!")

    @app.get("/")
    async def health():
        return {"status": "ok", "service": "sdr-agent-test"}

    @app.websocket("/media-stream")
    async def media_stream(websocket: WebSocket):
        """Handle Twilio media stream with STT and TTS."""
        await websocket.accept()

        stream_sid = None
        call_sid = None
        audio_buffer = []
        silence_start = None
        has_speech = False
        is_speaking = False  # Are we currently sending TTS?

        print("[Server] WebSocket accepted")

        try:
            while True:
                message = await websocket.receive_text()
                data = json.loads(message)
                event = data.get("event")

                if event == "connected":
                    print("[Server] Twilio connected")

                elif event == "start":
                    start_data = data.get("start", {})
                    stream_sid = start_data.get("streamSid")
                    call_sid = start_data.get("callSid")
                    print(f"[Server] Stream started: {stream_sid}")

                    # Send greeting immediately!
                    greeting = "Hi there! This is Alex from Voice AI. How are you doing today?"
                    print(f"[Server] Sending greeting: {greeting}")

                    is_speaking = True
                    await send_tts_response(websocket, stream_sid, greeting)
                    is_speaking = False

                elif event == "media":
                    # Received audio from caller
                    if is_speaking:
                        continue  # Ignore input while speaking

                    media = data.get("media", {})
                    payload = media.get("payload", "")

                    if payload:
                        # Decode mulaw and convert to PCM
                        mulaw_audio = base64.b64decode(payload)
                        pcm_audio = audioop.ulaw2lin(mulaw_audio, 2)
                        # Upsample 8kHz -> 16kHz for whisper
                        pcm_16k, _ = audioop.ratecv(pcm_audio, 2, 1, TWILIO_SAMPLE_RATE, STT_SAMPLE_RATE, None)
                        audio_buffer.append(pcm_16k)

                        # Voice activity detection
                        audio_array = np.frombuffer(pcm_16k, dtype=np.int16)
                        rms = np.sqrt(np.mean(audio_array.astype(np.float32) ** 2))

                        current_time = time.time()

                        if rms < SILENCE_THRESHOLD:
                            # Silence detected
                            if has_speech:
                                if silence_start is None:
                                    silence_start = current_time
                                elif current_time - silence_start >= SILENCE_DURATION:
                                    # Enough silence, transcribe!
                                    print("[STT] Silence detected, transcribing...")
                                    transcript = await transcribe_audio(audio_buffer)
                                    audio_buffer.clear()
                                    has_speech = False
                                    silence_start = None

                                    if transcript and len(transcript.strip()) > 2:
                                        print(f"[STT] User: {transcript}")
                                        response = simple_response(transcript)
                                        if response:
                                            print(f"[Agent] {response}")
                                            is_speaking = True
                                            await send_tts_response(websocket, stream_sid, response)
                                            is_speaking = False
                        else:
                            # Speech detected
                            has_speech = True
                            silence_start = None

                elif event == "stop":
                    print("[Server] Stream stopped")
                    break

        except WebSocketDisconnect:
            print("[Server] WebSocket disconnected")
        except Exception as e:
            print(f"[Server] Error: {e}")
            import traceback
            traceback.print_exc()

    return app


async def transcribe_audio(audio_buffer: list) -> str:
    """Transcribe audio using whisper.cpp."""
    import subprocess
    import tempfile
    import os
    import soundfile as sf

    if not audio_buffer:
        return ""

    # Combine audio chunks
    audio_data = b''.join(audio_buffer)
    audio_array = np.frombuffer(audio_data, dtype=np.int16)

    # Check minimum duration (0.3 seconds at 16kHz)
    duration = len(audio_array) / STT_SAMPLE_RATE
    if duration < 0.3:
        return ""

    # Save to temp file
    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
        temp_file = f.name

    try:
        sf.write(temp_file, audio_array, STT_SAMPLE_RATE)

        # Run whisper.cpp
        whisper_cli = "/home/rajathdb/ASR/whisper.cpp/build/bin/whisper-cli"
        whisper_model = "/home/rajathdb/ASR/whisper.cpp/models/ggml-medium.en.bin"

        result = await asyncio.to_thread(
            subprocess.run,
            [
                whisper_cli,
                "-m", whisper_model,
                "-f", temp_file,
                "-nt",  # No timestamps
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=30
        )
        return result.stdout.strip()
    except Exception as e:
        print(f"[STT] Error: {e}")
        return ""
    finally:
        os.unlink(temp_file)


async def send_tts_response(websocket: WebSocket, stream_sid: str, text: str):
    """Generate TTS and send to Twilio."""
    try:
        import torch
        import numpy as np

        model = get_tts_model()
        print(f"[TTS] Generating speech for: '{text}'")

        # Generate audio (runs on GPU)
        # Run in thread to not block event loop
        wav = await asyncio.to_thread(model.generate, text)

        print(f"[TTS] Generated audio: shape={wav.shape}, dtype={wav.dtype}")

        # Convert torch tensor to numpy int16 PCM
        audio_np = wav.squeeze().cpu().numpy()
        audio_np = np.clip(audio_np, -1.0, 1.0)
        audio_int16 = (audio_np * 32767).astype(np.int16)
        audio_bytes = audio_int16.tobytes()

        print(f"[TTS] Audio bytes: {len(audio_bytes)} bytes")

        # TTS outputs at 24kHz, Twilio needs 8kHz mulaw
        # Downsample 24kHz -> 8kHz
        audio_8k, _ = audioop.ratecv(audio_bytes, 2, 1, TTS_SAMPLE_RATE, 8000, None)

        # Convert PCM to mulaw
        audio_mulaw = audioop.lin2ulaw(audio_8k, 2)

        print(f"[TTS] Sending {len(audio_mulaw)} bytes of mulaw audio")

        # Send in chunks (Twilio expects ~20ms chunks = 160 bytes at 8kHz)
        chunk_size = 160  # 20ms at 8kHz mulaw
        chunks_sent = 0
        for i in range(0, len(audio_mulaw), chunk_size):
            chunk = audio_mulaw[i:i+chunk_size]
            if chunk:
                payload = base64.b64encode(chunk).decode('ascii')
                message = {
                    "event": "media",
                    "streamSid": stream_sid,
                    "media": {"payload": payload}
                }
                await websocket.send_text(json.dumps(message))
                chunks_sent += 1
                await asyncio.sleep(0.02)  # 20ms between chunks

        print(f"[TTS] Sent {chunks_sent} audio chunks")

    except Exception as e:
        print(f"[TTS] Error: {e}")
        import traceback
        traceback.print_exc()


# For running directly
if __name__ == "__main__":
    import uvicorn
    app = create_simple_app()
    uvicorn.run(app, host="0.0.0.0", port=8080)
