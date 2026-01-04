"""
Call Monitor

Monitors calls for edge cases and takes appropriate action.
Handles voicemail detection, timeouts, audio issues, etc.
"""

import time
from dataclasses import dataclass, field
from typing import Optional, Callable, Awaitable
from enum import Enum

from .prompts import IMMEDIATE_END_TRIGGERS, VOICEMAIL_INDICATORS


class CallIssue(Enum):
    """Types of issues that can occur during a call."""
    NONE = "none"
    VOICEMAIL = "voicemail"
    HOSTILE = "hostile"
    SILENCE_TIMEOUT = "silence_timeout"
    CALL_TOO_LONG = "call_too_long"
    REPEATED_CONFUSION = "repeated_confusion"
    AUDIO_ISSUES = "audio_issues"
    DO_NOT_CALL = "do_not_call"


@dataclass
class CallMetrics:
    """Tracks metrics during a call."""
    start_time: float = field(default_factory=time.time)
    last_speech_time: float = field(default_factory=time.time)
    transcript_count: int = 0
    empty_transcript_count: int = 0
    confusion_count: int = 0  # "what?" "can you repeat?" etc.
    total_words_spoken: int = 0
    agent_turns: int = 0
    user_turns: int = 0

    @property
    def call_duration(self) -> float:
        """Duration in seconds."""
        return time.time() - self.start_time

    @property
    def silence_duration(self) -> float:
        """Time since last speech."""
        return time.time() - self.last_speech_time


class CallMonitor:
    """
    Monitors a call for issues and edge cases.

    Provides real-time detection of:
    - Voicemail
    - Hostile responses / do-not-call requests
    - Prolonged silence
    - Calls running too long
    - Repeated confusion
    - Audio quality issues
    """

    # Thresholds
    MAX_CALL_DURATION = 300  # 5 minutes max
    MAX_SILENCE_DURATION = 30  # 30 seconds of silence
    MAX_CONFUSION_COUNT = 3  # After 3 "what?" we have audio issues
    MAX_EMPTY_TRANSCRIPTS = 5  # Too many empty transcriptions

    def __init__(
        self,
        on_issue_detected: Optional[Callable[[CallIssue, str], Awaitable[None]]] = None,
    ):
        self.metrics = CallMetrics()
        self.on_issue_detected = on_issue_detected
        self.detected_issues: list[CallIssue] = []
        self._ended = False

    def reset(self):
        """Reset for a new call."""
        self.metrics = CallMetrics()
        self.detected_issues = []
        self._ended = False

    async def process_transcript(self, transcript: str, is_user: bool = True) -> Optional[CallIssue]:
        """
        Process a transcript and check for issues.

        Args:
            transcript: The transcribed text
            is_user: True if from user, False if from agent

        Returns:
            Detected issue or None
        """
        if self._ended:
            return None

        self.metrics.last_speech_time = time.time()
        self.metrics.transcript_count += 1

        if is_user:
            self.metrics.user_turns += 1
        else:
            self.metrics.agent_turns += 1

        # Check for empty transcript
        if not transcript or not transcript.strip():
            self.metrics.empty_transcript_count += 1
            if self.metrics.empty_transcript_count >= self.MAX_EMPTY_TRANSCRIPTS:
                return await self._report_issue(CallIssue.AUDIO_ISSUES, "Too many empty transcriptions")
            return None

        transcript_lower = transcript.lower()
        self.metrics.total_words_spoken += len(transcript.split())

        # Check for voicemail (usually in first transcript)
        if self.metrics.transcript_count <= 2:
            if self._is_voicemail(transcript_lower):
                return await self._report_issue(CallIssue.VOICEMAIL, transcript)

        # Check for hostile / do-not-call
        if self._is_hostile(transcript_lower):
            return await self._report_issue(CallIssue.DO_NOT_CALL, transcript)

        # Check for confusion indicators
        if self._is_confusion(transcript_lower):
            self.metrics.confusion_count += 1
            if self.metrics.confusion_count >= self.MAX_CONFUSION_COUNT:
                return await self._report_issue(CallIssue.REPEATED_CONFUSION, "User repeatedly confused")

        return None

    async def check_timeouts(self) -> Optional[CallIssue]:
        """Check for timeout conditions."""
        if self._ended:
            return None

        # Check call duration
        if self.metrics.call_duration > self.MAX_CALL_DURATION:
            return await self._report_issue(
                CallIssue.CALL_TOO_LONG,
                f"Call exceeded {self.MAX_CALL_DURATION}s"
            )

        # Check silence duration (only after call has started)
        if self.metrics.transcript_count > 0:
            if self.metrics.silence_duration > self.MAX_SILENCE_DURATION:
                return await self._report_issue(
                    CallIssue.SILENCE_TIMEOUT,
                    f"No speech for {self.MAX_SILENCE_DURATION}s"
                )

        return None

    def _is_voicemail(self, text: str) -> bool:
        """Check if the transcript indicates voicemail."""
        for indicator in VOICEMAIL_INDICATORS:
            if indicator in text:
                return True
        return False

    def _is_hostile(self, text: str) -> bool:
        """Check if the transcript indicates hostile response."""
        for trigger in IMMEDIATE_END_TRIGGERS:
            if trigger in text:
                return True
        return False

    def _is_confusion(self, text: str) -> bool:
        """Check if user is confused/can't hear."""
        confusion_phrases = [
            "what?",
            "what did you say",
            "can you repeat",
            "say that again",
            "i can't hear",
            "cant hear",
            "sorry?",
            "pardon?",
            "huh?",
            "excuse me?",
        ]
        for phrase in confusion_phrases:
            if phrase in text:
                return True
        return False

    async def _report_issue(self, issue: CallIssue, details: str) -> CallIssue:
        """Report an issue."""
        if issue not in self.detected_issues:
            self.detected_issues.append(issue)
            if self.on_issue_detected:
                await self.on_issue_detected(issue, details)
        return issue

    def mark_ended(self):
        """Mark the call as ended."""
        self._ended = True

    def get_summary(self) -> dict:
        """Get a summary of the call metrics."""
        return {
            "duration_seconds": int(self.metrics.call_duration),
            "transcript_count": self.metrics.transcript_count,
            "agent_turns": self.metrics.agent_turns,
            "user_turns": self.metrics.user_turns,
            "words_spoken": self.metrics.total_words_spoken,
            "confusion_count": self.metrics.confusion_count,
            "issues_detected": [i.value for i in self.detected_issues],
        }


def should_skip_lead(lead_status: str, last_outcome: Optional[str]) -> tuple[bool, str]:
    """
    Check if a lead should be skipped.

    Returns:
        (should_skip, reason)
    """
    # Skip if marked as do-not-call
    if last_outcome == "hostile":
        return True, "Lead requested do-not-call"

    # Skip if wrong number
    if last_outcome == "wrong_number":
        return True, "Wrong number on previous attempt"

    # Skip if already contacted successfully
    if last_outcome == "meeting_booked":
        return True, "Already booked a meeting"

    # Skip if too many failed attempts (could be configurable)
    if lead_status == "failed":
        return True, "Lead marked as failed"

    return False, ""


def get_voicemail_message(business_name: str) -> str:
    """Generate a voicemail message."""
    return (
        f"Hi, this is Alex calling for {business_name} about AI phone answering "
        f"for your business. I'll try back later, or feel free to call us back. "
        f"Thanks and have a great day!"
    )


def get_timeout_message() -> str:
    """Message to say when call is timing out."""
    return (
        "I appreciate your time, but I should let you go. "
        "If you're ever interested in AI phone answering, just give us a call. "
        "Have a great day!"
    )
