"""
IVR Handler

Detects and navigates Interactive Voice Response (phone menu) systems.
"""

import re
from dataclasses import dataclass
from typing import Optional, Tuple
from enum import Enum


class IVRAction(Enum):
    """Actions to take when IVR is detected."""
    PRESS_DIGIT = "press_digit"
    SAY_SOMETHING = "say_something"
    WAIT = "wait"
    GIVE_UP = "give_up"


@dataclass
class IVRDetectionResult:
    """Result of IVR detection."""
    is_ivr: bool
    action: Optional[IVRAction] = None
    digit: Optional[str] = None  # For PRESS_DIGIT
    phrase: Optional[str] = None  # For SAY_SOMETHING
    confidence: float = 0.0
    reason: str = ""


# Common IVR phrases that indicate a phone menu
IVR_INDICATORS = [
    r"press\s+(\d+|one|two|three|four|five|six|seven|eight|nine|zero|star|pound)",
    r"for\s+\w+[,.]?\s+press",
    r"dial\s+(\d+)",
    r"enter\s+your",
    r"please\s+hold",
    r"your\s+call\s+(is|will\s+be)\s+(important|recorded)",
    r"to\s+speak\s+(to|with)\s+(a|an)\s+(representative|operator|person)",
    r"say\s+(yes|no|representative|operator|agent)",
    r"main\s+menu",
    r"press\s+star",
    r"press\s+pound",
    r"if\s+you\s+know\s+your\s+party",
    r"office\s+hours\s+are",
    r"we\s+are\s+(currently\s+)?(closed|unavailable)",
    r"thank\s+you\s+for\s+calling",
    r"listen\s+carefully\s+(as|because)",
    r"menu\s+(options\s+)?ha(ve|s)\s+changed",
]

# Phrases that indicate we should press 0 for operator
OPERATOR_INDICATORS = [
    r"press\s+(0|zero)\s+(for|to).*(operator|representative|person|someone|assistance)",
    r"(operator|representative|person).*press\s+(0|zero)",
    r"to\s+speak\s+(to|with)\s+(someone|a\s+person).*press\s+(0|zero)",
]

# Phrases where we should just say "representative" or "operator"
VOICE_MENU_INDICATORS = [
    r"say\s+(representative|operator|agent|customer\s+service)",
    r"you\s+can\s+(also\s+)?say",
    r"or\s+say\s+",
]

# Map spoken numbers to digits
SPOKEN_TO_DIGIT = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    "star": "*", "pound": "#", "hash": "#",
}


def detect_ivr(transcript: str) -> IVRDetectionResult:
    """
    Detect if transcript indicates an IVR system.

    Args:
        transcript: The transcribed audio

    Returns:
        IVRDetectionResult with detection info and recommended action
    """
    text = transcript.lower().strip()

    if not text:
        return IVRDetectionResult(is_ivr=False)

    # Check for voice menu (say something)
    for pattern in VOICE_MENU_INDICATORS:
        if re.search(pattern, text):
            return IVRDetectionResult(
                is_ivr=True,
                action=IVRAction.SAY_SOMETHING,
                phrase="representative",
                confidence=0.9,
                reason="Voice menu detected - will say 'representative'"
            )

    # Check for operator option (press 0)
    for pattern in OPERATOR_INDICATORS:
        if re.search(pattern, text):
            return IVRDetectionResult(
                is_ivr=True,
                action=IVRAction.PRESS_DIGIT,
                digit="0",
                confidence=0.95,
                reason="Operator option detected - pressing 0"
            )

    # Check for general IVR indicators
    for pattern in IVR_INDICATORS:
        match = re.search(pattern, text)
        if match:
            # Try to extract the best digit to press
            digit = _extract_best_digit(text)

            if digit:
                return IVRDetectionResult(
                    is_ivr=True,
                    action=IVRAction.PRESS_DIGIT,
                    digit=digit,
                    confidence=0.8,
                    reason=f"IVR detected - pressing {digit}"
                )
            else:
                # IVR detected but no clear action - try 0
                return IVRDetectionResult(
                    is_ivr=True,
                    action=IVRAction.PRESS_DIGIT,
                    digit="0",
                    confidence=0.6,
                    reason="IVR detected - trying 0 for operator"
                )

    return IVRDetectionResult(is_ivr=False)


