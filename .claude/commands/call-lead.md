# Call Lead

Make a real phone call to a lead using Twilio. The voice agent will know who to ask for.

## Usage

`/call-lead [index or phone]`

- `/call-lead --list` - List all available leads
- `/call-lead 0` - Call first lead from CSV
- `/call-lead 3` - Call fourth lead
- `/call-lead +14032661212` - Call specific number

## Requirements

- ngrok running on port 8080: `ngrok http 8080`
- Voice agent server running on port 8080

## Commands

```bash
cd /home/rajathdb/voice-agent

# Check ngrok is running
if ! curl -s localhost:4040/api/tunnels > /dev/null 2>&1; then
    echo "ERROR: ngrok not running. Start with: ngrok http 8080"
    exit 1
fi

# Default to --list if no argument
TARGET="${1:---list}"

python call_lead.py $TARGET
```
