#!/usr/bin/env bash
set -euo pipefail

API="${API:-http://127.0.0.1:9000}"

echo "▶ Creating session A (Dubai)..."
RESP_A="$(curl -s -X POST "$API/sessions")"
SESSION_A="$(echo "$RESP_A" | python3 -c 'import sys,json; print(json.load(sys.stdin)["session_id"])')"
UI_A="$(echo "$RESP_A" | python3 -c 'import sys,json; print(json.load(sys.stdin)["ui_url"])')"

echo "▶ Creating session B (Tokyo/New York)..."
RESP_B="$(curl -s -X POST "$API/sessions")"
SESSION_B="$(echo "$RESP_B" | python3 -c 'import sys,json; print(json.load(sys.stdin)["session_id"])')"
UI_B="$(echo "$RESP_B" | python3 -c 'import sys,json; print(json.load(sys.stdin)["ui_url"])')"

echo
echo "Session A (Dubai): $SESSION_A"
echo "UI A: $UI_A"
echo
echo "Session B (Tokyo/New York): $SESSION_B"
echo "UI B: $UI_B"
echo

echo "▶ Starting SSE streams in background..."
LOG_A="/tmp/session_a_${SESSION_A}.sse.log"
LOG_B="/tmp/session_b_${SESSION_B}.sse.log"

# -N: no buffer
curl -N "$API/sessions/$SESSION_A/events" > "$LOG_A" &
PID_A=$!

curl -N "$API/sessions/$SESSION_B/events" > "$LOG_B" &
PID_B=$!

sleep 1

echo "▶ Sending messages concurrently..."
# Caso 1: Dubai
curl -s -X POST "$API/sessions/$SESSION_A/messages" \
  -H "Content-Type: application/json" \
  -d '{"text":"Search the weather in Dubai and tell me the temperature."}' >/dev/null &

# Caso 2: Tokyo (y luego New York SIN esperar)
curl -s -X POST "$API/sessions/$SESSION_B/messages" \
  -H "Content-Type: application/json" \
  -d '{"text":"Find the best sushi restaurant in Tokyo."}' >/dev/null &

wait
echo " Messages sent."

echo
echo "▶ Now, while Tokyo is running, send New York on the SAME session B (non-blocking test)..."
# Nota: si tu API marca busy=409, esto es EXACTAMENTE el punto del challenge:
# debes mostrar concurrencia real con dos sesiones en paralelo.
# Aquí demostramos: A y B en paralelo. Y dentro de B, ver si soporta "pipeline".
curl -s -X POST "$API/sessions/$SESSION_B/messages" \
  -H "Content-Type: application/json" \
  -d '{"text":"Now, without waiting, also search the weather in New York."}' >/dev/null || true

echo
echo "▶ Streaming responses for a few seconds..."
sleep 6

echo
echo "▶ Tail Session A SSE (Dubai):"
tail -n 30 "$LOG_A" || true

echo
echo "▶ Tail Session B SSE (Tokyo/New York):"
tail -n 30 "$LOG_B" || true

echo
echo "▶ Open UIs:"
echo "Dubai UI:  $UI_A"
echo "Tokyo/NY UI: $UI_B"
echo
echo "▶ Logs:"
echo "$LOG_A"
echo "$LOG_B"

echo
echo "▶ Cleanup SSE background readers..."
kill "$PID_A" "$PID_B" >/dev/null 2>&1 || true

echo "Demo complete."