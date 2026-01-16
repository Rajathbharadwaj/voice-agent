"""
LangGraph Healthcare Agent Graph

Exports a compiled LangGraph StateGraph for healthcare appointment reminders.
Uses the same architecture as the sales agent but with healthcare-specific
prompts and tools.
"""

import os
from dotenv import load_dotenv
load_dotenv()  # Load .env before reading LLM_MODEL
import sys
import importlib.util
from datetime import datetime, timedelta
from pathlib import Path

from langchain.chat_models import init_chat_model
from langchain_core.messages import SystemMessage
from langgraph.graph import StateGraph, MessagesState, START, END
from langgraph.prebuilt import ToolNode


# Load modules directly to avoid __init__.py chain
def _load_module_direct(module_name: str, file_path: Path):
    """Load a Python module directly from file path."""
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


# Get the directory containing this file
_this_dir = Path(__file__).parent

# Load healthcare prompts and tools directly
_prompts = _load_module_direct("_healthcare_prompts", _this_dir / "prompts_healthcare.py")
_tools = _load_module_direct("_healthcare_tools", _this_dir / "tools_healthcare.py")

HEALTHCARE_SYSTEM_PROMPT = _prompts.HEALTHCARE_SYSTEM_PROMPT
HEALTHCARE_TOOLS = _tools.HEALTHCARE_TOOLS


def build_system_prompt(config: dict = None) -> str:
    """Build the system prompt with current date/time and appointment context."""
    now = datetime.now()
    current_datetime = now.strftime("%A, %B %d, %Y at %I:%M %p")

    # Build mini calendar for context
    calendar_lines = [
        "## CURRENT DATE/TIME",
        f"**Today is {current_datetime}**",
        "",
        "Reference days for scheduling:"
    ]
    for i in range(7):
        future = now + timedelta(days=i)
        day_label = "Today" if i == 0 else "Tomorrow" if i == 1 else future.strftime("%A")
        calendar_lines.append(f"- {day_label}: {future.strftime('%A, %B %d, %Y')}")

    calendar_lines.append("")
    calendar_lines.append("IMPORTANT: Use exact dates when discussing rescheduling preferences.")
    calendar_context = "\n".join(calendar_lines)

    # Add appointment context if available
    appointment_context = ""
    if config and "configurable" in config:
        cfg = config["configurable"]
        patient_name = cfg.get("patient_name", "")
        if patient_name:
            appointment_context = f"""

## CURRENT CALL - APPOINTMENT DETAILS
**Patient:** {patient_name}
**Appointment Date:** {cfg.get('appointment_date', 'Not specified')}
**Appointment Time:** {cfg.get('appointment_time', 'Not specified')}
**Provider:** {cfg.get('provider_name', 'Not specified')}
**Clinic:** {cfg.get('clinic_name', 'Not specified')}
**Appointment Type:** {cfg.get('appointment_type', 'Not specified')}

CRITICAL: You KNOW these appointment details. Reference them confidently when the patient asks."""

    return f"{HEALTHCARE_SYSTEM_PROMPT}\n\n{calendar_context}{appointment_context}"


# Initialize model - configurable via LLM_MODEL env var
# Examples: "openai:gpt-5.2", "anthropic:claude-opus-4-5-20251101", "anthropic:claude-sonnet-4-5-20250929"
DEFAULT_MODEL = "anthropic:claude-opus-4-5-20251101"
LLM_MODEL = os.environ.get("LLM_MODEL", DEFAULT_MODEL)
print(f"[LangGraph Healthcare] Using model: {LLM_MODEL}")

model = init_chat_model(
    model=LLM_MODEL,
    temperature=0.7,
)

# Bind healthcare tools to the model
model_with_tools = model.bind_tools(HEALTHCARE_TOOLS)


def should_continue(state: MessagesState) -> str:
    """Determine if we should continue to tools or end."""
    last_message = state["messages"][-1]

    # If the last message has tool calls, route to tools
    if hasattr(last_message, "tool_calls") and last_message.tool_calls:
        return "tools"

    # Otherwise, end
    return END


def call_model(state: MessagesState, config: dict = None) -> dict:
    """Call the model with the current state and config."""
    messages = state["messages"]

    # Add system message if not present (include appointment context from config)
    if not messages or not isinstance(messages[0], SystemMessage):
        system_prompt = build_system_prompt(config)
        messages = [SystemMessage(content=system_prompt)] + list(messages)

    response = model_with_tools.invoke(messages)
    return {"messages": [response]}


# Build the graph
builder = StateGraph(MessagesState)

# Add nodes
builder.add_node("agent", call_model)
builder.add_node("tools", ToolNode(HEALTHCARE_TOOLS))

# Add edges
builder.add_edge(START, "agent")
builder.add_conditional_edges("agent", should_continue)
builder.add_edge("tools", "agent")

# Compile the graph
# Note: When running via LangGraph Platform (langgraph up), checkpointer is
# automatically configured from POSTGRES_URI environment variable
graph = builder.compile()
