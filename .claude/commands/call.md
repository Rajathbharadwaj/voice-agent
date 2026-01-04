# Make Test Call

Test the voice agent by sending a message to the LangGraph server.

## Usage

`/call [message]`

If no message provided, defaults to "Hi, what times do you have available tomorrow?"

## Steps

1. Create a new thread
2. Send message to the sales_agent graph
3. Display the response

## Commands

```bash
cd /home/rajathdb/voice-agent

# Check if server is running
if ! curl -s http://localhost:8123/ok > /dev/null 2>&1; then
    echo "ERROR: LangGraph server not running. Run /start-servers first."
    exit 1
fi

# Message (use argument or default)
MESSAGE="${1:-Hi, what times do you have available tomorrow?}"

echo "=== Testing Voice Agent ==="
echo "Message: $MESSAGE"
echo ""

# Create thread and send message
python3 << EOF
import requests
import json

# Create thread
resp = requests.post("http://localhost:8123/threads", json={})
thread_id = resp.json()["thread_id"]
print(f"Thread: {thread_id}")
print("")

# Send message
resp = requests.post(
    f"http://localhost:8123/threads/{thread_id}/runs/wait",
    json={
        "assistant_id": "sales_agent",
        "input": {"messages": [{"role": "user", "content": "$MESSAGE"}]}
    }
)

# Display response
data = resp.json()
for msg in data.get("messages", []):
    if msg.get("type") == "tool":
        print(f"[Tool: {msg.get('name')}]")
        print(f"  {msg.get('content', '')[:200]}")
        print("")
    elif msg.get("type") == "ai":
        content = msg.get("content", "")
        if isinstance(content, str) and content:
            print(f"Agent: {content}")
EOF
```
