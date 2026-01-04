"""
Turn Detector using LiveKit's End-of-Utterance Model

Uses a transformer model to predict when a user has finished speaking,
rather than just relying on silence detection.

Based on: https://huggingface.co/livekit/turn-detector
Implementation derived from: https://github.com/livekit/agents
"""

import re
import numpy as np
from typing import Optional, List, Dict
from pathlib import Path


# Maximum tokens for context window
MAX_HISTORY_TOKENS = 512

# Token ID for end-of-utterance marker <|im_end|>
EOU_TOKEN_ID = 2


def _softmax(x: np.ndarray) -> np.ndarray:
    """Apply softmax to get probabilities."""
    exp_x = np.exp(x - np.max(x))
    return exp_x / exp_x.sum()


class TurnDetector:
    """
    Predicts end-of-utterance using LiveKit's turn detector model.

    The model analyzes conversation context to determine if the user
    has finished their turn, which is more accurate than silence-based detection.
    """

    # Thresholds for end-of-turn detection
    # Higher threshold = less interruption, more buffering
    # 0.5 was too aggressive (interrupted mid-sentence)
    # 0.9 was too conservative (didn't trigger on "Yeah, tomorrow works")
    # 0.65 was still too high - blocked "Okay, yeah" (0.37) and "My name is Raj" (0.20)
    # 0.35 = lower threshold to catch short affirmations and names
    EOT_THRESHOLD = 0.30  # Probability threshold to consider turn complete (lower = respond faster)

    def __init__(self, use_onnx: bool = True):
        """
        Initialize the turn detector.

        Args:
            use_onnx: Use ONNX runtime for faster inference (recommended)
        """
        self.use_onnx = use_onnx
        self._model = None
        self._tokenizer = None
        self._onnx_session = None

        # Conversation history (last 4 turns)
        self._history: List[Dict[str, str]] = []
        self._max_history = 4

    def _normalize_text(self, text: str) -> str:
        """Normalize text for the model (lowercase, remove punctuation)."""
        # Convert to lowercase
        text = text.lower()
        # Remove punctuation except apostrophes and hyphens
        text = re.sub(r"[^\w\s'\-]", "", text)
        # Collapse whitespace
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _load_model(self):
        """Lazy load the model."""
        if self._tokenizer is not None:
            return

        print("[TurnDetector] Loading model...")

        from transformers import AutoTokenizer

        self._tokenizer = AutoTokenizer.from_pretrained("livekit/turn-detector")

        if self.use_onnx:
            import onnxruntime as ort
            from huggingface_hub import hf_hub_download

            # Download ONNX model
            model_path = hf_hub_download(
                repo_id="livekit/turn-detector",
                filename="model_quantized.onnx",
            )

            # Create ONNX session with CPU provider
            self._onnx_session = ort.InferenceSession(
                model_path,
                providers=['CPUExecutionProvider']
            )
            print("[TurnDetector] ONNX model loaded")
        else:
            from transformers import AutoModelForCausalLM
            import torch

            self._model = AutoModelForCausalLM.from_pretrained(
                "livekit/turn-detector"
            )
            self._model.eval()
            print("[TurnDetector] PyTorch model loaded")

    def add_agent_message(self, text: str):
        """Add an agent message to history."""
        self._history.append({"role": "assistant", "content": text})
        # Keep only last N turns
        if len(self._history) > self._max_history * 2:
            self._history = self._history[-self._max_history * 2:]

    def _format_chat_ctx(self, messages: List[Dict[str, str]]) -> str:
        """Format chat context for the model."""
        # Normalize text in messages
        normalized_messages = []
        for msg in messages:
            normalized_messages.append({
                "role": msg["role"],
                "content": self._normalize_text(msg["content"])
            })

        # Apply chat template
        text = self._tokenizer.apply_chat_template(
            normalized_messages,
            add_generation_prompt=False,
            add_special_tokens=False,
            tokenize=False
        )

        # Remove the final EOU token marker (we're predicting if it should be there)
        # The model uses <|im_end|> as the end-of-utterance marker
        ix = text.rfind("<|im_end|>")
        if ix > 0:
            text = text[:ix]

        return text

    def predict_eot(self, user_text: str) -> float:
        """
        Predict probability that user has finished their turn.

        Args:
            user_text: Current user utterance (from STT)

        Returns:
            Probability (0-1) that the user has finished speaking
        """
        self._load_model()

        # Build messages with current user text
        messages = self._history.copy()
        messages.append({"role": "user", "content": user_text})

        # Format chat context
        text = self._format_chat_ctx(messages)

        # Tokenize - only need input_ids for ONNX
        inputs = self._tokenizer(
            text,
            add_special_tokens=False,
            return_tensors="np" if self.use_onnx else "pt",
            truncation=True,
            max_length=MAX_HISTORY_TOKENS
        )

        if self.use_onnx:
            # Run ONNX inference - model only expects input_ids
            ort_inputs = {
                "input_ids": inputs["input_ids"].astype(np.int64)
            }
            outputs = self._onnx_session.run(None, ort_inputs)

            # Output shape: [batch, sequence, vocab_size]
            # Get logits at the last position and apply softmax
            last_logits = outputs[0][0, -1, :]  # Shape: [vocab_size]
            probs = _softmax(last_logits)

            # Get probability of the EOU token being next
            eot_prob = float(probs[EOU_TOKEN_ID])
        else:
            import torch

            with torch.no_grad():
                outputs = self._model(input_ids=inputs["input_ids"])
                # Get logits at the last position
                last_logits = outputs.logits[0, -1, :]
                probs = torch.nn.functional.softmax(last_logits, dim=-1)
                eot_prob = float(probs[EOU_TOKEN_ID].item())

        return eot_prob

    def is_turn_complete(self, user_text: str) -> bool:
        """
        Check if the user has finished their turn.

        Args:
            user_text: Current user utterance

        Returns:
            True if user is likely done speaking
        """
        prob = self.predict_eot(user_text)
        print(f"[TurnDetector] EOT probability: {prob:.2f}")
        return prob >= self.EOT_THRESHOLD

    def add_user_message(self, text: str):
        """Add confirmed user message to history (after turn is complete)."""
        self._history.append({"role": "user", "content": text})
        # Keep only last N turns
        if len(self._history) > self._max_history * 2:
            self._history = self._history[-self._max_history * 2:]

    def clear_history(self):
        """Clear conversation history."""
        self._history = []


# Global instance for reuse
_detector: Optional[TurnDetector] = None

def get_turn_detector() -> TurnDetector:
    """Get or create the global turn detector instance."""
    global _detector
    if _detector is None:
        _detector = TurnDetector(use_onnx=True)
    return _detector
