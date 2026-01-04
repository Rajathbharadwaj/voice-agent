"""
Chatterbox Text-to-Speech

Uses Chatterbox TTS for high-quality voice synthesis.
Supports voice cloning with reference audio.
"""

import asyncio
from pathlib import Path
from typing import AsyncIterator, Optional

import torch
import numpy as np

from events import TTSChunkEvent

# Default model path
DEFAULT_MODEL_PATH = Path("/home/rajathdb/ComfyUI/models/tts/chatterbox/resembleai_default_voice")


class ChatterboxTTS:
    """
    Chatterbox TTS wrapper.

    Generates audio from text using the Chatterbox model.
    """

    # Class-level model cache to avoid reloading
    _cached_model = None
    _cached_model_path = None

    def __init__(
        self,
        model_path: Optional[Path] = None,
        device: str = "cuda",
        voice_reference: Optional[str] = None,
    ):
        self.model_path = model_path or DEFAULT_MODEL_PATH
        self.device = device
        self.voice_reference = voice_reference
        self._model = None
        self._text_queue: asyncio.Queue[Optional[str]] = asyncio.Queue()
        self._closed = False

        if not self.model_path.exists():
            raise FileNotFoundError(f"Chatterbox model not found at {self.model_path}")

    def _load_model(self):
        """Lazy load the Chatterbox model (uses class-level cache)."""
        if self._model is None:
            # Check class-level cache first
            if (ChatterboxTTS._cached_model is not None and
                ChatterboxTTS._cached_model_path == str(self.model_path)):
                print(f"[TTS] Using cached Chatterbox model")
                self._model = ChatterboxTTS._cached_model
            else:
                from chatterbox.tts import ChatterboxTTS as CBModel
                print(f"[TTS] Loading Chatterbox model from {self.model_path}...")
                self._model = CBModel.from_local(str(self.model_path), device=self.device)
                # Cache for future instances
                ChatterboxTTS._cached_model = self._model
                ChatterboxTTS._cached_model_path = str(self.model_path)
                print(f"[TTS] Chatterbox loaded and cached.")
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
        """
        Yield audio chunks as they're generated.

        Note: Chatterbox generates complete audio, not streaming chunks.
        We yield the full audio as a single chunk for now.
        For true streaming, we could split the audio into smaller chunks.
        """
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
            # Run synthesis synchronously (threading causes segfaults with diffusers)
            audio_tensor = self._synthesize(text)

            if audio_tensor is None:
                return None

            # Convert to 16-bit PCM bytes
            # Chatterbox outputs float32 tensor, need to convert
            audio_np = audio_tensor.squeeze().cpu().numpy()

            # Normalize and convert to int16
            audio_np = np.clip(audio_np, -1.0, 1.0)
            audio_int16 = (audio_np * 32767).astype(np.int16)

            latency = (time.time() - start_time) * 1000
            duration = len(audio_int16) / self.sample_rate
            print(f"[LATENCY] TTS (Chatterbox): {latency:.0f}ms for {duration:.1f}s audio")

            return audio_int16.tobytes()

        except Exception as e:
            print(f"TTS generation error: {e}")
            return None

    def _synthesize(self, text: str) -> Optional[torch.Tensor]:
        """Run Chatterbox synthesis (blocking)."""
        model = self._load_model()

        try:
            if self.voice_reference:
                wav = model.generate(text, audio_prompt_path=self.voice_reference)
            else:
                wav = model.generate(text)
            return wav
        except Exception as e:
            print(f"Chatterbox synthesis error: {e}")
            return None

    @property
    def sample_rate(self) -> int:
        """Get the model's sample rate."""
        model = self._load_model()
        return model.sr


class StreamingChatterboxTTS:
    """
    Streaming-like wrapper for Chatterbox.

    Breaks generated audio into smaller chunks for progressive playback.
    """

    def __init__(
        self,
        model_path: Optional[Path] = None,
        device: str = "cuda",
        voice_reference: Optional[str] = None,
        chunk_duration_ms: int = 100,  # Size of each audio chunk
    ):
        self.tts = ChatterboxTTS(model_path, device, voice_reference)
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
            chunk_bytes = chunk_samples * 2  # 16-bit audio

            # Yield chunks
            for i in range(0, len(audio_bytes), chunk_bytes):
                chunk = audio_bytes[i:i + chunk_bytes]
                if chunk:
                    yield TTSChunkEvent.create(chunk)

    @property
    def sample_rate(self) -> int:
        return self.tts.sample_rate


def preload_model():
    """Pre-load the Chatterbox model to avoid delay on first call."""
    if ChatterboxTTS._cached_model is None:
        print("[TTS] Pre-loading Chatterbox model...")
        from chatterbox.tts import ChatterboxTTS as CBModel
        ChatterboxTTS._cached_model = CBModel.from_local(str(DEFAULT_MODEL_PATH), device="cuda")
        ChatterboxTTS._cached_model_path = str(DEFAULT_MODEL_PATH)
        print("[TTS] Chatterbox model pre-loaded!")
