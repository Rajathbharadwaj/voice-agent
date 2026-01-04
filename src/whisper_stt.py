"""
Whisper.cpp Speech-to-Text

Uses whisper.cpp CLI for GPU-accelerated transcription.
Adapted from the ASR push_to_talk_sd.py implementation.
"""

import asyncio
import subprocess
import tempfile
import os
from pathlib import Path
from typing import AsyncIterator, Optional

import numpy as np
import soundfile as sf

from events import STTOutputEvent, STTEvent

# Default paths - can be overridden
DEFAULT_WHISPER_CLI = Path("/home/rajathdb/ASR/whisper.cpp/build/bin/whisper-cli")
DEFAULT_WHISPER_MODEL = Path("/home/rajathdb/ASR/whisper.cpp/models/ggml-medium.en.bin")  # medium is more accurate


class WhisperSTT:
    """
    Whisper.cpp STT that processes audio buffers.

    Unlike streaming STT services, whisper.cpp works on complete audio files.
    This class collects audio chunks and transcribes when signaled.
    """

    def __init__(
        self,
        whisper_cli: Optional[Path] = None,
        whisper_model: Optional[Path] = None,
        sample_rate: int = 16000,
    ):
        self.whisper_cli = whisper_cli or DEFAULT_WHISPER_CLI
        self.whisper_model = whisper_model or DEFAULT_WHISPER_MODEL
        self.sample_rate = sample_rate
        self.audio_buffer: list[bytes] = []
        self._transcribe_requested = asyncio.Event()
        self._closed = False

        if not self.whisper_cli.exists():
            raise FileNotFoundError(f"whisper-cli not found at {self.whisper_cli}")
        if not self.whisper_model.exists():
            raise FileNotFoundError(f"Whisper model not found at {self.whisper_model}")

    async def add_audio(self, audio_chunk: bytes) -> None:
        """Add audio chunk to the buffer."""
        if not self._closed:
            self.audio_buffer.append(audio_chunk)

    async def request_transcription(self) -> None:
        """Signal that transcription should happen."""
        self._transcribe_requested.set()

    async def close(self) -> None:
        """Close the STT and request final transcription."""
        self._closed = True
        self._transcribe_requested.set()

    async def receive_events(self) -> AsyncIterator[STTEvent]:
        """
        Yield transcription events.

        Waits for transcription to be requested, then processes the buffer.
        """
        while True:
            await self._transcribe_requested.wait()
            self._transcribe_requested.clear()

            if not self.audio_buffer:
                if self._closed:
                    break
                continue

            # Combine all audio chunks
            audio_data = b''.join(self.audio_buffer)
            self.audio_buffer.clear()

            # Transcribe
            transcript = await self._transcribe(audio_data)

            if transcript:
                yield STTOutputEvent.create(transcript)

            if self._closed:
                break

    async def _transcribe(self, audio_data: bytes) -> str:
        """Run whisper.cpp on the audio data."""
        import time
        start_time = time.time()

        # Convert bytes to numpy array (assuming 16-bit PCM)
        audio_array = np.frombuffer(audio_data, dtype=np.int16)

        # Check minimum duration (0.3 seconds)
        duration = len(audio_array) / self.sample_rate
        if duration < 0.3:
            return ""

        # Save to temp file
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
            temp_file = f.name

        try:
            sf.write(temp_file, audio_array, self.sample_rate)

            # Run whisper.cpp
            result = await asyncio.to_thread(
                subprocess.run,
                [
                    str(self.whisper_cli),
                    "-m", str(self.whisper_model),
                    "-f", temp_file,
                    "-nt",  # No timestamps
                    "-fa",  # Flash attention
                    "--entropy-thold", "2.4",
                    "-ml", "1",  # Max segment length
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=30
            )
            transcript = result.stdout.strip()
            latency = (time.time() - start_time) * 1000
            print(f"[LATENCY] STT (Whisper): {latency:.0f}ms for {duration:.1f}s audio")
            return transcript
        except Exception as e:
            print(f"Transcription error: {e}")
            return ""
        finally:
            os.unlink(temp_file)


class StreamingWhisperSTT:
    """
    A wrapper that provides streaming-like interface for whisper.cpp.

    Uses voice activity detection (VAD) to automatically segment speech.
    When silence is detected, triggers transcription.
    """

    def __init__(
        self,
        whisper_cli: Optional[Path] = None,
        whisper_model: Optional[Path] = None,
        sample_rate: int = 16000,
        silence_threshold: int = 500,  # RMS threshold for silence
        silence_duration: float = 0.8,  # Seconds of silence before transcribing
    ):
        self.whisper = WhisperSTT(whisper_cli, whisper_model, sample_rate)
        self.sample_rate = sample_rate
        self.silence_threshold = silence_threshold
        self.silence_duration = silence_duration
        self._silence_start: Optional[float] = None
        self._has_speech = False

    async def add_audio(self, audio_chunk: bytes) -> None:
        """Add audio and check for silence to trigger transcription."""
        await self.whisper.add_audio(audio_chunk)

        # Check RMS level
        audio_array = np.frombuffer(audio_chunk, dtype=np.int16)
        rms = np.sqrt(np.mean(audio_array.astype(np.float32) ** 2))

        import time
        current_time = time.time()

        if rms < self.silence_threshold:
            # Silence detected
            if self._has_speech:
                if self._silence_start is None:
                    self._silence_start = current_time
                elif current_time - self._silence_start >= self.silence_duration:
                    # Silence long enough, trigger transcription
                    await self.whisper.request_transcription()
                    self._has_speech = False
                    self._silence_start = None
        else:
            # Speech detected
            self._has_speech = True
            self._silence_start = None

    async def close(self) -> None:
        """Close and transcribe any remaining audio."""
        await self.whisper.close()

    async def receive_events(self) -> AsyncIterator[STTEvent]:
        """Yield transcription events."""
        async for event in self.whisper.receive_events():
            yield event
