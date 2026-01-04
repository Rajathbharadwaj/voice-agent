.PHONY: help start stop restart status logs db-start db-stop server-start server-stop auth test clean

# Default target
help:
	@echo "Voice Agent - Available Commands"
	@echo "================================="
	@echo ""
	@echo "  make start        - Start all services (DB + LangGraph server)"
	@echo "  make stop         - Stop all services"
	@echo "  make restart      - Restart all services"
	@echo "  make status       - Check status of all services"
	@echo "  make logs         - Tail LangGraph server logs"
	@echo ""
	@echo "  make db-start     - Start PostgreSQL and Redis containers"
	@echo "  make db-stop      - Stop PostgreSQL and Redis containers"
	@echo "  make server-start - Start LangGraph dev server"
	@echo "  make server-stop  - Stop LangGraph dev server"
	@echo ""
	@echo "  make auth         - Run Google Calendar OAuth flow"
	@echo "  make test         - Test the agent with a sample message"
	@echo "  make clean        - Stop all and remove containers"
	@echo ""

# Start all services
start: db-start server-start
	@echo "All services started!"
	@echo "Studio UI: https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:8123"

# Stop all services
stop: server-stop db-stop
	@echo "All services stopped."

# Restart all services
restart: stop start

# Check status
status:
	@echo "=== Docker Containers ==="
	@docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" | grep -E "langgraph|NAMES" || echo "No containers running"
	@echo ""
	@echo "=== LangGraph Server ==="
	@curl -s http://localhost:8123/ok && echo " - Running on port 8123" || echo "Not running"
	@echo ""
	@echo "=== Registered Graphs ==="
	@curl -s -X POST http://localhost:8123/assistants/search -H "Content-Type: application/json" -d '{}' 2>/dev/null | python3 -c "import sys,json; data=json.load(sys.stdin); print('\n'.join([f'  - {a[\"name\"]}' for a in data]))" 2>/dev/null || echo "  Unable to fetch"

# View logs
logs:
	@echo "=== LangGraph Server Logs ==="
	@tail -50 /tmp/langgraph-dev.log 2>/dev/null || echo "No log file found. Server may not be running."

# Start database containers
db-start:
	@echo "Starting PostgreSQL and Redis..."
	@docker compose up -d langgraph-postgres langgraph-redis
	@echo "Waiting for containers to be healthy..."
	@sleep 5
	@docker compose ps

# Stop database containers
db-stop:
	@echo "Stopping database containers..."
	@docker compose stop langgraph-postgres langgraph-redis

# Start LangGraph server
server-start:
	@echo "Starting LangGraph dev server..."
	@if curl -s http://localhost:8123/ok > /dev/null 2>&1; then \
		echo "Server already running on port 8123"; \
	else \
		cd /home/rajathdb/voice-agent && bash -c 'source .env && nohup langgraph dev --port 8123 > /tmp/langgraph-dev.log 2>&1 &' && \
		echo "Server starting... (logs at /tmp/langgraph-dev.log)"; \
		sleep 10; \
		curl -s http://localhost:8123/ok && echo " - Server ready!" || echo "Server may still be starting..."; \
	fi

# Stop LangGraph server
server-stop:
	@echo "Stopping LangGraph server..."
	@pkill -f "langgraph dev" 2>/dev/null || true
	@echo "Server stopped."

# Run Google Calendar OAuth
auth:
	@echo "Running Google Calendar OAuth..."
	@python scripts/auth_google_calendar.py

# Test the agent
test:
	@echo "Testing agent..."
	@THREAD=$$(curl -s -X POST http://localhost:8123/threads -H "Content-Type: application/json" -d '{}' | python3 -c "import sys,json; print(json.load(sys.stdin)['thread_id'])") && \
	echo "Thread: $$THREAD" && \
	curl -s -X POST "http://localhost:8123/threads/$$THREAD/runs/wait" \
		-H "Content-Type: application/json" \
		-d '{"assistant_id":"sales_agent","input":{"messages":[{"role":"user","content":"Hi, what times do you have available tomorrow?"}]}}' | \
	python3 -c "import sys,json; msgs=json.load(sys.stdin).get('messages',[]); [print(f\"Agent: {m.get('content')}\") for m in msgs if m.get('type')=='ai' and isinstance(m.get('content'),str)]"

# Clean up everything
clean: stop
	@echo "Removing containers and volumes..."
	@docker compose down -v
	@echo "Cleanup complete."
