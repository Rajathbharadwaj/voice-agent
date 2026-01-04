"""
Sentence Splitter for TTS Chunking

Splits text into sentences for progressive TTS generation.
First sentence can start generating while others queue.
"""

import re
from typing import List


# Common abbreviations that shouldn't trigger sentence splits
ABBREVIATIONS = {
    'mr', 'mrs', 'ms', 'dr', 'prof', 'sr', 'jr', 'vs', 'etc', 'inc', 'ltd',
    'co', 'corp', 'st', 'ave', 'blvd', 'rd', 'apt', 'dept', 'est', 'vol',
    'rev', 'gen', 'col', 'lt', 'sgt', 'capt', 'cmdr', 'adm', 'gov', 'pres',
    'sen', 'rep', 'hon', 'jan', 'feb', 'mar', 'apr', 'jun', 'jul', 'aug',
    'sep', 'oct', 'nov', 'dec', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun',
    'i.e', 'e.g', 'cf', 'al', 'approx', 'govt', 'dept', 'univ', 'assn',
}


def split_sentences(text: str, min_chunk_length: int = 15) -> List[str]:
    """
    Split text into sentences for TTS chunking.

    Args:
        text: Input text to split
        min_chunk_length: Minimum characters per chunk (short sentences merged with next)

    Returns:
        List of sentence chunks, each suitable for TTS generation

    Example:
        >>> split_sentences("Hello! How are you? I'm doing great.")
        ["Hello!", "How are you?", "I'm doing great."]

        >>> split_sentences("Yes. No. Maybe.")  # Short sentences merged
        ["Yes. No. Maybe."]
    """
    if not text or not text.strip():
        return []

    text = text.strip()

    # Pattern matches sentence-ending punctuation followed by space or end
    # But avoids splitting on abbreviations and numbers (e.g., "3.14")

    # First, protect abbreviations by replacing their periods temporarily
    protected_text = text
    abbrev_placeholders = {}

    for abbrev in ABBREVIATIONS:
        # Match abbreviation followed by period (case insensitive)
        pattern = rf'\b({re.escape(abbrev)})\.(?=\s|$)'
        matches = list(re.finditer(pattern, protected_text, re.IGNORECASE))
        for i, match in enumerate(matches):
            placeholder = f"__ABBREV_{abbrev}_{i}__"
            abbrev_placeholders[placeholder] = match.group(0)
            protected_text = protected_text[:match.start()] + placeholder + protected_text[match.end():]

    # Protect decimal numbers (e.g., "3.14", "$5.99")
    decimal_pattern = r'(\d+)\.(\d+)'
    decimal_placeholders = {}
    for i, match in enumerate(re.finditer(decimal_pattern, protected_text)):
        placeholder = f"__DECIMAL_{i}__"
        decimal_placeholders[placeholder] = match.group(0)
        protected_text = protected_text[:match.start()] + placeholder + protected_text[match.end():]

    # Protect ellipsis
    protected_text = protected_text.replace('...', '__ELLIPSIS__')

    # Now split on sentence-ending punctuation
    # Match . ! ? followed by space and capital letter, or end of string
    sentence_pattern = r'(?<=[.!?])\s+(?=[A-Z])|(?<=[.!?])$'

    # Split the text
    raw_sentences = re.split(sentence_pattern, protected_text)

    # Restore placeholders
    sentences = []
    for sent in raw_sentences:
        sent = sent.strip()
        if not sent:
            continue

        # Restore ellipsis
        sent = sent.replace('__ELLIPSIS__', '...')

        # Restore decimals
        for placeholder, original in decimal_placeholders.items():
            sent = sent.replace(placeholder, original)

        # Restore abbreviations
        for placeholder, original in abbrev_placeholders.items():
            sent = sent.replace(placeholder, original)

        sentences.append(sent)

    # Merge short sentences with the next one
    merged = []
    buffer = ""

    for sent in sentences:
        if buffer:
            buffer = buffer + " " + sent
        else:
            buffer = sent

        # If buffer is long enough, emit it
        if len(buffer) >= min_chunk_length:
            merged.append(buffer)
            buffer = ""

    # Don't forget any remaining buffer
    if buffer:
        if merged:
            # Append to last sentence if buffer is too short
            merged[-1] = merged[-1] + " " + buffer
        else:
            merged.append(buffer)

    return merged


def split_for_tts(text: str, max_chunk_length: int = 200) -> List[str]:
    """
    Split text optimally for TTS generation.

    Balances between:
    - Starting first chunk quickly (shorter = faster TTS)
    - Natural sentence boundaries (better prosody)
    - Not too many tiny chunks (overhead)

    Args:
        text: Input text
        max_chunk_length: If a sentence exceeds this, split on commas/conjunctions

    Returns:
        List of text chunks optimized for TTS
    """
    sentences = split_sentences(text)

    chunks = []
    for sent in sentences:
        if len(sent) <= max_chunk_length:
            chunks.append(sent)
        else:
            # Long sentence - try to split on natural breaks
            # Split on comma + space, semicolon, or conjunctions
            sub_pattern = r'(?<=[,;])\s+|(?<=\s)(?:and|but|or|so|because|however|therefore)\s+'
            parts = re.split(sub_pattern, sent)

            # Merge very short parts
            current = ""
            for part in parts:
                part = part.strip()
                if not part:
                    continue
                if current:
                    if len(current) + len(part) + 1 <= max_chunk_length:
                        current = current + " " + part
                    else:
                        chunks.append(current)
                        current = part
                else:
                    current = part

            if current:
                chunks.append(current)

    return chunks


# Quick test
if __name__ == "__main__":
    test_cases = [
        "Hello! How are you today?",
        "We help businesses automate marketing with AI. Things like answering calls 24/7 and managing social media. It's like having a marketing team that never sleeps.",
        "Yes. No. Maybe.",
        "Dr. Smith said the price is $3.99. That's a great deal!",
        "I'll send you a link... just give me a moment.",
        "This is a very long sentence that goes on and on, covering multiple topics like marketing, sales, customer service, and automation, all of which are very important for modern businesses.",
    ]

    print("Testing sentence splitter:\n")
    for text in test_cases:
        chunks = split_for_tts(text)
        print(f"Input: {text}")
        print(f"Chunks ({len(chunks)}): {chunks}")
        print()
