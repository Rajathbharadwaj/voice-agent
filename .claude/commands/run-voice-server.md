# Run Voice Agent Server

Start the voice agent server with the correct Python environment.

## Usage

`/run-server`

## Commands

```bash
cd /home/rajathdb/voice-agent

# Kill any existing server
pkill -f "python.*sdr_agent.server" 2>/dev/null || true

# Start server with venv activated
source .venv/bin/activate && nohup python -X faulthandler -m src.sdr_agent.server > /tmp/voice-agent-server.log 2>&1 &

sleep 3

# Verify it's running
if curl -s http://localhost:8080/ > /dev/null 2>&1; then
    echo "Voice server started on port 8080"
    echo "Logs: /tmp/voice-agent-server.log"
else
    echo "Server may still be starting... check logs"
    tail -5 /tmp/voice-agent-server.log
fi
```
