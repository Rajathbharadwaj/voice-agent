#!/bin/bash
# Claude Code Notification Hook
# Usage: notify.sh <event_type> [message]

EVENT_TYPE="${1:-info}"
MESSAGE="${2:-Claude Code notification}"
ICON="dialog-information"

case "$EVENT_TYPE" in
    stop|complete)
        TITLE="Claude Code - Task Complete"
        ICON="dialog-positive"
        # Play completion sound if available
        paplay /usr/share/sounds/freedesktop/stereo/complete.oga 2>/dev/null &
        ;;
    tool)
        TITLE="Claude Code - Tool Called"
        ICON="dialog-scripts"
        ;;
    error)
        TITLE="Claude Code - Error"
        ICON="dialog-error"
        paplay /usr/share/sounds/freedesktop/stereo/dialog-error.oga 2>/dev/null &
        ;;
    calendar)
        TITLE="Claude Code - Calendar"
        ICON="calendar"
        ;;
    *)
        TITLE="Claude Code"
        ;;
esac

# Send desktop notification
notify-send -i "$ICON" "$TITLE" "$MESSAGE" 2>/dev/null

# Log to file for debugging
echo "[$(date '+%Y-%m-%d %H:%M:%S')] $EVENT_TYPE: $MESSAGE" >> /tmp/claude-notifications.log
