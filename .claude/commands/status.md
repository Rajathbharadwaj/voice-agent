# Check Server Status

Quick status check of all voice agent services.

## Commands

```bash
echo "=== Voice Agent Services Status ==="
echo ""

# Docker Containers
echo "Docker Containers:"
if docker ps | grep -q langgraph-postgres; then
    echo "  ✅ PostgreSQL (5433): Running"
else
    echo "  ❌ PostgreSQL (5433): NOT RUNNING"
fi

if docker ps | grep -q langgraph-redis; then
    echo "  ✅ Redis (6379): Running"
else
    echo "  ❌ Redis (6379): NOT RUNNING"
fi

echo ""

# LangGraph Server
echo "LangGraph Server:"
if curl -s http://localhost:8123/ok > /dev/null 2>&1; then
    echo "  ✅ API (8123): Running"
    # Get registered graphs
    GRAPHS=$(curl -s -X POST http://localhost:8123/assistants/search -H "Content-Type: application/json" -d '{}' 2>/dev/null | python3 -c "import sys,json; data=json.load(sys.stdin); print(', '.join([a['name'] for a in data]))" 2>/dev/null)
    echo "  📊 Graphs: $GRAPHS"
    echo ""
    echo "  Studio: https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:8123"
else
    echo "  ❌ API (8123): NOT RUNNING"
fi

echo ""
```
