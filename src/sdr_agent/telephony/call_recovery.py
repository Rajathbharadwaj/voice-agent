"""
Call Recovery Handler

Handles unexpected call disconnections and recovery logic.
"""

import asyncio
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional, Callable, Awaitable
from enum import Enum

from ..data.models import Call, CallOutcome, LeadStatus
from ..data.database import CallRepository, LeadRepository


class DisconnectReason(Enum):
    """Reasons for call disconnection."""
    NORMAL_END = "normal_end"  # Call ended normally
    WEBSOCKET_DISCONNECT = "websocket_disconnect"  # WebSocket dropped
    TWILIO_ERROR = "twilio_error"  # Twilio reported an error
    TIMEOUT = "timeout"  # Call timed out
    NETWORK_ERROR = "network_error"  # Network issue
    UNKNOWN = "unknown"


@dataclass
class CallState:
    """Preserved state of a call for recovery."""
    call_id: str
    lead_id: str
    campaign_id: str
    phone_number: str
    started_at: datetime
    transcript_lines: list[str] = field(default_factory=list)
    last_agent_response: Optional[str] = None
    last_user_input: Optional[str] = None
    outcome_so_far: Optional[str] = None
    meeting_info: Optional[dict] = None
    notes: list[str] = field(default_factory=list)
    disconnect_reason: Optional[DisconnectReason] = None
    disconnect_time: Optional[datetime] = None

    @property
    def duration_before_disconnect(self) -> int:
        """Duration in seconds before disconnect."""
        if self.disconnect_time:
            return int((self.disconnect_time - self.started_at).total_seconds())
        return int((datetime.utcnow() - self.started_at).total_seconds())

    @property
    def transcript(self) -> str:
        """Full transcript."""
        return "\n".join(self.transcript_lines)


