#!/bin/bash

# Production startup script for AutoInfraRemediation
# Simple version - Docker Compose handles dependency waiting

set -e

echo "[STARTUP] AutoInfraRemediation Production Mode"
echo "[STARTUP] Starting at $(date)"

# Docker Compose ensures dependencies are healthy before starting this container
echo "[STARTUP] Dependencies handled by Docker Compose health checks"

# Set Python path
export PYTHONPATH=/app

# Start Temporal worker in background
echo "[STARTUP] Starting Temporal worker..."
python worker.py &
WORKER_PID=$!

# Give worker time to start
sleep 5

# Start FastAPI server
echo "[STARTUP] Starting FastAPI API server..."
uvicorn api:app \
  --host 0.0.0.0 \
  --port 8001 \
  --workers 1 \
  --access-log &
API_PID=$!

# Signal handler for graceful shutdown
shutdown() {
  echo "[SHUTDOWN] Received shutdown signal"
  echo "[SHUTDOWN] Stopping API server (PID: $API_PID)"
  kill -TERM $API_PID 2>/dev/null || true
  echo "[SHUTDOWN] Stopping Temporal worker (PID: $WORKER_PID)" 
  kill -TERM $WORKER_PID 2>/dev/null || true
  wait $API_PID $WORKER_PID
  echo "[SHUTDOWN] Shutdown complete"
  exit 0
}

# Register signal handlers
trap shutdown SIGTERM SIGINT

# Wait for both processes  
wait $API_PID $WORKER_PID