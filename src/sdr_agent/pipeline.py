"""
SDR Voice Pipeline

Connects Twilio audio to our voice stack:
Audio Input → whisper.cpp (STT) → Sales Agent (Claude) → TTS → Audio Output

The media stream handler converts mulaw ↔ PCM, so this pipeline works with PCM audio.
"""

import asyncio
import struct
import math
import time
import collections
from typing import AsyncIterator, Optional, Callable, Awaitable, Union
from dataclasses import dataclass, field
from pathlib import Path

# VAD States (Vapi-style)
VAD_SILENCE = 0
VAD_STARTING = 1
VAD_SPEAKING = 2
VAD_STOPPING = 3


def calculate_rms(audio_chunk: bytes) -> float:
    """Calculate RMS (root mean square) of PCM audio chunk."""
    if len(audio_chunk) < 2:
        return 0.0
    # Unpack 16-bit PCM samples
    try:
        samples = struct.unpack(f'<{len(audio_chunk)//2}h', audio_chunk)
        if not samples:
            return 0.0
        # Calculate RMS
        sum_squares = sum(s * s for s in samples)
        return math.sqrt(sum_squares / len(samples))
    except struct.error:
        return 0.0


def get_adaptive_threshold(audio_levels: collections.deque, multiplier: float = 1.5) -> float:
    """Get adaptive VAD threshold based on 85th percentile of recent audio levels."""
    if len(audio_levels) < 50:  # Need at least 1 second of audio
        return 500  # Default threshold
    sorted_levels = sorted(audio_levels)
    p85_idx = int(len(sorted_levels) * 0.85)
    baseline = sorted_levels[p85_idx]
    # Threshold is baseline * multiplier, with min/max bounds
    return max(300, min(baseline * multiplier, 2000))

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

# Fix perth watermarker issue before importing chatterbox
try:
    import perth
    if hasattr(perth, 'DummyWatermarker'):
        perth.PerthImplicitWatermarker = perth.DummyWatermarker
except Exception:
    pass  # Skip if perth module structure has changed

from whisper_stt import StreamingWhisperSTT
from events import STTOutputEvent
from sentence_splitter import split_for_tts


@dataclass
class PipelineConfig:
    """Configuration for the voice pipeline."""
    # STT settings
    whisper_cli: Optional[Path] = None
    whisper_model: Optional[Path] = None
    stt_sample_rate: int = 16000
    silence_threshold: int = 500  # RMS threshold for silence detection
    silence_duration: float = 1.0  # Respond faster after silence

    # TTS settings
    # "comfyui" (fast ~1s via ComfyUI) or "kokoro" (~600ms) or "mira" (voice ref) or "chatterbox" (slow)
    tts_engine: str = "comfyui"
    tts_voice: str = "am_adam"  # Kokoro voice (not used by mira)
    tts_model_path: Optional[Path] = None
    tts_device: str = "cuda"
    voice_reference: Optional[str] = None
    chunk_duration_ms: int = 100


def create_tts(config: PipelineConfig):
    """Create TTS engine based on config."""
    if config.tts_engine == "comfyui":
        from comfyui_tts import StreamingComfyUITTS
        return StreamingComfyUITTS(
            chunk_duration_ms=config.chunk_duration_ms,
        )
    elif config.tts_engine == "mira":
        from mira_tts import StreamingMiraTTS
        return StreamingMiraTTS(
            voice_reference=config.voice_reference,
            device=config.tts_device,
            chunk_duration_ms=config.chunk_duration_ms,
        )
    elif config.tts_engine == "kokoro":
        from kokoro_tts import StreamingKokoroTTS
        return StreamingKokoroTTS(
            voice=config.tts_voice,
            chunk_duration_ms=config.chunk_duration_ms,
        )
    else:
        from chatterbox_tts import StreamingChatterboxTTS
        return StreamingChatterboxTTS(
            model_path=config.tts_model_path,
            device=config.tts_device,
            voice_reference=config.voice_reference,
            chunk_duration_ms=config.chunk_duration_ms,
        )


