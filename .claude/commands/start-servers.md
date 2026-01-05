# Start Voice Agent Servers

Start all services for the voice agent: PostgreSQL, Redis, and LangGraph dev server.

> **Note**: If you've updated `.env` (e.g., NGROK_URL changed), use `/restart-servers` instead to force a fresh restart and pick up the new values.

## Steps

1. Start Docker containers (PostgreSQL + Redis) if not running
2. Start LangGraph dev server on port 8123 if not running
3. Verify all services are healthy
4. Report status and provide Studio UI link

## Commands

```bash
cd /home/rajathdb/voice-agent

echo "=== Starting Voice Agent Services ==="
echo ""

# Start Docker containers if not running
if ! docker ps | grep -q langgraph-postgres; then
    echo "Starting PostgreSQL and Redis..."
    docker compose up -d langgraph-postgres langgraph-redis
    sleep 5
fi

# Check container health
echo "Docker containers:"
docker ps --format "  {{.Names}}: {{.Status}}" | grep langgraph || echo "  No containers running"
echo ""

# Start LangGraph server if not running
if ! curl -s http://localhost:8123/ok > /dev/null 2>&1; then
    echo "Starting LangGraph server..."
    source .env && nohup langgraph dev --port 8123 > /tmp/langgraph-dev.log 2>&1 &
    sleep 10
fi

# Verify server
if curl -s http://localhost:8123/ok > /dev/null 2>&1; then
    echo "✅ LangGraph server: Running on port 8123"
    echo ""
    echo "Studio UI: https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:8123"
else
    echo "❌ LangGraph server: Failed to start"
    echo "   Check logs: tail -50 /tmp/langgraph-dev.log"
fi
```
