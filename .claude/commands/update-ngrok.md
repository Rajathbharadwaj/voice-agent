# Update Ngrok URL

Update the NGROK_URL in .env and restart services to pick up the change.

**Use this when:**
- You've started a new ngrok tunnel
- The ngrok URL has changed after a restart
- Webhooks are not being received

## Steps

1. Get the new ngrok URL from the user or detect it
2. Update NGROK_URL in .env
3. Restart all services to pick up the change

## Commands

First, check the current ngrok URL:
```bash
echo "Current NGROK_URL in .env:"
grep "NGROK_URL" /home/rajathdb/voice-agent/.env

echo ""
echo "If ngrok is running locally, check:"
curl -s http://localhost:4040/api/tunnels 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print('Ngrok tunnel:', d['tunnels'][0]['public_url'] if d.get('tunnels') else 'No tunnels found')" 2>/dev/null || echo "Ngrok API not accessible"
```

Then update .env with the new URL (ask user for the URL if needed):
```bash
# Replace OLD_URL with NEW_URL in .env
# sed -i 's|NGROK_URL=.*|NGROK_URL=https://NEW_URL.ngrok-free.app|' /home/rajathdb/voice-agent/.env
```

After updating, run `/restart-servers` to apply the changes.

## Why This Matters

The voice agent uses NGROK_URL for:
1. **Webhook callbacks**: CUA backend posts form submissions to `{NGROK_URL}/webhook/booking`
2. **Calendar invites**: Without the correct webhook URL, calendar events won't be created

If the LangGraph server was started with an old NGROK_URL, the webhook URL passed to CUA will be stale, and form submissions won't trigger calendar creation.
