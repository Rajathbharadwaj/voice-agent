"""
MiraTTS Text-to-Speech

Ultra-fast TTS (100x realtime, ~100ms latency).
Uses voice cloning with reference audio.
"""

import asyncio
from pathlib import Path
from typing import AsyncIterator, Optional

import torch
import numpy as np

from events import TTSChunkEvent

# Default reference voice path (you can change this)
DEFAULT_VOICE_REFERENCE = Path(__file__).parent / "voices" / "alex_reference.wav"


class MiraTTS:
    """
    MiraTTS wrapper for ultra-fast voice synthesis.

    100x realtime speed, ~100ms latency.
    """

    # Class-level model cache
    _cached_model = None
    _cached_context_tokens = None

    def __init__(
        self,
        model_name: str = "YatharthS/MiraTTS",
        voice_reference: Optional[str] = None,
        device: str = "cuda",
    ):
        self.model_name = model_name
        self.voice_reference = voice_reference or str(DEFAULT_VOICE_REFERENCE)
        self.device = device
        self._model = None
        self._context_tokens = None
        self._text_queue: asyncio.Queue[Optional[str]] = asyncio.Queue()
        self._closed = False
        self._sample_rate = 48000  # MiraTTS outputs 48kHz audio

    def _load_model(self):
        """Lazy load the MiraTTS model."""
        if self._model is None:
            if MiraTTS._cached_model is not None:
                print("[TTS] Using cached MiraTTS model")
                self._model = MiraTTS._cached_model
                self._context_tokens = MiraTTS._cached_context_tokens
            else:
                from mira.model import MiraTTS as MiraModel
                print(f"[TTS] Loading MiraTTS model: {self.model_name}...")
                self._model = MiraModel(self.model_name)

                # Pre-encode voice reference for faster generation
                if self.voice_reference and Path(self.voice_reference).exists():
                    print(f"[TTS] Encoding voice reference: {self.voice_reference}")
                    self._context_tokens = self._model.encode_audio(self.voice_reference)
                else:
                    print("[TTS] Warning: No voice reference found, using default voice")
                    self._context_tokens = None

                # Cache for future instances
                MiraTTS._cached_model = self._model
                MiraTTS._cached_context_tokens = self._context_tokens
                print("[TTS] MiraTTS loaded and cached!")

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
        import time
        start_time = time.time()

        try:
            # Run synthesis in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            audio_np = await loop.run_in_executor(None, self._synthesize, text)

            if audio_np is None:
                return None

            # Convert to 16-bit PCM bytes
            # MiraTTS outputs float32, normalize and convert
            audio_np = np.clip(audio_np, -1.0, 1.0)
            audio_int16 = (audio_np * 32767).astype(np.int16)

            latency = (time.time() - start_time) * 1000
            duration = len(audio_int16) / self._sample_rate
            print(f"[LATENCY] TTS (MiraTTS): {latency:.0f}ms for {duration:.1f}s audio")

            return audio_int16.tobytes()

        except Exception as e:
            print(f"[TTS] MiraTTS generation error: {e}")
            import traceback
            traceback.print_exc()
            return None

    def _synthesize(self, text: str) -> Optional[np.ndarray]:
        """Run MiraTTS synthesis (blocking)."""
        model = self._load_model()

        try:
            if self._context_tokens is None:
                print("[TTS] Error: MiraTTS requires a voice reference file for voice cloning")
                return None

            audio = model.generate(text, self._context_tokens)

            # Convert to numpy array if it's a tensor
            if hasattr(audio, 'numpy'):
                audio = audio.numpy()
            elif hasattr(audio, 'cpu'):
                audio = audio.cpu().numpy()

            # Ensure it's a 1D array
            audio = np.squeeze(audio)

            return audio

        except Exception as e:
            print(f"[TTS] MiraTTS synthesis error: {e}")
            import traceback
            traceback.print_exc()
            return None

    @property
    def sample_rate(self) -> int:
        """MiraTTS outputs 48kHz audio."""
        return self._sample_rate


class StreamingMiraTTS:
    """
    Streaming wrapper for MiraTTS.

    Breaks generated audio into smaller chunks for progressive playback.
    """

    def __init__(
        self,
        model_name: str = "YatharthS/MiraTTS",
        voice_reference: Optional[str] = None,
        device: str = "cuda",
        chunk_duration_ms: int = 100,
    ):
        self.tts = MiraTTS(model_name, voice_reference, device)
        self.chunk_duration_ms = chunk_duration_ms

    async def send_text(self, text: Optional[str]) -> None:
        await self.tts.send_text(text)

    async def close(self) -> None:
        await self.tts.close()

    async def receive_events(self) -> AsyncIterator[TTSChunkEvent]:
        """Yield audio in smaller chunks for progressive playback."""
        async for event in self.tts.receive_events():
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


def preload_model(voice_reference: Optional[str] = None):
    """Pre-load the MiraTTS model to avoid delay on first call."""
    if MiraTTS._cached_model is None:
        print("[TTS] Pre-loading MiraTTS model...")
        from mira.model import MiraTTS as MiraModel
        MiraTTS._cached_model = MiraModel("YatharthS/MiraTTS")

        # Pre-encode voice reference
        if voice_reference and Path(voice_reference).exists():
            print(f"[TTS] Pre-encoding voice reference: {voice_reference}")
            MiraTTS._cached_context_tokens = MiraTTS._cached_model.encode_audio(voice_reference)

        print("[TTS] MiraTTS model pre-loaded!")
