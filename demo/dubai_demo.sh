#!/usr/bin/env bash
set -euo pipefail

API="http://127.0.0.1:9000"

json_get_session_id() {
python - <<'PY'
import sys, json
print(json.load(sys.stdin)["session_id"])
PY
}

echo "▶ Creating session (Dubai)..."
SID=$(curl -s -X POST "$API/sessions" | json_get_session_id)

echo "Session: $SID"
echo "Open UI: $API/sessions/$SID/ui"
echo
echo "▶ Sending message..."
curl -s -X POST "$API/sessions/$SID/messages" \
  -H "Content-Type: application/json" \
  -d '{"text":"Search the weather in Dubai"}' | cat

echo
echo "▶ Watch SSE in another terminal:"
echo "curl -N $API/sessions/$SID/events"