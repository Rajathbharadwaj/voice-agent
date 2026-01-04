#!/usr/bin/env python3
"""
Start the SDR Agent server with TTS model warmup.

Starts the server and immediately warms up the TTS model
to avoid the 8-second delay on the first call.
"""

import sys
import subprocess
import time
import requests

sys.path.insert(0, 'src')

# Patch perth watermarker FIRST (before any chatterbox imports)
import perth
perth.PerthImplicitWatermarker = perth.DummyWatermarker

# Start the server in a subprocess
print("[Startup] Starting server...")
server_process = subprocess.Popen(
    [sys.executable, "-c", """
import sys
sys.path.insert(0, 'src')
import perth
perth.PerthImplicitWatermarker = perth.DummyWatermarker
from sdr_agent.server import create_app
import uvicorn
app = create_app()
uvicorn.run(app, host='0.0.0.0', port=8080)
"""],
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True
)

# Wait for server to be ready
print("[Startup] Waiting for server to be ready...")
for _ in range(30):
    time.sleep(1)
    try:
        resp = requests.get("http://localhost:8080/", timeout=1)
        if resp.status_code == 200:
            print("[Startup] Server is ready!")
            break
    except:
        pass
else:
    print("[Startup] Server failed to start!")
    sys.exit(1)

# Warm up the TTS model
print("[Startup] Warming up TTS model (this takes ~8 seconds)...")
try:
    resp = requests.post("http://localhost:8080/warmup", timeout=60)
    if resp.status_code == 200:
        print("[Startup] TTS model warmed up!")
    else:
        print(f"[Startup] Warmup failed: {resp.text}")
except Exception as e:
    print(f"[Startup] Warmup error: {e}")

print("[Startup] Server is ready for calls!")
print("[Startup] Press Ctrl+C to stop")

# Keep running and show server output
try:
    for line in server_process.stdout:
        print(line, end='')
except KeyboardInterrupt:
    print("\n[Startup] Shutting down...")
    server_process.terminate()
