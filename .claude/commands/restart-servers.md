# Restart Voice Agent Servers

Force restart all services to pick up new .env changes (e.g., when NGROK_URL changes).

**Use this when:**
- ngrok URL has changed
- .env file has been updated
- Services are not picking up new environment variables

## Steps

1. Kill existing LangGraph server on port 8123
2. Kill existing voice server
3. Restart Docker containers
4. Start LangGraph with fresh .env
5. Start voice server with fresh .env
6. Verify all services are healthy

## Commands

```bash
cd /home/rajathdb/voice-agent

echo "=== Restarting Voice Agent Services ==="
echo ""
echo "Current NGROK_URL:"
grep "NGROK_URL" .env
echo ""

# Kill LangGraph server
echo "Stopping LangGraph server..."
lsof -t -i:8123 | xargs kill -9 2>/dev/null || true
sleep 2

# Kill voice server
echo "Stopping voice server..."
pkill -f "src.sdr_agent.server" 2>/dev/null || true
sleep 2

# Restart Docker containers
echo "Restarting Docker containers..."
docker compose restart langgraph-postgres langgraph-redis 2>/dev/null || docker compose up -d langgraph-postgres langgraph-redis
sleep 3

# Start LangGraph with fresh env
echo "Starting LangGraph server with fresh .env..."
source .env && nohup langgraph dev --port 8123 --no-browser > /tmp/langgraph-dev.log 2>&1 &
sleep 10

# Verify LangGraph
if curl -s http://localhost:8123/ok > /dev/null 2>&1; then
    echo "✅ LangGraph server: Running on port 8123"
else
    echo "❌ LangGraph server: Failed to start"
    echo "   Check logs: tail -50 /tmp/langgraph-dev.log"
fi

# Start voice server with fresh env
echo "Starting voice server..."
source .env && nohup python -m src.sdr_agent.server > /tmp/voice-server.log 2>&1 &
sleep 5

# Verify voice server
if pgrep -f "src.sdr_agent.server" > /dev/null; then
    echo "✅ Voice server: Running on port 8080"
else
    echo "❌ Voice server: Failed to start"
    echo "   Check logs: tail -50 /tmp/voice-server.log"
fi

echo ""
echo "=== Restart Complete ==="
echo "Studio UI: https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:8123"
```

## Important Notes

- Always update .env with new NGROK_URL before running this command
- The webhook URL for CUA bookings uses NGROK_URL from .env
- If NGROK_URL is stale, calendar invites won't be sent after form submission