class CallRecoveryHandler:
    """
    Handles call disconnection and recovery.

    Responsibilities:
    - Detect disconnection type
    - Save partial call state
    - Decide if retry is appropriate
    - Schedule retries
    """

    # Don't retry if call was shorter than this
    MIN_DURATION_FOR_RETRY = 10  # seconds

    # Wait this long before retry
    RETRY_DELAY = 300  # 5 minutes

    # Max retries for a single call attempt
    MAX_RETRIES = 2

    def __init__(
        self,
        on_retry_scheduled: Optional[Callable[[str, datetime], Awaitable[None]]] = None,
    ):
        self.on_retry_scheduled = on_retry_scheduled
        self._active_calls: dict[str, CallState] = {}
        self._retry_counts: dict[str, int] = {}  # lead_id -> retry count

    def register_call(
        self,
        call_id: str,
        lead_id: str,
        campaign_id: str,
        phone_number: str,
    ) -> CallState:
        """Register a new call for monitoring."""
        state = CallState(
            call_id=call_id,
            lead_id=lead_id,
            campaign_id=campaign_id,
            phone_number=phone_number,
            started_at=datetime.utcnow(),
        )
        self._active_calls[call_id] = state
        return state

    def update_transcript(self, call_id: str, line: str):
        """Add a line to the transcript."""
        if call_id in self._active_calls:
            self._active_calls[call_id].transcript_lines.append(line)

    def update_last_exchange(
        self,
        call_id: str,
        user_input: Optional[str] = None,
        agent_response: Optional[str] = None,
    ):
        """Update the last exchange."""
        if call_id in self._active_calls:
            state = self._active_calls[call_id]
            if user_input:
                state.last_user_input = user_input
            if agent_response:
                state.last_agent_response = agent_response

    def update_outcome(self, call_id: str, outcome: str):
        """Update the partial outcome."""
        if call_id in self._active_calls:
            self._active_calls[call_id].outcome_so_far = outcome

    def add_note(self, call_id: str, note: str):
        """Add a note to the call state."""
        if call_id in self._active_calls:
            self._active_calls[call_id].notes.append(note)

    async def handle_disconnect(
        self,
        call_id: str,
        reason: DisconnectReason,
        error_details: Optional[str] = None,
    ) -> Optional[datetime]:
        """
        Handle a call disconnection.

        Args:
            call_id: The call that disconnected
            reason: Why the call disconnected
            error_details: Additional error info

        Returns:
            Scheduled retry time if applicable, None otherwise
        """
        if call_id not in self._active_calls:
            print(f"[Recovery] Unknown call disconnected: {call_id}")
            return None

        state = self._active_calls[call_id]
        state.disconnect_reason = reason
        state.disconnect_time = datetime.utcnow()

        print(f"[Recovery] Call {call_id} disconnected: {reason.value}")

        # Save partial call data to database
        await self._save_partial_call(state, error_details)

        # Determine if we should retry
        should_retry = self._should_retry(state, reason)

        retry_time = None
        if should_retry:
            retry_time = await self._schedule_retry(state)

        # Clean up
        del self._active_calls[call_id]

        return retry_time

    async def handle_normal_end(self, call_id: str, outcome: CallOutcome):
        """Handle a call that ended normally."""
        if call_id in self._active_calls:
            state = self._active_calls[call_id]
            state.disconnect_reason = DisconnectReason.NORMAL_END
            state.outcome_so_far = outcome.value if isinstance(outcome, CallOutcome) else outcome

            # Reset retry count on successful completion
            if state.lead_id in self._retry_counts:
                del self._retry_counts[state.lead_id]

            del self._active_calls[call_id]

    def _should_retry(self, state: CallState, reason: DisconnectReason) -> bool:
        """Determine if a retry is appropriate."""
        # Don't retry normal endings
        if reason == DisconnectReason.NORMAL_END:
            return False

        # Don't retry very short calls (probably wrong number or immediate hangup)
        if state.duration_before_disconnect < self.MIN_DURATION_FOR_RETRY:
            print(f"[Recovery] Call too short for retry: {state.duration_before_disconnect}s")
            return False

        # Check retry count
        current_retries = self._retry_counts.get(state.lead_id, 0)
        if current_retries >= self.MAX_RETRIES:
            print(f"[Recovery] Max retries reached for lead {state.lead_id}")
            return False

        # Don't retry if we detected hostility or do-not-call
        if state.outcome_so_far in ["hostile", "do_not_call", "wrong_number"]:
            return False

        # Don't retry if meeting was already booked (unlikely but possible)
        if state.outcome_so_far == "meeting_booked":
            return False

        # Retry for technical issues
        if reason in [
            DisconnectReason.WEBSOCKET_DISCONNECT,
            DisconnectReason.NETWORK_ERROR,
            DisconnectReason.TIMEOUT,
        ]:
            return True

        return False

    async def _save_partial_call(self, state: CallState, error_details: Optional[str]):
        """Save partial call data to database."""
        # Determine outcome
        outcome = CallOutcome.CALL_FAILED
        if state.outcome_so_far:
            try:
                outcome = CallOutcome(state.outcome_so_far)
            except ValueError:
                outcome = CallOutcome.CALL_FAILED

        ended_reason = f"disconnect:{state.disconnect_reason.value}"
        if error_details:
            ended_reason += f" - {error_details}"

        # Create summary
        summary_parts = [f"Call disconnected: {state.disconnect_reason.value}"]
        if state.notes:
            summary_parts.append(f"Notes: {'; '.join(state.notes[:3])}")
        if state.last_user_input:
            summary_parts.append(f"Last heard: {state.last_user_input[:50]}...")

        try:
            CallRepository.update_completed(
                call_id=state.call_id,
                outcome=outcome,
                duration_seconds=state.duration_before_disconnect,
                ended_reason=ended_reason,
                transcript=state.transcript,
                transcript_summary=" | ".join(summary_parts),
            )
            print(f"[Recovery] Saved partial call data for {state.call_id}")
        except Exception as e:
            print(f"[Recovery] Error saving call data: {e}")

    async def _schedule_retry(self, state: CallState) -> datetime:
        """Schedule a retry for the call."""
        retry_time = datetime.utcnow() + timedelta(seconds=self.RETRY_DELAY)

        # Increment retry count
        self._retry_counts[state.lead_id] = self._retry_counts.get(state.lead_id, 0) + 1

        # Update lead status to indicate retry pending
        try:
            LeadRepository.update_status(state.lead_id, LeadStatus.QUEUED)
        except Exception as e:
            print(f"[Recovery] Error updating lead status: {e}")

        print(f"[Recovery] Retry scheduled for {state.lead_id} at {retry_time}")

        if self.on_retry_scheduled:
            await self.on_retry_scheduled(state.lead_id, retry_time)

        return retry_time

    def get_active_calls(self) -> list[CallState]:
        """Get all active calls being monitored."""
        return list(self._active_calls.values())

    def get_retry_count(self, lead_id: str) -> int:
        """Get retry count for a lead."""
        return self._retry_counts.get(lead_id, 0)


async def handle_twilio_status_callback(
    call_sid: str,
    status: str,
    error_code: Optional[str],
    recovery_handler: CallRecoveryHandler,
) -> Optional[datetime]:
    """
    Handle Twilio status callback and trigger recovery if needed.

    Args:
        call_sid: The Twilio call SID
        status: Call status (completed, failed, busy, no-answer, canceled)
        error_code: Twilio error code if any
        recovery_handler: The recovery handler instance

    Returns:
        Retry time if scheduled
    """
    # Map Twilio status to disconnect reason
    reason_map = {
        "completed": DisconnectReason.NORMAL_END,
        "failed": DisconnectReason.TWILIO_ERROR,
        "busy": DisconnectReason.NORMAL_END,  # Not really an error
        "no-answer": DisconnectReason.NORMAL_END,
        "canceled": DisconnectReason.NORMAL_END,
    }

    reason = reason_map.get(status, DisconnectReason.UNKNOWN)
    error_details = f"Twilio status: {status}"
    if error_code:
        error_details += f", error: {error_code}"

    return await recovery_handler.handle_disconnect(call_sid, reason, error_details)
