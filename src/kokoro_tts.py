"""
Kokoro Text-to-Speech

Fast, lightweight TTS using Kokoro-ONNX.
Much faster than Chatterbox (~750ms vs 2-4s for typical sentences).
"""

import asyncio
from pathlib import Path
from typing import AsyncIterator, Optional
import time

import numpy as np

from events import TTSChunkEvent

# Model paths
MODEL_DIR = Path("/home/rajathdb/voice-agent")
MODEL_PATH = MODEL_DIR / "kokoro-v1.0.onnx"
VOICES_PATH = MODEL_DIR / "voices-v1.0.bin"

# Default voice - "am" = American Male
DEFAULT_VOICE = "am_adam"


class KokoroTTS:
    """
    Kokoro TTS wrapper.

    Generates audio from text using the Kokoro ONNX model.
    """

    def __init__(
        self,
        voice: str = DEFAULT_VOICE,
        speed: float = 1.0,
    ):
        self.voice = voice
        self.speed = speed
        self._model = None
        self._text_queue: asyncio.Queue[Optional[str]] = asyncio.Queue()
        self._closed = False
        self._sample_rate = 24000  # Kokoro outputs 24kHz

    def _load_model(self):
        """Lazy load the Kokoro model."""
        if self._model is None:
            from kokoro_onnx import Kokoro
            print(f"[TTS] Loading Kokoro model...")
            start = time.time()
            self._model = Kokoro(str(MODEL_PATH), str(VOICES_PATH))
            print(f"[TTS] Kokoro loaded in {time.time()-start:.1f}s")
        return self._model

    async def send_text(self, text: Optional[str]) -> None:
        """Queue text for synthesis."""
        if text and text.strip():
            await self._text_queue.put(text)

    async def close(self) -> None:
        """Signal end of text stream."""
        self._closed = True
        await self._text_queue.put(None)

    async def receive_events(self) -> AsyncIterator[TTSChunkEvent]:
        """Yield audio chunks as they're generated."""
        while True:
            text = await self._text_queue.get()

            if text is None:
                if self._closed:
                    break
                continue

            # Generate audio
            audio = await self._generate(text)
            if audio is not None:
                yield TTSChunkEvent.create(audio)

            if self._closed and self._text_queue.empty():
                break

    async def _generate(self, text: str) -> Optional[bytes]:
        """Generate audio from text."""
        start_time = time.time()

        try:
            # Run synthesis in thread to not block event loop
            samples, sample_rate = await asyncio.to_thread(
                self._synthesize, text
            )

            if samples is None:
                return None

            self._sample_rate = sample_rate

            # Convert to 16-bit PCM bytes
            audio_int16 = (samples * 32767).astype(np.int16)

            latency = (time.time() - start_time) * 1000
            duration = len(audio_int16) / sample_rate
            print(f"[LATENCY] TTS (Kokoro): {latency:.0f}ms for {duration:.1f}s audio")

            return audio_int16.tobytes()

        except Exception as e:
            print(f"[TTS] Kokoro generation error: {e}")
            import traceback
            traceback.print_exc()
            return None

    def _synthesize(self, text: str):
        """Run Kokoro synthesis (blocking)."""
        model = self._load_model()

        try:
            samples, sample_rate = model.create(
                text,
                voice=self.voice,
                speed=self.speed
            )
            return samples, sample_rate
        except Exception as e:
            print(f"[TTS] Kokoro synthesis error: {e}")
            return None, None

    @property
    def sample_rate(self) -> int:
        """Get the output sample rate."""
        return self._sample_rate


class StreamingKokoroTTS:
    """
    Streaming wrapper for Kokoro TTS.

    Breaks generated audio into smaller chunks for progressive playback.
    """

    def __init__(
        self,
        voice: str = DEFAULT_VOICE,
        speed: float = 1.0,
        chunk_duration_ms: int = 100,
    ):
        self.tts = KokoroTTS(voice=voice, speed=speed)
        self.chunk_duration_ms = chunk_duration_ms

    async def send_text(self, text: Optional[str]) -> None:
        await self.tts.send_text(text)

    async def close(self) -> None:
        await self.tts.close()

    async def receive_events(self) -> AsyncIterator[TTSChunkEvent]:
        """Yield audio in smaller chunks for progressive playback."""
        async for event in self.tts.receive_events():
            # Split audio into chunks
            audio_bytes = event.audio
            sample_rate = self.tts.sample_rate

            # Calculate chunk size in bytes (16-bit = 2 bytes per sample)
            chunk_samples = int(sample_rate * self.chunk_duration_ms / 1000)
            chunk_bytes = chunk_samples * 2

            # Yield chunks
            for i in range(0, len(audio_bytes), chunk_bytes):
                chunk = audio_bytes[i:i + chunk_bytes]
                if chunk:
                    yield TTSChunkEvent.create(chunk)

    @property
    def sample_rate(self) -> int:
        return self.tts.sample_rate
