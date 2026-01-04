"""
Voice Agent Server

A real-time voice-to-voice AI agent using:
- whisper.cpp for STT
- LangChain + Claude for the agent
- Chatterbox for TTS

Following the LangChain voice agent documentation:
https://docs.langchain.com/oss/python/langchain/voice-agent
"""

import asyncio
import contextlib
from pathlib import Path
from typing import AsyncIterator
from uuid import uuid4

from chatterbox_tts import StreamingChatterboxTTS
from dotenv import load_dotenv
from events import (
    AgentChunkEvent,
    AgentEndEvent,
    ToolCallEvent,
    ToolResultEvent,
    VoiceAgentEvent,
    event_to_dict,
)
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from langchain.agents import create_agent
from langchain.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableGenerator
from langgraph.checkpoint.memory import InMemorySaver
from utils import merge_async_iters
import uvicorn
from whisper_stt import StreamingWhisperSTT

load_dotenv()

# Static files directory
STATIC_DIR = Path(__file__).parent.parent / "static"

app = FastAPI(title="Voice Agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =============================================================================
# Agent Tools
# =============================================================================

def get_current_time() -> str:
    """Get the current time."""
    from datetime import datetime
    return datetime.now().strftime("%I:%M %p")


def get_weather(location: str) -> str:
    """Get the weather for a location (simulated)."""
    return f"The weather in {location} is sunny and 72 degrees."


def search_web(query: str) -> str:
    """Search the web for information (simulated)."""
    return f"Here are the top results for '{query}': [Simulated search results]"


def set_reminder(message: str, time: str) -> str:
    """Set a reminder (simulated)."""
    return f"Reminder set: '{message}' at {time}"


# =============================================================================
# Agent Setup (following LangChain docs)
# =============================================================================

SYSTEM_PROMPT = """You are a helpful voice assistant. You can help with:
- Answering questions
- Getting the current time
- Checking the weather
- Setting reminders
- General conversation

Keep your responses concise and conversational - they will be spoken aloud.
Do NOT use emojis, special characters, or markdown.
Your responses will be read by a text-to-speech engine.
"""

# Create the agent using LangChain's create_agent
# Using the model string format as shown in the docs
agent = create_agent(
    model="anthropic:claude-sonnet-4-20250514",
    tools=[get_current_time, get_weather, search_web, set_reminder],
    system_prompt=SYSTEM_PROMPT,
    checkpointer=InMemorySaver(),
)


# =============================================================================
# Pipeline Stages (following LangChain docs pattern)
# =============================================================================

async def _stt_stream(
    audio_stream: AsyncIterator[bytes],
) -> AsyncIterator[VoiceAgentEvent]:
    """
    Transform stream: Audio (Bytes) -> Voice Events (VoiceAgentEvent)

    Uses a producer-consumer pattern where:
    - Producer: A background task reads audio chunks and sends them to STT
    - Consumer: The main coroutine receives transcription events and yields them
    """
    stt = StreamingWhisperSTT(sample_rate=16000)

    async def send_audio():
        """Background task that processes audio chunks."""
        try:
            async for audio_chunk in audio_stream:
                await stt.add_audio(audio_chunk)
        finally:
            await stt.close()

    # Launch the audio sending task in the background
    send_task = asyncio.create_task(send_audio())

    try:
        # Receive and yield transcription events as they arrive
        async for event in stt.receive_events():
            yield event
    finally:
        # Cleanup
        with contextlib.suppress(asyncio.CancelledError):
            send_task.cancel()
            await send_task


async def _agent_stream(
    event_stream: AsyncIterator[VoiceAgentEvent],
) -> AsyncIterator[VoiceAgentEvent]:
    """
    Transform stream: Voice Events -> Voice Events (with Agent Responses)

    Passes through all upstream events and adds agent response events
    when processing STT transcripts.
    """
    # Generate unique thread ID for conversation memory
    thread_id = str(uuid4())

    async for event in event_stream:
        # Pass through all events to downstream consumers
        yield event

        # When we receive a final transcript, invoke the agent
        if event.type == "stt_output":
            print(f"[Agent] Processing: {event.transcript}")

            # Stream the agent's response using stream_mode="messages"
            stream = agent.astream(
                {"messages": [HumanMessage(content=event.transcript)]},
                {"configurable": {"thread_id": thread_id}},
                stream_mode="messages",
            )

            async for message, metadata in stream:
                # Emit agent chunks (AI messages)
                if isinstance(message, AIMessage):
                    # Use message.text as shown in the docs
                    if hasattr(message, 'text') and message.text:
                        yield AgentChunkEvent.create(message.text)
                    elif message.content:
                        # Fallback to content if text not available
                        if isinstance(message.content, str):
                            yield AgentChunkEvent.create(message.content)
                        elif isinstance(message.content, list):
                            for block in message.content:
                                if isinstance(block, dict) and block.get("type") == "text":
                                    yield AgentChunkEvent.create(block.get("text", ""))

                    # Emit tool calls if present
                    if hasattr(message, "tool_calls") and message.tool_calls:
                        for tool_call in message.tool_calls:
                            yield ToolCallEvent.create(
                                id=tool_call.get("id", str(uuid4())),
                                name=tool_call.get("name", "unknown"),
                                args=tool_call.get("args", {}),
                            )

                # Emit tool results
                if isinstance(message, ToolMessage):
                    yield ToolResultEvent.create(
                        tool_call_id=getattr(message, "tool_call_id", ""),
                        name=getattr(message, "name", "unknown"),
                        result=str(message.content) if message.content else "",
                    )

            # Signal that the agent has finished responding
            yield AgentEndEvent.create()


async def _tts_stream(
    event_stream: AsyncIterator[VoiceAgentEvent],
) -> AsyncIterator[VoiceAgentEvent]:
    """
    Transform stream: Voice Events -> Voice Events (with Audio)

    Uses merge_async_iters to combine two concurrent streams:
    - process_upstream(): Iterates events and sends agent text to TTS
    - tts.receive_events(): Yields audio chunks from TTS
    """
    tts = StreamingChatterboxTTS()

    async def process_upstream() -> AsyncIterator[VoiceAgentEvent]:
        """Process upstream events while sending text to TTS."""
        buffer: list[str] = []

        async for event in event_stream:
            # Pass through all events
            yield event

            # Buffer agent text chunks
            if event.type == "agent_chunk":
                buffer.append(event.text)

            # Send buffered text to TTS when agent finishes
            if event.type == "agent_end" and buffer:
                full_text = "".join(buffer)
                print(f"[TTS] Synthesizing: {full_text[:50]}...")
                await tts.send_text(full_text)
                buffer.clear()

        await tts.close()

    try:
        # Merge upstream events with TTS audio events
        async for event in merge_async_iters(process_upstream(), tts.receive_events()):
            yield event
    finally:
        await tts.close()


# =============================================================================
# Pipeline Composition (using RunnableGenerator as per docs)
# =============================================================================

pipeline = (
    RunnableGenerator(_stt_stream)      # Audio -> STT events
    | RunnableGenerator(_agent_stream)  # STT events -> STT + Agent events
    | RunnableGenerator(_tts_stream)    # All events -> All events + TTS audio
)


# =============================================================================
# WebSocket Endpoint
# =============================================================================

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Handle WebSocket connections for voice streaming."""
    await websocket.accept()
    print("[WebSocket] Client connected")

    async def websocket_audio_stream() -> AsyncIterator[bytes]:
        """Async generator that yields audio bytes from the websocket."""
        try:
            while True:
                data = await websocket.receive_bytes()
                yield data
        except WebSocketDisconnect:
            print("[WebSocket] Client disconnected")

    try:
        # Transform audio through the pipeline
        output_stream = pipeline.atransform(websocket_audio_stream())

        # Process all events, sending them back to the client
        async for event in output_stream:
            try:
                await websocket.send_json(event_to_dict(event))
            except Exception as e:
                print(f"[WebSocket] Error sending event: {e}")
                break

    except WebSocketDisconnect:
        print("[WebSocket] Client disconnected")
    except Exception as e:
        print(f"[WebSocket] Error: {e}")


# =============================================================================
# HTTP Endpoints
# =============================================================================

@app.get("/", response_class=HTMLResponse)
async def root():
    """Serve the main page."""
    html_file = STATIC_DIR / "index.html"
    if html_file.exists():
        return html_file.read_text()
    return """
    <!DOCTYPE html>
    <html>
    <head><title>Voice Agent</title></head>
    <body>
        <h1>Voice Agent</h1>
        <p>Static files not found.</p>
    </body>
    </html>
    """


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok"}


# Mount static files
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
