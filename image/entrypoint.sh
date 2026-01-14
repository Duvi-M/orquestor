#!/usr/bin/env bash
set -euo pipefail

./start_all.sh
./novnc_startup.sh

echo "[entrypoint] starting worker api on :8080..."
python -m uvicorn computer_use_demo.worker_api_service.main:app --host 0.0.0.0 --port 8080 > /tmp/worker_api.log 2>&1 &
WORKER_PID=$!

echo "[entrypoint] starting streamlit on :8501..."
STREAMLIT_SERVER_PORT=8501 python -m streamlit run computer_use_demo/streamlit.py > /tmp/streamlit_stdout.log 2>&1 &

echo "✨ Computer Use Demo is ready!"
echo "➡️  worker API: :8080 | streamlit: :8501 | noVNC: :6080"

# Mantén el contenedor vivo mientras el Worker API siga vivo
wait $WORKER_PID

echo "[entrypoint] worker api exited. Dumping logs..."
tail -n 200 /tmp/worker_api.log || true
tail -n 200 /tmp/streamlit_stdout.log || true
exit 1