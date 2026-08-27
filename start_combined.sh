echo "[INFO] Starting Igot Aurora Agent API..."
uvicorn main:app --host 0.0.0.0 --port 4020 &
API_PID=$!

echo "[INFO] Starting 4 Kafka Workers..."
python3 kafka_worker.py --workers 4 &
WORKER_PID=$!

wait -n

echo "[ERROR] One process exited. Shutting down..."
kill $WORKER_PID $API_PID 2>/dev/null || true