class SDRVoicePipeline:
    """
    Voice pipeline for the SDR agent.

    Processes incoming audio through STT → Agent → TTS and yields output audio.
    """

    def __init__(
        self,
        agent_handler: Callable[[str], Awaitable[str]],
        config: Optional[PipelineConfig] = None,
        on_transcript: Optional[Callable[[str], Awaitable[None]]] = None,
        on_response: Optional[Callable[[str], Awaitable[None]]] = None,
    ):
        """
        Initialize the voice pipeline.

        Args:
            agent_handler: Async function that takes user text and returns agent response
            config: Pipeline configuration
            on_transcript: Callback when user speech is transcribed
            on_response: Callback when agent generates response
        """
        self.config = config or PipelineConfig()
        self.agent_handler = agent_handler
        self.on_transcript = on_transcript
        self.on_response = on_response

        # Initialize STT
        self.stt = StreamingWhisperSTT(
            whisper_cli=self.config.whisper_cli,
            whisper_model=self.config.whisper_model,
            sample_rate=self.config.stt_sample_rate,
            silence_threshold=self.config.silence_threshold,
            silence_duration=self.config.silence_duration,
        )

        # Initialize TTS
        self.tts = create_tts(self.config)

        self._running = False
        self._transcript_buffer: list[str] = []

    async def process_audio(
        self, audio_input: AsyncIterator[bytes]
    ) -> AsyncIterator[bytes]:
        """
        Process audio through the full pipeline.

        Args:
            audio_input: Async iterator of PCM audio chunks (16kHz, 16-bit)

        Yields:
            PCM audio chunks (24kHz, 16-bit) from TTS
        """
        self._running = True

        # Start tasks for STT processing and TTS output
        stt_task = asyncio.create_task(self._process_stt())
        tts_task = asyncio.create_task(self._collect_tts())

        # Feed audio to STT
        try:
            async for audio_chunk in audio_input:
                if not self._running:
                    break
                await self.stt.add_audio(audio_chunk)

            # Signal end of audio
            await self.stt.close()

        except Exception as e:
            print(f"[Pipeline] Audio input error: {e}")
            await self.stt.close()

        # Wait for STT processing to complete
        await stt_task

        # Close TTS and wait for remaining output
        await self.tts.close()

        # Yield all TTS output
        tts_output = await tts_task
        for chunk in tts_output:
            yield chunk

        self._running = False

    async def _process_stt(self):
        """Process STT events and send to agent."""
        async for event in self.stt.receive_events():
            if isinstance(event, STTOutputEvent) and event.transcript:
                transcript = event.transcript.strip()
                if not transcript:
                    continue

                print(f"[Pipeline] User: {transcript}")
                self._transcript_buffer.append(f"User: {transcript}")

                # Callback for transcript
                if self.on_transcript:
                    await self.on_transcript(transcript)

                # Get agent response
                try:
                    response = await self.agent_handler(transcript)
                    if response:
                        print(f"[Pipeline] Agent: {response}")
                        self._transcript_buffer.append(f"Agent: {response}")

                        # Callback for response
                        if self.on_response:
                            await self.on_response(response)

                        # Send to TTS
                        await self.tts.send_text(response)

                except Exception as e:
                    print(f"[Pipeline] Agent error: {e}")

    async def _collect_tts(self) -> list[bytes]:
        """Collect TTS output chunks."""
        chunks = []
        async for event in self.tts.receive_events():
            chunks.append(event.audio)
        return chunks

    def get_transcript(self) -> str:
        """Get the full conversation transcript."""
        return "\n".join(self._transcript_buffer)

    @property
    def tts_sample_rate(self) -> int:
        """Get the TTS output sample rate."""
        return self.tts.sample_rate


