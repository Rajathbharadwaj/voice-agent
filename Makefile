.PHONY: help start stop restart status logs db-start db-stop server-start server-stop ngrok-start ngrok-stop voice-start voice-stop auth test clean call

# Ports - voice-agent uses 8124/8081 to avoid conflicts with PACE (8123) and Nudging GW (8080)
LANGGRAPH_PORT ?= 8124
VOICE_PORT ?= 8081
AGENT_MODE ?= healthcare
TTS_ENGINE ?= orpheus
CONDA_PYTHON ?= /home/rajathdb/miniconda3/envs/voice-agent/bin/python

# Default target
help:
	@echo "Voice Agent - Available Commands"
	@echo "================================="
	@echo ""
	@echo "  make start        - Start all services (DB + LangGraph + ngrok + voice server)"
	@echo "  make stop         - Stop all services"
	@echo "  make restart      - Restart all services"
	@echo "  make status       - Check status of all services"
	@echo "  make logs         - Tail LangGraph server logs"
	@echo "  make logs-voice   - Tail voice server logs"
	@echo ""
	@echo "  make db-start     - Start PostgreSQL and Redis containers"
	@echo "  make db-stop      - Stop PostgreSQL and Redis containers"
	@echo "  make server-start - Start LangGraph dev server (port $(LANGGRAPH_PORT))"
	@echo "  make server-stop  - Stop LangGraph dev server"
	@echo "  make ngrok-start  - Start ngrok tunnel (port $(VOICE_PORT))"
	@echo "  make ngrok-stop   - Stop ngrok"
	@echo "  make voice-start  - Start voice server (port $(VOICE_PORT))"
	@echo "  make voice-stop   - Stop voice server"
	@echo ""
	@echo "  make call          - Call first patient from CSV (index 0)"
	@echo "  make call N=1      - Call patient by index"
	@echo "  make call-list     - List all patients"
	@echo "  make auth          - Run Google Calendar OAuth flow"
	@echo "  make test          - Test the agent with a sample message"
	@echo "  make clean         - Stop all and remove containers"
	@echo ""

# Start all services
start: db-start server-start ngrok-start voice-start
	@echo ""
	@echo "All services started!"
	@echo "  LangGraph:  http://localhost:$(LANGGRAPH_PORT)"
	@echo "  Voice:      http://localhost:$(VOICE_PORT)"
	@echo "  Studio UI:  https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:$(LANGGRAPH_PORT)"
	@echo ""
	@echo "To make a call: make call"

# Stop all services
stop: voice-stop ngrok-stop server-stop db-stop
	@echo "All services stopped."

# Restart all services
restart: stop start

# Check status
status:
	@echo "=== Docker Containers ==="
	@docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" | grep -E "langgraph|NAMES" || echo "No containers running"
	@echo ""
	@echo "=== LangGraph Server (port $(LANGGRAPH_PORT)) ==="
	@curl -s http://localhost:$(LANGGRAPH_PORT)/ok && echo " - Running" || echo "Not running"
	@echo ""
	@echo "=== Voice Server (port $(VOICE_PORT)) ==="
	@curl -s http://localhost:$(VOICE_PORT)/health > /dev/null 2>&1 && echo " - Running" || (ps aux | grep -q "[u]vicorn.*$(VOICE_PORT)" && echo " - Running (no health endpoint)" || echo "Not running")
	@echo ""
	@echo "=== Ngrok ==="
	@curl -s localhost:4040/api/tunnels 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); [print(f'  {t[\"name\"]}: {t[\"public_url\"]} -> {t[\"config\"][\"addr\"]}') for t in d.get('tunnels',[])]" 2>/dev/null || echo "  Not running"
	@echo ""
	@echo "=== Registered Graphs ==="
	@curl -s -X POST http://localhost:$(LANGGRAPH_PORT)/assistants/search -H "Content-Type: application/json" -d '{}' 2>/dev/null | python3 -c "import sys,json; data=json.load(sys.stdin); print('\n'.join([f'  - {a[\"name\"]}' for a in data]))" 2>/dev/null || echo "  Unable to fetch"

# View logs
logs:
	@echo "=== LangGraph Server Logs ==="
	@tail -50 /tmp/langgraph-voice.log 2>/dev/null || echo "No log file found."

logs-voice:
	@echo "=== Voice Server Logs ==="
	@tail -50 /tmp/voice-server.log 2>/dev/null || echo "No log file found."

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
	@docker compose stop langgraph-postgres langgraph-redis 2>/dev/null || true

# Start LangGraph server
server-start:
	@echo "Starting LangGraph dev server on port $(LANGGRAPH_PORT)..."
	@if curl -s http://localhost:$(LANGGRAPH_PORT)/ok > /dev/null 2>&1; then \
		echo "Server already running on port $(LANGGRAPH_PORT)"; \
	else \
		cd /home/rajathdb/voice-agent && \
		nohup langgraph dev --port $(LANGGRAPH_PORT) --no-browser > /tmp/langgraph-voice.log 2>&1 & \
		echo "Server starting... (logs: /tmp/langgraph-voice.log)"; \
		sleep 10; \
		curl -s http://localhost:$(LANGGRAPH_PORT)/ok && echo " - Server ready!" || echo "Server may still be starting. Check: tail -f /tmp/langgraph-voice.log"; \
	fi

