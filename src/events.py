"""
Voice Agent Event Types

Events that flow through the voice agent pipeline:
- STT events: partial and final transcripts
- Agent events: response chunks, tool calls
- TTS events: audio chunks
"""

import base64
import time
from dataclasses import dataclass
from typing import Literal, Union


def _now_ms() -> int:
    """Return current Unix timestamp in milliseconds."""
    return int(time.time() * 1000)


@dataclass
class STTChunkEvent:
    """Partial transcript from STT (real-time feedback)."""
    type: Literal["stt_chunk"]
    transcript: str
    ts: int

    @classmethod
    def create(cls, transcript: str) -> "STTChunkEvent":
        return cls(type="stt_chunk", transcript=transcript, ts=_now_ms())


@dataclass
class STTOutputEvent:
    """Final transcript from STT."""
    type: Literal["stt_output"]
    transcript: str
    ts: int

    @classmethod
    def create(cls, transcript: str) -> "STTOutputEvent":
        return cls(type="stt_output", transcript=transcript, ts=_now_ms())


STTEvent = Union[STTChunkEvent, STTOutputEvent]


@dataclass
class AgentChunkEvent:
    """Text chunk from agent response."""
    type: Literal["agent_chunk"]
    text: str
    ts: int

    @classmethod
    def create(cls, text: str) -> "AgentChunkEvent":
        return cls(type="agent_chunk", text=text, ts=_now_ms())


@dataclass
class AgentEndEvent:
    """Agent finished responding."""
    type: Literal["agent_end"]
    ts: int

    @classmethod
    def create(cls) -> "AgentEndEvent":
        return cls(type="agent_end", ts=_now_ms())


@dataclass
class ToolCallEvent:
    """Agent invoked a tool."""
    type: Literal["tool_call"]
    id: str
    name: str
    args: dict
    ts: int

    @classmethod
    def create(cls, id: str, name: str, args: dict) -> "ToolCallEvent":
        return cls(type="tool_call", id=id, name=name, args=args, ts=_now_ms())


@dataclass
class ToolResultEvent:
    """Tool execution result."""
    type: Literal["tool_result"]
    tool_call_id: str
    name: str
    result: str
    ts: int

    @classmethod
    def create(cls, tool_call_id: str, name: str, result: str) -> "ToolResultEvent":
        return cls(type="tool_result", tool_call_id=tool_call_id, name=name, result=result, ts=_now_ms())


AgentEvent = Union[AgentChunkEvent, AgentEndEvent, ToolCallEvent, ToolResultEvent]


@dataclass
class TTSChunkEvent:
    """Audio chunk from TTS."""
    type: Literal["tts_chunk"]
    audio: bytes
    ts: int

    @classmethod
    def create(cls, audio: bytes) -> "TTSChunkEvent":
        return cls(type="tts_chunk", audio=audio, ts=_now_ms())


VoiceAgentEvent = Union[STTEvent, AgentEvent, TTSChunkEvent]


def event_to_dict(event: VoiceAgentEvent) -> dict:
    """Convert event to JSON-serializable dict."""
    if isinstance(event, STTChunkEvent):
        return {"type": event.type, "transcript": event.transcript, "ts": event.ts}
    elif isinstance(event, STTOutputEvent):
        return {"type": event.type, "transcript": event.transcript, "ts": event.ts}
    elif isinstance(event, AgentChunkEvent):
        return {"type": event.type, "text": event.text, "ts": event.ts}
    elif isinstance(event, AgentEndEvent):
        return {"type": event.type, "ts": event.ts}
    elif isinstance(event, ToolCallEvent):
        return {"type": event.type, "id": event.id, "name": event.name, "args": event.args, "ts": event.ts}
    elif isinstance(event, ToolResultEvent):
        return {"type": event.type, "toolCallId": event.tool_call_id, "name": event.name, "result": event.result, "ts": event.ts}
    elif isinstance(event, TTSChunkEvent):
        return {"type": event.type, "audio": base64.b64encode(event.audio).decode("ascii"), "ts": event.ts}
    else:
        raise ValueError(f"Unknown event type: {type(event)}")
