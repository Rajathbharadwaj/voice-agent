"""
ComfyUI TTS - Uses ComfyUI's ChatterboxTTS node via API

Much faster than direct Chatterbox since model stays loaded in GPU memory.
"""

import asyncio
import json
import uuid
import time
from pathlib import Path
from typing import AsyncIterator, Optional

import numpy as np
import requests
import websocket

from events import TTSChunkEvent

COMFY_URL = "http://localhost:8188"
COMFY_OUTPUT_DIR = Path.home() / "ComfyUI" / "output"


class ComfyUITTS:
    """
    TTS using ComfyUI's ChatterboxTTS node.

    Faster than direct Chatterbox because model stays loaded.
    """

    def __init__(
        self,
        comfy_url: str = COMFY_URL,
        output_dir: Optional[Path] = None,
    ):
        self.comfy_url = comfy_url
        self.output_dir = output_dir or COMFY_OUTPUT_DIR
        self._text_queue: asyncio.Queue[Optional[str]] = asyncio.Queue()
        self._closed = False
        self._sample_rate = 24000  # Chatterbox outputs 24kHz

    async def send_text(self, text: Optional[str]) -> None:
        """Queue text for synthesis."""
        if text and text.strip():
            await self._text_queue.put(text)

    async def close(self) -> None:
        """Signal end of text stream."""
        self._closed = True
        await self._text_queue.put(None)

    def clear_queue(self) -> None:
        """Clear all pending text from synthesis queue (for interruption)."""
        cleared = 0
        while not self._text_queue.empty():
            try:
                self._text_queue.get_nowait()
                cleared += 1
            except asyncio.QueueEmpty:
                break
        if cleared:
            print(f"[TTS] Cleared {cleared} pending text chunks")

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
        """Generate audio from text via ComfyUI."""
        start_time = time.time()

        try:
            # Run in thread to not block event loop
            loop = asyncio.get_event_loop()
            audio_bytes = await loop.run_in_executor(None, self._synthesize, text)

            if audio_bytes is None:
                return None

            latency = (time.time() - start_time) * 1000
            duration = len(audio_bytes) / 2 / self._sample_rate  # 16-bit = 2 bytes
            print(f"[LATENCY] TTS (ComfyUI): {latency:.0f}ms for {duration:.1f}s audio")

            return audio_bytes

        except Exception as e:
            print(f"[TTS] ComfyUI generation error: {e}")
            import traceback
            traceback.print_exc()
            return None

    def _synthesize(self, text: str) -> Optional[bytes]:
        """Run ComfyUI synthesis (blocking)."""
        try:
            # Create unique filename prefix
            prefix = f"voice_agent_{uuid.uuid4().hex[:8]}"

            # Workflow with ChatterboxTTS -> SaveAudio
            workflow = {
                "1": {
                    "class_type": "ChatterboxTTS",
                    "inputs": {
                        "model_pack_name": "resembleai_default_voice",
                        "text": text,
                        "max_new_tokens": 1000,
                        "flow_cfg_scale": 0.7,
                        "exaggeration": 0.5,
                        "temperature": 0.8,
                        "cfg_weight": 0.5,
                        "repetition_penalty": 1.2,
                        "min_p": 0.05,
                        "top_p": 1.0,
                        "seed": 0,  # Random seed
                        "use_watermark": False
                    }
                },
                "2": {
                    "class_type": "SaveAudio",
                    "inputs": {
                        "filename_prefix": prefix,
                        "audio": ["1", 0]
                    }
                }
            }

            client_id = str(uuid.uuid4())

            # Submit workflow
            response = requests.post(
                f"{self.comfy_url}/prompt",
                json={"prompt": workflow, "client_id": client_id},
                timeout=30
            )

            if response.status_code != 200:
                print(f"[TTS] ComfyUI error: {response.text}")
                return None

            # Wait for completion via websocket
            ws = websocket.create_connection(
                f"ws://localhost:8188/ws?clientId={client_id}",
                timeout=60
            )

            output_file = None
            while True:
                msg = ws.recv()
                data = json.loads(msg)

                if data.get("type") == "executed":
                    node_data = data.get("data", {})
                    if node_data.get("node") == "2":
                        # Get output filename
                        output = node_data.get("output", {})
                        audio_list = output.get("audio", [])
                        if audio_list:
                            output_file = audio_list[0].get("filename")
                        break

                if data.get("type") == "execution_error":
                    print(f"[TTS] ComfyUI execution error: {data}")
                    break

            ws.close()

            if not output_file:
                print("[TTS] No output file from ComfyUI")
                return None

            # Read the audio file
            audio_path = self.output_dir / output_file
            if not audio_path.exists():
                # Try with subfolder
                audio_path = self.output_dir / "audio" / output_file

            if not audio_path.exists():
                print(f"[TTS] Audio file not found: {audio_path}")
                return None

            # Load audio and convert to 16-bit PCM
            import soundfile as sf
            audio_data, sr = sf.read(str(audio_path))

            # Resample if needed
            if sr != self._sample_rate:
                # Simple resampling
                import scipy.signal
                num_samples = int(len(audio_data) * self._sample_rate / sr)
                audio_data = scipy.signal.resample(audio_data, num_samples)

            # Convert to 16-bit PCM bytes
            if audio_data.ndim > 1:
                audio_data = audio_data.mean(axis=1)  # Mono
            audio_data = np.clip(audio_data, -1.0, 1.0)
            audio_int16 = (audio_data * 32767).astype(np.int16)

            # Clean up temp file
            try:
                audio_path.unlink()
            except:
                pass

            return audio_int16.tobytes()

        except Exception as e:
            print(f"[TTS] ComfyUI synthesis error: {e}")
            import traceback
            traceback.print_exc()
            return None

    @property
    def sample_rate(self) -> int:
        return self._sample_rate


class StreamingComfyUITTS:
    """
    Streaming wrapper for ComfyUI TTS.
    """

    def __init__(
        self,
        comfy_url: str = COMFY_URL,
        chunk_duration_ms: int = 100,
    ):
        self.tts = ComfyUITTS(comfy_url)
        self.chunk_duration_ms = chunk_duration_ms

    async def send_text(self, text: Optional[str]) -> None:
        await self.tts.send_text(text)

    async def close(self) -> None:
        await self.tts.close()

    def clear_queue(self) -> None:
        """Clear pending text queue (for interruption)."""
        self.tts.clear_queue()

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
