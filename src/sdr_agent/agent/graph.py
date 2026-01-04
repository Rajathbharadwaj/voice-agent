"""
LangGraph Sales Agent Graph

Exports a compiled LangGraph StateGraph for use with LangGraph Platform.
Uses PostgreSQL for checkpointing and Redis for pub/sub when deployed via docker-compose.
"""

import os
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

# Load prompts and tools directly
_prompts = _load_module_direct("_graph_prompts", _this_dir / "prompts.py")
_tools = _load_module_direct("_graph_tools", _this_dir / "tools.py")

SALES_SYSTEM_PROMPT = _prompts.SALES_SYSTEM_PROMPT
SALES_TOOLS = _tools.SALES_TOOLS


def build_system_prompt() -> str:
    """Build the system prompt with current date/time context."""
    now = datetime.now()
    current_datetime = now.strftime("%A, %B %d, %Y at %I:%M %p")

    # Build mini calendar for next 7 days
    calendar_lines = [
        "## CURRENT DATE/TIME & CALENDAR",
        f"**Today is {current_datetime}**",
        "",
        "Upcoming days for scheduling:"
    ]
    for i in range(7):
        future = now + timedelta(days=i)
        day_label = "Today" if i == 0 else "Tomorrow" if i == 1 else future.strftime("%A")
        calendar_lines.append(f"- {day_label}: {future.strftime('%A, %B %d, %Y')}")

    calendar_lines.append("")
    calendar_lines.append("IMPORTANT: Use exact dates from this calendar. Don't guess dates!")
    calendar_context = "\n".join(calendar_lines)

    return f"{SALES_SYSTEM_PROMPT}\n\n{calendar_context}"


# Initialize model - uses ANTHROPIC_API_KEY from environment
model = init_chat_model(
    model="anthropic:claude-opus-4-5-20251101",
    temperature=0.7,
)

# Bind tools to the model
model_with_tools = model.bind_tools(SALES_TOOLS)


def should_continue(state: MessagesState) -> str:
    """Determine if we should continue to tools or end."""
    last_message = state["messages"][-1]

    # If the last message has tool calls, route to tools
    if hasattr(last_message, "tool_calls") and last_message.tool_calls:
        return "tools"

    # Otherwise, end
    return END


def call_model(state: MessagesState) -> dict:
    """Call the model with the current state."""
    messages = state["messages"]

    # Add system message if not present
    if not messages or not isinstance(messages[0], SystemMessage):
        system_prompt = build_system_prompt()
        messages = [SystemMessage(content=system_prompt)] + list(messages)

    response = model_with_tools.invoke(messages)
    return {"messages": [response]}


# Build the graph
builder = StateGraph(MessagesState)

# Add nodes
builder.add_node("agent", call_model)
builder.add_node("tools", ToolNode(SALES_TOOLS))

# Add edges
builder.add_edge(START, "agent")
builder.add_conditional_edges("agent", should_continue)
builder.add_edge("tools", "agent")

# Compile the graph
# Note: When running via LangGraph Platform (langgraph up), checkpointer is
# automatically configured from POSTGRES_URI environment variable
graph = builder.compile()