# Stop LangGraph server
server-stop:
	@echo "Stopping LangGraph server..."
	@pkill -f "langgraph dev.*$(LANGGRAPH_PORT)" 2>/dev/null || pkill -f "langgraph dev.*voice" 2>/dev/null || true
	@echo "Server stopped."

# Start ngrok (uses named tunnel from ~/.config/ngrok/ngrok.yml)
# voice-agent tunnel has a persistent domain: voice-agent.ngrok.dev
ngrok-start:
	@echo "Starting ngrok tunnel for voice-agent..."
	@if curl -s localhost:4040/api/tunnels/voice-agent > /dev/null 2>&1; then \
		echo "ngrok already running"; \
		curl -s localhost:4040/api/tunnels/voice-agent | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'  URL: {d[\"public_url\"]}')"; \
	else \
		nohup ngrok start voice-agent > /tmp/ngrok.log 2>&1 & \
		sleep 3; \
		curl -s localhost:4040/api/tunnels/voice-agent | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'ngrok URL: {d[\"public_url\"]}')"; \
	fi

# Stop ngrok
ngrok-stop:
	@echo "Stopping ngrok..."
	@pkill -f ngrok 2>/dev/null || true

# Start voice server
voice-start:
	@echo "Starting voice server on port $(VOICE_PORT) (mode: $(AGENT_MODE), tts: $(TTS_ENGINE))..."
	@if ps aux | grep -q "[u]vicorn.*$(VOICE_PORT)"; then \
		echo "Voice server already running on port $(VOICE_PORT)"; \
	else \
		cd /home/rajathdb/voice-agent && \
		PYTHONPATH=src AGENT_MODE=$(AGENT_MODE) TTS_ENGINE=$(TTS_ENGINE) LANGGRAPH_URL=http://127.0.0.1:$(LANGGRAPH_PORT) \
		nohup $(CONDA_PYTHON) -m uvicorn sdr_agent.server:app --host 0.0.0.0 --port $(VOICE_PORT) > /tmp/voice-server.log 2>&1 & \
		echo "Voice server starting... (logs: /tmp/voice-server.log)"; \
		sleep 5; \
		echo "Voice server started."; \
	fi

# Stop voice server
voice-stop:
	@echo "Stopping voice server..."
	@pkill -f "uvicorn.*sdr_agent" 2>/dev/null || pkill -f "uvicorn.*$(VOICE_PORT)" 2>/dev/null || true
	@echo "Voice server stopped."

# Make a call
N ?= 0
call:
	@cd /home/rajathdb/voice-agent && AGENT_MODE=$(AGENT_MODE) $(CONDA_PYTHON) call_patient.py $(N)

call-new:
	@cd /home/rajathdb/voice-agent && AGENT_MODE=$(AGENT_MODE) $(CONDA_PYTHON) call_patient.py $(N) --new-thread

call-list:
	@cd /home/rajathdb/voice-agent && $(CONDA_PYTHON) call_patient.py --list

# Configure Twilio SMS webhook to point to persistent ngrok domain
# Since voice-agent.ngrok.dev is stable, this only needs to run once
sms-setup:
	@echo "Configuring Twilio SMS webhook to voice-agent.ngrok.dev..."
	@SMS_URL="https://voice-agent.ngrok.dev/sms/inbound" && \
	echo "Setting SMS webhook to: $$SMS_URL" && \
	$(CONDA_PYTHON) -c " \
from dotenv import load_dotenv; load_dotenv('.env'); \
import os; from twilio.rest import Client; \
c = Client(os.environ['TWILIO_ACCOUNT_SID'], os.environ['TWILIO_AUTH_TOKEN']); \
nums = c.incoming_phone_numbers.list(phone_number=os.environ['TWILIO_PHONE_NUMBER']); \
n = c.incoming_phone_numbers(nums[0].sid).update(sms_url='$$SMS_URL', sms_method='POST'); \
print(f'Done! SMS webhook: {n.sms_url}') \
"

# Run Google Calendar OAuth
auth:
	@echo "Running Google Calendar OAuth..."
	@python scripts/auth_google_calendar.py

# Test the agent
test:
	@echo "Testing healthcare agent..."
	@THREAD=$$(curl -s -X POST http://localhost:$(LANGGRAPH_PORT)/threads -H "Content-Type: application/json" -d '{}' | python3 -c "import sys,json; print(json.load(sys.stdin)['thread_id'])") && \
	echo "Thread: $$THREAD" && \
	curl -s -X POST "http://localhost:$(LANGGRAPH_PORT)/threads/$$THREAD/runs/wait" \
		-H "Content-Type: application/json" \
		-d '{"assistant_id":"healthcare_agent","input":{"messages":[{"role":"user","content":"Hi, yes this is a good time."}]},"config":{"configurable":{"patient_name":"David Chen","appointment_date":"February 13 2026","appointment_time":"2:30 PM","provider_name":"Dr. Patel","clinic_name":"City of Hope","appointment_type":"Oncology follow-up"}}}' | \
	python3 -c "import sys,json; msgs=json.load(sys.stdin).get('messages',[]); [print(f\"Agent: {m.get('content')}\") for m in msgs if m.get('type')=='ai' and isinstance(m.get('content'),str)]"

# Clean up everything
clean: stop
	@echo "Removing containers and volumes..."
	@docker compose down -v
	@echo "Cleanup complete."
