"""
Orpheus Text-to-Speech with vLLM

Ultra-low latency TTS using Orpheus (Llama-3B backbone) with vLLM for efficient inference.
Supports true streaming - yields audio chunks as they're generated (~100-200ms to first chunk).
"""

import asyncio
from typing import AsyncIterator, Optional
import time

import numpy as np

from events import TTSChunkEvent

# Available voices
ORPHEUS_VOICES = {
    "tara": 0,    # Female, warm
    "leo": 1,     # Male, professional
    "aria": 2,    # Female, energetic
    "jason": 3,   # Male, casual
    "john": 4,    # Male, authoritative
}

DEFAULT_VOICE = "tara"
DEFAULT_MODEL = "canopylabs/orpheus-tts-0.1-finetune-prod"

# Module-level singleton for the loaded model (shared across all instances)
_shared_model = None
_shared_model_name = None


class OrpheusTTS:
    """
    Orpheus TTS with vLLM backend.

    Uses native streaming to yield audio chunks as they're generated,
    achieving ~100-200ms time-to-first-chunk.
    """

    def __init__(
        self,
        voice: str = DEFAULT_VOICE,
        model_name: str = DEFAULT_MODEL,
    ):
        self.voice = voice if voice in ORPHEUS_VOICES else DEFAULT_VOICE
        self.model_name = model_name
        self._model = None
        self._text_queue: asyncio.Queue[Optional[str]] = asyncio.Queue()
        self._closed = False
        self._sample_rate = 24000  # Orpheus outputs 24kHz

    def _load_model(self):
        """Lazy load the Orpheus model with vLLM (uses module-level singleton)."""
        global _shared_model, _shared_model_name

        # Use shared model if already loaded with same model name
        if _shared_model is not None and _shared_model_name == self.model_name:
            self._model = _shared_model
            return self._model

        if self._model is None:
            from orpheus_tts import OrpheusModel
            import torch
            print(f"[TTS] Loading Orpheus model ({self.model_name})...")
            start = time.time()
            self._model = OrpheusModel(
                model_name=self.model_name,
                dtype=torch.bfloat16,
                max_model_len=2048,  # TTS only needs ~2K tokens, not 128K
                gpu_memory_utilization=0.7,  # Leave ~30% VRAM for Whisper STT
            )
            print(f"[TTS] Orpheus loaded in {time.time()-start:.1f}s")
            # Store in module-level singleton
            _shared_model = self._model
            _shared_model_name = self.model_name
        return self._model

    async def send_text(self, text: Optional[str]) -> None:
        """Queue text for synthesis."""
        if text and text.strip():
            await self._text_queue.put(text)

    async def close(self) -> None:
        """Signal end of text stream."""
        self._closed = True
        await self._text_queue.put(None)

    def clear_queue(self) -> None:
        """Clear pending text (for interruption handling)."""
        cleared = 0
        while not self._text_queue.empty():
            try:
                self._text_queue.get_nowait()
                cleared += 1
            except asyncio.QueueEmpty:
                break
        if cleared:
            print(f"[TTS] Cleared {cleared} items from Orpheus queue")

    async def receive_events(self) -> AsyncIterator[TTSChunkEvent]:
        """Yield audio chunks as they're generated (true streaming)."""
        while True:
            text = await self._text_queue.get()

            if text is None:
                if self._closed:
                    break
                continue

            # Stream audio chunks as they're generated
            async for audio_chunk in self._generate_streaming(text):
                if audio_chunk is not None:
                    yield TTSChunkEvent.create(audio_chunk)

            if self._closed and self._text_queue.empty():
                break

    async def _generate_streaming(self, text: str) -> AsyncIterator[bytes]:
        """Generate audio with true streaming via queue."""
        import queue
        import threading

        # Pre-load model before starting generation (blocking, but only once)
        model = self._load_model()

        start_time = time.time()
        first_chunk = True
        total_samples = 0
        chunk_queue: queue.Queue = queue.Queue()
        error_holder = [None]  # Mutable to capture errors from thread

        def producer():
            """Generate chunks in background thread, push to queue."""
            try:
                for audio_chunk in model.generate_speech(prompt=text, voice=self.voice):
                    chunk_queue.put(audio_chunk)
                chunk_queue.put(None)  # Sentinel
            except Exception as e:
                error_holder[0] = e
                chunk_queue.put(None)

        # Start producer thread
        thread = threading.Thread(target=producer)
        thread.start()

        try:
            while True:
                # Poll queue with small timeout to stay async-friendly
                try:
                    audio_chunk = await asyncio.to_thread(chunk_queue.get, timeout=0.1)
                except queue.Empty:
                    continue

                if audio_chunk is None:
                    break

                if len(audio_chunk) > 0:
                    # Orpheus returns raw bytes already
                    audio_bytes = audio_chunk if isinstance(audio_chunk, bytes) else audio_chunk.tobytes()

                    if first_chunk:
                        latency = (time.time() - start_time) * 1000
                        print(f"[LATENCY] TTS first chunk (Orpheus): {latency:.0f}ms")
                        first_chunk = False

                    total_samples += len(audio_bytes) // 2
                    yield audio_bytes

            thread.join()

            if error_holder[0]:
                raise error_holder[0]

            total_time = (time.time() - start_time) * 1000
            duration = total_samples / self._sample_rate
            print(f"[LATENCY] TTS complete (Orpheus): {total_time:.0f}ms for {duration:.1f}s audio")

        except Exception as e:
            print(f"[TTS] Orpheus generation error: {e}")
            import traceback
            traceback.print_exc()

    @property
    def sample_rate(self) -> int:
        """Get the output sample rate."""
        return self._sample_rate


class StreamingOrpheusTTS:
    """
    Streaming wrapper for Orpheus TTS.

    Note: Orpheus already streams natively, but this wrapper ensures
    consistent chunk sizes for smooth playback over Twilio.
    """

    def __init__(
        self,
        voice: str = DEFAULT_VOICE,
        model_name: str = DEFAULT_MODEL,
        chunk_duration_ms: int = 100,
    ):
        self.tts = OrpheusTTS(voice=voice, model_name=model_name)
        self.chunk_duration_ms = chunk_duration_ms
        self._buffer = b""

    async def send_text(self, text: Optional[str]) -> None:
        await self.tts.send_text(text)

    async def close(self) -> None:
        await self.tts.close()

    def clear_queue(self) -> None:
        self.tts.clear_queue()
        self._buffer = b""

    async def receive_events(self) -> AsyncIterator[TTSChunkEvent]:
        """Yield audio in consistent chunk sizes for smooth playback."""
        sample_rate = self.tts.sample_rate

        # Calculate target chunk size (16-bit = 2 bytes per sample)
        chunk_samples = int(sample_rate * self.chunk_duration_ms / 1000)
        chunk_bytes = chunk_samples * 2

        async for event in self.tts.receive_events():
            # Add to buffer
            self._buffer += event.audio

            # Yield complete chunks
            while len(self._buffer) >= chunk_bytes:
                chunk = self._buffer[:chunk_bytes]
                self._buffer = self._buffer[chunk_bytes:]
                yield TTSChunkEvent.create(chunk)

        # Yield remaining buffer
        if self._buffer:
            yield TTSChunkEvent.create(self._buffer)
            self._buffer = b""

    @property
    def sample_rate(self) -> int:
        return self.tts.sample_rate
