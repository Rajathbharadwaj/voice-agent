"""
Sales Agent

LangChain agent for conducting sales calls.
Uses Claude as the LLM with sales-specific tools.
Supports PostgreSQL checkpointing for persistent conversation memory.
"""

import os
from typing import Optional
from datetime import datetime, timedelta
from langchain.agents import create_agent
from langchain_core.messages import HumanMessage, AIMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.base import BaseCheckpointSaver

from .prompts import SALES_SYSTEM_PROMPT, OPENING_TEMPLATES
from .tools import (
    SALES_TOOLS,
    CallContext,
    set_call_context,
    get_call_context,
    clear_call_context,
)
from ..data.models import Lead, Call, CallOutcome
from ..data.database import CallRepository, LeadRepository, CampaignRepository
from ..thread_mapping import get_thread_mapping_service


# Module-level checkpointer - initialized once, reused across agents
_checkpointer: Optional[BaseCheckpointSaver] = None
_checkpointer_context = None  # Keep context manager alive


def get_checkpointer() -> BaseCheckpointSaver:
    """
    Get or create the checkpointer.
    Uses PostgreSQL if POSTGRES_URI is set, otherwise falls back to MemorySaver.
    """
    global _checkpointer, _checkpointer_context

    if _checkpointer is not None:
        return _checkpointer

    postgres_uri = os.environ.get("POSTGRES_URI")

    if postgres_uri:
        try:
            from langgraph.checkpoint.postgres import PostgresSaver

            # Create PostgresSaver - it manages its own connection pool
            _checkpointer_context = PostgresSaver.from_conn_string(postgres_uri)
            _checkpointer = _checkpointer_context.__enter__()

            # Run setup on first use to create tables
            _checkpointer.setup()
            print(f"[SalesAgent] Using PostgreSQL checkpointer: {postgres_uri[:50]}...")

        except Exception as e:
            print(f"[SalesAgent] PostgreSQL checkpointer failed, falling back to MemorySaver: {e}")
            _checkpointer = MemorySaver()
    else:
        print("[SalesAgent] No POSTGRES_URI set, using in-memory checkpointer")
        _checkpointer = MemorySaver()

    return _checkpointer


