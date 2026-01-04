"""
Sales Agent Package

Contains the LangChain-powered sales agent for conducting outbound calls.
"""

from .sales_agent import SalesAgent, CallSession
from .prompts import SALES_SYSTEM_PROMPT, IMMEDIATE_END_TRIGGERS, VOICEMAIL_INDICATORS
from .tools import SALES_TOOLS, CallContext
from .call_monitor import CallMonitor, CallIssue, should_skip_lead

__all__ = [
    "SalesAgent",
    "CallSession",
    "SALES_SYSTEM_PROMPT",
    "SALES_TOOLS",
    "CallContext",
    "CallMonitor",
    "CallIssue",
    "should_skip_lead",
    "IMMEDIATE_END_TRIGGERS",
    "VOICEMAIL_INDICATORS",
]