class InteractivePipeline:
    """
    Interactive voice pipeline with interruption support.

    Allows the caller to interrupt the agent while it's speaking.
    """

    def __init__(
        self,
        agent_handler: Callable[[str], Awaitable[str]],
        config: Optional[PipelineConfig] = None,
        on_transcript: Optional[Callable[[str], Awaitable[None]]] = None,
        on_response: Optional[Callable[[str], Awaitable[None]]] = None,
        on_interrupt: Optional[Callable[[], Awaitable[None]]] = None,
        initial_greeting: Optional[Union[str, Callable[[], Awaitable[str]]]] = None,
    ):
        self.config = config or PipelineConfig()
        self.agent_handler = agent_handler
        self.on_transcript = on_transcript
        self.on_response = on_response
        self.on_interrupt = on_interrupt
        self.initial_greeting = initial_greeting  # Can be str or async callable

        # Initialize components
        self.stt = StreamingWhisperSTT(
            whisper_cli=self.config.whisper_cli,
            whisper_model=self.config.whisper_model,
            sample_rate=self.config.stt_sample_rate,
            silence_threshold=self.config.silence_threshold,
            silence_duration=self.config.silence_duration,
        )

        self.tts = create_tts(self.config)

        self._running = False
        self._speaking = False
        self._audio_output_queue: asyncio.Queue[Optional[bytes]] = asyncio.Queue()
        self._transcript_buffer: list[str] = []
        self._greeting_cooldown_until = 0  # Timestamp when greeting cooldown ends

        # Initialize turn detector for smarter end-of-utterance detection
        from turn_detector import get_turn_detector
        self._turn_detector = get_turn_detector()

    async def _handle_interrupt(self):
        """Handle user interruption - stop all audio immediately."""
        print("[Pipeline] Handling interrupt - clearing all audio queues")

        # 1. Clear TTS text queue (stop generating more audio)
        if hasattr(self.tts, 'clear_queue'):
            self.tts.clear_queue()

        # 2. Clear audio output queue (stop sending queued chunks to Twilio)
        cleared_chunks = 0
        while not self._audio_output_queue.empty():
            try:
                self._audio_output_queue.get_nowait()
                cleared_chunks += 1
            except asyncio.QueueEmpty:
                break
        if cleared_chunks:
            print(f"[Pipeline] Cleared {cleared_chunks} audio chunks from output queue")

        # 3. Call external interrupt handler (to send Twilio 'clear' event)
        if self.on_interrupt:
            await self.on_interrupt()

    async def process_audio(
        self, audio_input: AsyncIterator[bytes]
    ) -> AsyncIterator[bytes]:
        """
        Process audio with interruption support.

        Args:
            audio_input: Async iterator of PCM audio chunks

        Yields:
            PCM audio chunks from TTS (streamed as generated)
        """
        self._running = True
        self._input_done = False

        # Start background tasks
        stt_task = asyncio.create_task(self._stt_loop())
        tts_task = asyncio.create_task(self._tts_loop())

        # Send initial greeting if configured (for outbound calls)
        GREETING_ECHO_COOLDOWN = 3.0  # Ignore STT for this long after greeting starts

        if self.initial_greeting:
            # Support both static strings and async callables
            if callable(self.initial_greeting):
                greeting = await self.initial_greeting()
            else:
                greeting = self.initial_greeting

            if greeting:
                print(f"[Pipeline] Sending initial greeting: {greeting}")
                self._transcript_buffer.append(f"Agent: {greeting}")
                # Add to turn detector history for accurate context
                self._turn_detector.add_agent_message(greeting)
                if self.on_response:
                    await self.on_response(greeting)
                # Set cooldown BEFORE sending TTS to catch any echo
                self._greeting_cooldown_until = time.time() + GREETING_ECHO_COOLDOWN
                print(f"[Pipeline] Greeting echo cooldown active for {GREETING_ECHO_COOLDOWN}s")
                await self.tts.send_text(greeting)

        # VAD configuration (Vapi-style)
        VAD_INTERRUPT_DURATION = 0.2   # 200ms of voice to trigger interrupt
        VAD_CHUNK_DURATION = 0.02      # 20ms per chunk (at 16kHz)

        # Task to feed audio to STT with VAD-based interrupt detection
        async def feed_stt():
            # VAD state
            vad_state = VAD_SILENCE
            voice_start_time = 0
            audio_levels = collections.deque(maxlen=1500)  # 30s rolling window
            vad_triggered = False

            try:
                async for audio_chunk in audio_input:
                    if not self._running:
                        break

                    current_time = time.time()

                    # Calculate RMS for this chunk
                    rms = calculate_rms(audio_chunk)
                    audio_levels.append(rms)

                    # Get adaptive threshold
                    threshold = get_adaptive_threshold(audio_levels)

                    # Skip VAD during greeting cooldown (prevents echo triggering)
                    in_cooldown = self._greeting_cooldown_until > 0 and current_time < self._greeting_cooldown_until

                    # Only run VAD if agent is speaking and not in cooldown
                    if self._speaking and not in_cooldown:
                        # VAD State Machine
                        if vad_state == VAD_SILENCE:
                            if rms > threshold:
                                vad_state = VAD_STARTING
                                voice_start_time = current_time

                        elif vad_state == VAD_STARTING:
                            if rms > threshold:
                                # Check if voice duration exceeds interrupt threshold
                                voice_duration = current_time - voice_start_time
                                if voice_duration >= VAD_INTERRUPT_DURATION:
                                    vad_state = VAD_SPEAKING
                                    if not vad_triggered:
                                        vad_triggered = True
                                        print(f"[VAD] Interrupt detected! Voice for {voice_duration*1000:.0f}ms (threshold: {threshold:.0f})")
                                        # Trigger interrupt immediately - don't wait for STT
                                        await self._handle_interrupt()
                                        self._speaking = False
                            else:
                                # Voice stopped before threshold - false start
                                vad_state = VAD_SILENCE

                        elif vad_state == VAD_SPEAKING:
                            if rms < threshold:
                                vad_state = VAD_STOPPING

                        elif vad_state == VAD_STOPPING:
                            if rms > threshold:
                                vad_state = VAD_SPEAKING
                            else:
                                vad_state = VAD_SILENCE
                                vad_triggered = False

                    elif not self._speaking:
                        # Reset VAD state when agent isn't speaking
                        vad_state = VAD_SILENCE
                        vad_triggered = False

                    # Always send audio to STT
                    await self.stt.add_audio(audio_chunk)

                await self.stt.close()
            except Exception as e:
                print(f"[Pipeline] Audio input error: {e}")
                await self.stt.close()
            finally:
                self._input_done = True

        # Start STT feed in background
        feed_task = asyncio.create_task(feed_stt())

        # Yield audio chunks as they're generated (stream them!)
        while True:
            try:
                chunk = await asyncio.wait_for(
                    self._audio_output_queue.get(),
                    timeout=0.1
                )
                if chunk is None:
                    # TTS is done
                    break
                yield chunk
            except asyncio.TimeoutError:
                # Check if we should exit
                if self._input_done and self._audio_output_queue.empty():
                    # Give TTS a moment to finish
                    await asyncio.sleep(0.5)
                    if self._audio_output_queue.empty():
                        break
                continue

        # Cleanup
        await feed_task
        await stt_task
        await self.tts.close()
        await tts_task

        self._running = False

    async def _stt_loop(self):
        """Process STT events with intelligent turn detection."""
        import time

        # Buffer for accumulating incomplete turns
        turn_buffer = []
        last_transcript_time = 0
        buffer_start_time = 0  # When first item was added to buffer

        # Turn detection thresholds (inspired by LiveKit/AssemblyAI best practices)
        SILENCE_FALLBACK_DELAY = 1.2   # Respond after this silence even if EOT is low (was 1.6)
        MAX_BUFFER_AGE = 2.5           # Absolute max time to buffer before forcing response (was 3.0)
        SHORT_INPUT_THRESHOLD = 4      # Word count for "short input" fast-track
        SHORT_INPUT_EOT_THRESHOLD = 0.15  # Lower EOT threshold for short inputs (names, "yes", etc.)
        NO_INPUT_TIMEOUT = 5.0         # Seconds of silence before "are you there?"

        # Track when agent last spoke (for "are you there?" fallback)
        # Timer only starts when TTS finishes playing (_speaking becomes False)
        last_agent_spoke_time = 0  # 0 means "agent still speaking or not started"
        waiting_for_response = False
        no_input_fallback_triggered = False

        # Patterns that indicate silence/no speech - should be ignored
        SILENCE_PATTERNS = [
            "[BLANK_AUDIO]",
            "[BLANK AUDIO]",
            "[ Silence ]",
            "[Silence]",
            "[ silence ]",
            "[silence]",
            "[ Pause ]",
            "[Pause]",
            "...",
            "(silence)",
            "(no speech)",
            "[inaudible]",
        ]

        # Helper to process buffered turn
        async def process_buffered_turn(reason: str = "", is_no_input_followup: bool = False):
            nonlocal turn_buffer, last_transcript_time, buffer_start_time
            nonlocal last_agent_spoke_time, waiting_for_response, no_input_fallback_triggered

            if not turn_buffer and not is_no_input_followup:
                return

            combined_text = " ".join(turn_buffer)
            turn_buffer = []
            last_transcript_time = 0
            buffer_start_time = 0

            log_reason = f" ({reason})" if reason else ""
            print(f"[Pipeline] User (complete){log_reason}: {combined_text}")
            self._transcript_buffer.append(f"User: {combined_text}")

            # Update turn detector with confirmed user message
            self._turn_detector.add_user_message(combined_text)

            if self.on_transcript:
                await self.on_transcript(combined_text)

            # Get agent response with latency logging
            try:
                agent_start = time.time()
                response = await self.agent_handler(combined_text)
                agent_latency = (time.time() - agent_start) * 1000
                print(f"[LATENCY] Agent (Claude): {agent_latency:.0f}ms")

                if not response:
                    print(f"[Pipeline] Agent returned empty response!")

                if response:
                    print(f"[Pipeline] Agent: {response}")
                    self._transcript_buffer.append(f"Agent: {response}")

                    # Update turn detector with agent message
                    self._turn_detector.add_agent_message(response)

                    if self.on_response:
                        await self.on_response(response)

                    self._speaking = True

                    # Split response into sentences for progressive TTS
                    # First sentence starts generating immediately while others queue
                    chunks = split_for_tts(response)
                    tts_start = time.time()

                    for i, chunk in enumerate(chunks):
                        await self.tts.send_text(chunk)
                        if i == 0:
                            first_chunk_latency = (time.time() - tts_start) * 1000
                            print(f"[LATENCY] TTS first chunk queued: {first_chunk_latency:.0f}ms ({len(chunk)} chars)")

                    total_latency = (time.time() - tts_start) * 1000
                    print(f"[LATENCY] TTS all {len(chunks)} chunks queued: {total_latency:.0f}ms")

                    # Mark that we're waiting for user response
                    # Timer starts when _speaking becomes False (TTS finishes playing)
                    last_agent_spoke_time = 0  # Will be set when TTS finishes
                    waiting_for_response = True
                    no_input_fallback_triggered = False

            except Exception as e:
                print(f"[Pipeline] Agent error: {e}")

        # Background task to flush buffer after silence timeout OR max buffer age
        async def silence_checker():
            nonlocal turn_buffer, last_transcript_time, buffer_start_time
            nonlocal waiting_for_response, no_input_fallback_triggered, last_agent_spoke_time

            while self._running:
                await asyncio.sleep(0.3)  # Check every 300ms
                current_time = time.time()

                # Check for buffered turn that needs processing
                if turn_buffer and last_transcript_time > 0:
                    silence_duration = current_time - last_transcript_time
                    buffer_age = current_time - buffer_start_time if buffer_start_time > 0 else 0

                    # Trigger if: silence timeout OR buffer has been accumulating too long
                    if silence_duration >= SILENCE_FALLBACK_DELAY:
                        print(f"[TurnDetector] Silence fallback after {silence_duration:.1f}s")
                        await process_buffered_turn("silence fallback")
                    elif buffer_age >= MAX_BUFFER_AGE:
                        print(f"[TurnDetector] Buffer age fallback after {buffer_age:.1f}s")
                        await process_buffered_turn("buffer age fallback")

                # "Are you there?" fallback - only when agent FINISHED speaking and waiting for user
                # Key: self._speaking is False means TTS finished playing
                elif waiting_for_response and not self._speaking and not no_input_fallback_triggered and not turn_buffer:
                    # Only start counting silence AFTER agent stops speaking
                    if last_agent_spoke_time == 0:
                        # Agent just finished speaking, start the timer now
                        last_agent_spoke_time = current_time
                        continue

                    time_since_agent_stopped = current_time - last_agent_spoke_time
                    if time_since_agent_stopped >= NO_INPUT_TIMEOUT:
                        print(f"[Pipeline] No user input for {time_since_agent_stopped:.1f}s after agent stopped - triggering follow-up")
                        no_input_fallback_triggered = True

                        # Send a follow-up message to prompt the user
                        followup = "Hey, are you still there?"
                        print(f"[Pipeline] Agent (follow-up): {followup}")
                        self._transcript_buffer.append(f"Agent: {followup}")
                        self._turn_detector.add_agent_message(followup)

                        if self.on_response:
                            await self.on_response(followup)

                        self._speaking = True
                        await self.tts.send_text(followup)

                        # Reset timer for next potential follow-up
                        last_agent_spoke_time = 0

        # Start silence checker in background
        silence_task = asyncio.create_task(silence_checker())

        try:
            async for event in self.stt.receive_events():
                if isinstance(event, STTOutputEvent) and event.transcript:
                    transcript = event.transcript.strip()
                    if not transcript:
                        continue

                    # Skip silence indicators - don't treat as user speech
                    if any(pattern.lower() in transcript.lower() for pattern in SILENCE_PATTERNS):
                        continue

                    current_time = time.time()

                    # Skip transcripts during greeting echo cooldown
                    if self._greeting_cooldown_until > 0 and current_time < self._greeting_cooldown_until:
                        print(f"[Pipeline] Ignoring echo during greeting cooldown: {transcript}")
                        continue

                    # Check for interruption - if agent is speaking
                    if self._speaking:
                        print(f"[Pipeline] Interrupted! User: {transcript}")
                        # Clear all queues and stop Twilio audio
                        await self._handle_interrupt()
                        self._speaking = False
                        # Clear any previous buffer and START with this interrupting statement
                        turn_buffer = [transcript]
                        last_transcript_time = current_time
                        buffer_start_time = current_time  # Track when buffer started
                    else:
                        # Normal case - add to existing buffer
                        if not turn_buffer:
                            buffer_start_time = current_time  # First item in buffer
                        turn_buffer.append(transcript)
                        last_transcript_time = current_time

                    # Combine buffer for turn detection
                    combined_text = " ".join(turn_buffer)
                    word_count = len(combined_text.split())

                    # Use turn detector to check if user is done
                    eot_prob = self._turn_detector.predict_eot(combined_text)

                    # Short input fast-track: use lower threshold for short responses
                    # This helps with names ("My name is Raj"), affirmations ("Yes", "Okay")
                    is_short_input = word_count <= SHORT_INPUT_THRESHOLD
                    effective_threshold = SHORT_INPUT_EOT_THRESHOLD if is_short_input else self._turn_detector.EOT_THRESHOLD

                    is_confident = eot_prob >= effective_threshold
                    threshold_type = "short-input" if is_short_input else "normal"
                    print(f"[TurnDetector] EOT: {eot_prob:.2f} (threshold={effective_threshold:.2f}, {threshold_type}, {word_count} words)")

                    # Reset waiting flag since user responded
                    waiting_for_response = False
                    no_input_fallback_triggered = False

                    if is_confident:
                        # Model confident turn is complete - respond immediately
                        await process_buffered_turn()
                    else:
                        # Turn not complete, wait for more input (or silence fallback)
                        print(f"[Pipeline] Buffering (turn incomplete): {transcript}")
        finally:
            silence_task.cancel()
            try:
                await silence_task
            except asyncio.CancelledError:
                pass

    async def _tts_loop(self):
        """Process TTS events and queue output."""
        async for event in self.tts.receive_events():
            await self._audio_output_queue.put(event.audio)
        await self._audio_output_queue.put(None)
        self._speaking = False

    def get_transcript(self) -> str:
        """Get the full conversation transcript."""
        return "\n".join(self._transcript_buffer)

    @property
    def tts_sample_rate(self) -> int:
        return self.tts.sample_rate


def create_audio_processor(
    agent_handler: Callable[[str], Awaitable[str]],
    config: Optional[PipelineConfig] = None,
    on_transcript: Optional[Callable[[str], Awaitable[None]]] = None,
    on_response: Optional[Callable[[str], Awaitable[None]]] = None,
    on_interrupt: Optional[Callable[[], Awaitable[None]]] = None,
    initial_greeting: Optional[Union[str, Callable[[], Awaitable[str]]]] = None,
) -> Callable[[AsyncIterator[bytes]], AsyncIterator[bytes]]:
    """
    Create an audio processor function for use with MediaStreamHandler.

    Returns a function that takes audio input and yields audio output.
    """
    pipeline = InteractivePipeline(
        agent_handler=agent_handler,
        config=config,
        on_transcript=on_transcript,
        on_response=on_response,
        on_interrupt=on_interrupt,
        initial_greeting=initial_greeting,
    )

    async def processor(audio_input: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
        async for chunk in pipeline.process_audio(audio_input):
            yield chunk

    return processor
