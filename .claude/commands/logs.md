# View Voice Agent Logs

View the LangGraph server logs, filtered by type.

## Usage

`/logs [type]`

Types:
- `all` - Full logs (default)
- `runs` - Just run start/complete messages
- `calendar` - Calendar/tool operations
- `errors` - Just errors

## Commands

```bash
LOG_FILE="/tmp/langgraph-dev.log"

case "${1:-all}" in
    runs)
        grep -E "(Starting background run|Background run succeeded|Created run)" "$LOG_FILE" | tail -30
        ;;
    calendar)
        grep -E "\[Calendar\]|\[Tools\]" "$LOG_FILE" | tail -30
        ;;
    errors)
        grep -iE "(error|exception|failed|traceback)" "$LOG_FILE" | tail -50
        ;;
    *)
        tail -100 "$LOG_FILE"
        ;;
esac
```