class SalesAgent:
    """
    AI Sales Development Representative.

    Handles phone conversations to pitch voice AI services and book meetings.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-4-5-20250929",
        temperature: float = 0.7,
    ):
        self.api_key = api_key
        self.model_name = model
        self.temperature = temperature

        # Create agent using LangChain's create_agent
        # Inject current date/time into prompt with mini calendar
        now = datetime.now()
        current_datetime = now.strftime("%A, %B %d, %Y at %I:%M %p")

        # Build mini calendar for next 7 days
        calendar_lines = ["## CURRENT DATE/TIME & CALENDAR", f"**Today is {current_datetime}**", "", "Upcoming days for scheduling:"]
        for i in range(7):
            future = now + timedelta(days=i)
            day_label = "Today" if i == 0 else "Tomorrow" if i == 1 else future.strftime("%A")
            calendar_lines.append(f"- {day_label}: {future.strftime('%A, %B %d, %Y')}")

        calendar_lines.append("")
        calendar_lines.append("IMPORTANT: Use exact dates from this calendar. Don't guess dates!")
        calendar_context = "\n".join(calendar_lines)

        prompt_with_date = f"{SALES_SYSTEM_PROMPT}\n\n{calendar_context}"

        self.agent = create_agent(
            model=model,
            tools=SALES_TOOLS,
            system_prompt=prompt_with_date,
            checkpointer=get_checkpointer(),
        )

        # Conversation state
        self.chat_history: list = []
        self.business_name: str = ""
        self._context: Optional[CallContext] = None
        self._thread_id: Optional[str] = None

    def start_call(
        self,
        lead: Lead,
        call: Call,
        campaign_id: str,
    ):
        """
        Initialize a new call session.

        Args:
            lead: The lead being called
            call: The call record
            campaign_id: Current campaign ID
        """
        self.business_name = lead.business_name
        self.chat_history = []

        # Get thread_id linked to phone number (persistent across calls)
        mapping_service = get_thread_mapping_service()
        self._thread_id = mapping_service.get_or_create_thread(
            external_id=lead.phone_number,
            external_type="phone",
            call_sid=call.id,
            user_name=lead.owner_name,
        )
        print(f"[SalesAgent] Using thread_id {self._thread_id} for phone {lead.phone_number}")

        # Set up call context for tools
        self._context = CallContext(
            call_id=call.id,
            lead_id=lead.id,
            campaign_id=campaign_id,
            business_name=lead.business_name,
            phone_number=lead.phone_number,
            call_sid=call.id,  # Twilio call SID for booking API
        )
        set_call_context(self._context)

    async def process_test_input(self, user_input: str) -> str:
        """
        Process user input for test calls (no lead info).
        Uses LangChain agent directly.
        """
        if not self._thread_id:
            self._thread_id = f"test_{id(self)}"

        try:
            config = {"configurable": {"thread_id": self._thread_id}}

            response = ""
            async for chunk in self.agent.astream(
                {"messages": [HumanMessage(content=user_input)]},
                config=config,
            ):
                # Check for model response (LangChain returns 'model' key)
                if "model" in chunk:
                    for message in chunk["model"].get("messages", []):
                        if hasattr(message, "content"):
                            content = message.content
                            # Handle string content
                            if isinstance(content, str) and content.strip():
                                response = content
                            # Handle list content (tool calls may have text blocks)
                            elif isinstance(content, list):
                                for block in content:
                                    if isinstance(block, dict) and block.get("type") == "text":
                                        if block.get("text", "").strip():
                                            response = block["text"]
                # Also check for agent key for backwards compatibility
                elif "agent" in chunk:
                    for message in chunk["agent"].get("messages", []):
                        if hasattr(message, "content"):
                            content = message.content
                            if isinstance(content, str) and content.strip():
                                response = content
                            elif isinstance(content, list):
                                for block in content:
                                    if isinstance(block, dict) and block.get("type") == "text":
                                        if block.get("text", "").strip():
                                            response = block["text"]

            return response

        except Exception as e:
            print(f"[SalesAgent] Test call error: {e}")
            import traceback
            traceback.print_exc()
            return "I apologize, I'm having some technical difficulties. Could you repeat that?"

    async def process_test_input_streaming(self, user_input: str):
        """
        Process user input with streaming - yields sentences as they come.
        This allows TTS to start immediately on first sentence.
        """
        import re

        if not self._thread_id:
            self._thread_id = f"test_{id(self)}"

        try:
            config = {"configurable": {"thread_id": self._thread_id}}

            full_response = ""
            yielded_up_to = 0

            async for chunk in self.agent.astream(
                {"messages": [HumanMessage(content=user_input)]},
                config=config,
                stream_mode="messages",
            ):
                # Extract content from streaming chunk
                if hasattr(chunk, 'content') and isinstance(chunk.content, str):
                    full_response = chunk.content
                elif isinstance(chunk, tuple) and len(chunk) >= 2:
                    msg = chunk[0]
                    if hasattr(msg, 'content') and isinstance(msg.content, str):
                        full_response = msg.content

                # Check for complete sentences we haven't yielded yet
                if full_response:
                    # Find sentence boundaries
                    sentences = re.split(r'(?<=[.!?])\s+', full_response)

                    # Yield complete sentences we haven't yielded
                    current_pos = 0
                    for i, sentence in enumerate(sentences[:-1]):  # Skip last (might be incomplete)
                        sentence_end = current_pos + len(sentence) + 1
                        if sentence_end > yielded_up_to:
                            yield sentence.strip()
                            yielded_up_to = sentence_end
                        current_pos = sentence_end

            # Yield any remaining text
            if full_response and yielded_up_to < len(full_response):
                remaining = full_response[yielded_up_to:].strip()
                if remaining:
                    yield remaining

        except Exception as e:
            print(f"[SalesAgent] Streaming error: {e}")
            import traceback
            traceback.print_exc()
            yield "I apologize, I'm having some technical difficulties."

    async def process_input(self, user_input: str) -> str:
        """
        Process user input and generate response.

        Args:
            user_input: What the user said (transcribed)

        Returns:
            Agent's spoken response
        """
        if not self._context:
            return "I'm sorry, there seems to be an error. Goodbye."

        # Check if call has ended
        if self._context.ended:
            return ""

        try:
            # Add context about the business to the input
            context_input = f"[Speaking with {self.business_name}] {user_input}"

            # Run agent with thread_id for conversation memory
            config = {"configurable": {"thread_id": self._thread_id}}

            response = ""
            async for chunk in self.agent.astream(
                {"messages": [HumanMessage(content=context_input)]},
                config=config,
            ):
                # Extract text from the agent response
                if "agent" in chunk:
                    for message in chunk["agent"].get("messages", []):
                        if hasattr(message, "content") and isinstance(message.content, str):
                            response = message.content

            # Update chat history
            self.chat_history.append(HumanMessage(content=user_input))
            self.chat_history.append(AIMessage(content=response))

            return response

        except Exception as e:
            print(f"[SalesAgent] Error: {e}")
            return "I apologize, I'm having some technical difficulties. Could you repeat that?"

    def generate_opening(self) -> str:
        """Generate the opening line for the call."""
        template = OPENING_TEMPLATES[0]  # Could randomize
        return template.format(business_name=self.business_name)

    def end_call(self) -> CallContext:
        """
        End the call and return the context with results.

        Returns:
            CallContext with outcome and collected information
        """
        context = get_call_context()
        clear_call_context()
        self._context = None
        return context

    def get_outcome(self) -> Optional[CallOutcome]:
        """Get the call outcome as a CallOutcome enum."""
        context = get_call_context()
        if not context or not context.outcome:
            return None

        outcome_map = {
            "meeting_booked": CallOutcome.MEETING_BOOKED,
            "interested": CallOutcome.INTERESTED,
            "callback_requested": CallOutcome.CALLBACK_REQUESTED,
            "not_interested": CallOutcome.NOT_INTERESTED,
            "wrong_number": CallOutcome.WRONG_NUMBER,
            "gatekeeper": CallOutcome.GATEKEEPER,
            "voicemail": CallOutcome.VOICEMAIL,
            "hostile": CallOutcome.NOT_INTERESTED,  # Map to not_interested
        }

        return outcome_map.get(context.outcome, CallOutcome.NOT_INTERESTED)


class CallSession:
    """
    Manages a complete call session.

    Ties together the sales agent, lead data, and call recording.
    """

    def __init__(
        self,
        agent: SalesAgent,
        lead: Lead,
        campaign_id: str,
        call_id: str,
    ):
        self.agent = agent
        self.lead = lead
        self.campaign_id = campaign_id
        self.call_id = call_id

        # Create call record
        self.call = Call(
            id=call_id,
            lead_id=lead.id,
            campaign_id=campaign_id,
            phone_number=lead.phone_number,
            status="queued",
        )

        # Transcript
        self.transcript_lines: list[str] = []
        self.started_at: Optional[datetime] = None
        self.ended_at: Optional[datetime] = None

    def start(self):
        """Start the call session."""
        self.started_at = datetime.utcnow()
        self.call.started_at = self.started_at
        self.call.status = "in-progress"

        # Initialize agent
        self.agent.start_call(self.lead, self.call, self.campaign_id)

        # Insert call record
        CallRepository.insert(self.call)
        CallRepository.update_started(self.call_id)

    async def process_speech(self, user_text: str) -> str:
        """Process user speech and get agent response."""
        self.transcript_lines.append(f"User: {user_text}")

        response = await self.agent.process_input(user_text)

        if response:
            self.transcript_lines.append(f"Agent: {response}")

        return response

    def get_opening(self) -> str:
        """Get the opening line."""
        opening = self.agent.generate_opening()
        self.transcript_lines.append(f"Agent: {opening}")
        return opening

    def end(self) -> Call:
        """
        End the call and save results.

        Returns:
            Updated Call record
        """
        self.ended_at = datetime.utcnow()

        # Get context from agent
        context = self.agent.end_call()

        # Calculate duration
        duration = 0
        if self.started_at:
            duration = int((self.ended_at - self.started_at).total_seconds())

        # Get outcome
        outcome = CallOutcome.NOT_INTERESTED  # Default
        if context:
            outcome = self.agent.get_outcome() or CallOutcome.NOT_INTERESTED

        # Full transcript
        transcript = "\n".join(self.transcript_lines)

        # Update call record
        CallRepository.update_completed(
            call_id=self.call_id,
            outcome=outcome,
            duration_seconds=duration,
            ended_reason="completed",
            transcript=transcript,
            transcript_summary=self._summarize_transcript(),
            meeting_time=context.meeting_time if context else None,
            contact_email=context.contact_email if context else None,
            contact_name=context.contact_name if context else None,
        )

        # Update lead
        LeadRepository.update_after_call(self.lead.id, self.call_id, outcome)

        # Update campaign stats
        CampaignRepository.increment_calls(self.campaign_id)
        if outcome == CallOutcome.MEETING_BOOKED:
            CampaignRepository.increment_meetings(self.campaign_id)

        # Return updated call
        return CallRepository.get(self.call_id)

    def _summarize_transcript(self) -> str:
        """Create a brief summary of the call."""
        context = get_call_context()
        if not context:
            return "Call completed"

        parts = []

        if context.outcome:
            parts.append(f"Outcome: {context.outcome}")

        if context.meeting_time:
            parts.append(f"Meeting: {context.meeting_time.strftime('%Y-%m-%d %H:%M')}")

        if context.contact_name:
            parts.append(f"Contact: {context.contact_name}")

        if context.notes:
            parts.append(f"Notes: {'; '.join(context.notes[:3])}")

        return " | ".join(parts) if parts else "Call completed"