def _extract_best_digit(text: str) -> Optional[str]:
    """
    Extract the best digit to press from IVR text.

    Priority:
    1. Option for operator/representative/person (usually 0)
    2. Option for appointments/scheduling (for dental clinics, etc.)
    3. Option for sales/general inquiry
    4. Default to 0
    """
    # Look for operator option
    operator_match = re.search(
        r"(operator|representative|speak\s+to\s+(a\s+)?person|assistance).*?press\s+(\d+|zero)",
        text
    )
    if operator_match:
        digit = operator_match.group(3)
        return SPOKEN_TO_DIGIT.get(digit, digit)

    # Also check reverse order
    operator_match = re.search(
        r"press\s+(\d+|zero).*?(operator|representative|person|assistance)",
        text
    )
    if operator_match:
        digit = operator_match.group(1)
        return SPOKEN_TO_DIGIT.get(digit, digit)

    # Look for scheduling/appointments (good for our pitch)
    schedule_match = re.search(
        r"(appointment|schedule|booking).*?press\s+(\d+)",
        text
    )
    if schedule_match:
        return schedule_match.group(2)

    # Look for sales/inquiry
    sales_match = re.search(
        r"(sales|inquiry|information|general).*?press\s+(\d+)",
        text
    )
    if sales_match:
        return sales_match.group(2)

    # Default: look for any "press X" and prefer 0
    all_digits = re.findall(r"press\s+(\d+|zero|one|two|three|four|five|six|seven|eight|nine)", text)
    if all_digits:
        # Prefer 0 if available
        for d in all_digits:
            digit = SPOKEN_TO_DIGIT.get(d, d)
            if digit == "0":
                return "0"
        # Otherwise return first option
        return SPOKEN_TO_DIGIT.get(all_digits[0], all_digits[0])

    return None


def get_ivr_navigation_attempts() -> list[str]:
    """
    Get list of digits to try when navigating IVR.

    Returns digits in order of preference for reaching a human.
    """
    return ["0", "9", "#", "1"]


class IVRNavigator:
    """
    Manages IVR navigation for a call.

    Tracks attempts and decides when to give up.
    """

    MAX_ATTEMPTS = 3

    def __init__(self):
        self.attempts = 0
        self.digits_tried: list[str] = []
        self.in_ivr = False

    def process_transcript(self, transcript: str) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Process transcript and determine if IVR action needed.

        Args:
            transcript: The transcribed audio

        Returns:
            Tuple of (is_ivr, digit_to_press, phrase_to_say)
        """
        result = detect_ivr(transcript)

        if not result.is_ivr:
            if self.in_ivr:
                # We were in IVR but now seem to have reached a person
                print(f"[IVR] Exited IVR after {self.attempts} attempts")
            self.in_ivr = False
            return False, None, None

        self.in_ivr = True
        self.attempts += 1

        if self.attempts > self.MAX_ATTEMPTS:
            print(f"[IVR] Max attempts ({self.MAX_ATTEMPTS}) reached, giving up")
            return True, None, None  # Signal to end call

        if result.action == IVRAction.SAY_SOMETHING:
            return True, None, result.phrase

        if result.action == IVRAction.PRESS_DIGIT:
            digit = result.digit

            # If we've already tried this digit, try next in sequence
            if digit in self.digits_tried:
                alternatives = get_ivr_navigation_attempts()
                for alt in alternatives:
                    if alt not in self.digits_tried:
                        digit = alt
                        break

            self.digits_tried.append(digit)
            print(f"[IVR] Pressing {digit} (attempt {self.attempts})")
            return True, digit, None

        return True, None, None

    def reset(self):
        """Reset for a new call."""
        self.attempts = 0
        self.digits_tried = []
        self.in_ivr = False
