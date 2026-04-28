#!/bin/bash

# OpenColab Startup Script
# This script starts both the API server (uvicorn) and multiple mock workers.

# Configuration
export OLLAMA_BASE_URL="http://10.0.0.126:11434"
VENV_PATH="./venv/bin/activate"

echo "🚀 Starting OpenColab Services (Task Parallelism Mode)..."

# Check if venv exists
if [ ! -f "$VENV_PATH" ]; then
    echo "❌ Error: Virtual environment not found at $VENV_PATH"
    exit 1
fi

# Function to clean up processes on exit
cleanup() {
    echo ""
    echo "🛑 Shutting down OCO services..."
    kill $API_PID 2>/dev/null
    for pid in "${WORKER_PIDS[@]}"; do
        kill $pid 2>/dev/null
    done
    echo "✅ Done."
    exit
}

# Trap SIGINT SIGTERM
trap cleanup SIGINT SIGTERM

# Activate virtual environment
source "$VENV_PATH"

# Stop existing API service if it's already running
echo "🧹 Checking for existing services on port 8000..."
API_PROCESS=$(lsof -ti:8000)
if [ ! -z "$API_PROCESS" ]; then
    echo "🛑 Stopping existing API process (PID: $API_PROCESS)..."
    kill -9 $API_PROCESS 2>/dev/null
    sleep 1
fi

# Start API Server
echo "📡 Starting API Server (uvicorn)..."
uvicorn main:app --reload --port 8000 &
API_PID=$!

# Give API a moment to start
sleep 2

# Start Workers
NUM_WORKERS=3
echo "🤖 Starting $NUM_WORKERS Workers..."
WORKER_PIDS=()
for i in {1..3}; do
    python mock_worker.py &
    WORKER_PIDS+=($!)
done

echo "✨ All services are running!"
echo "   - API: http://localhost:8000"
echo "   - Parallel Workers: $NUM_WORKERS active"
echo "   - Press Ctrl+C to stop everything."
echo ""

# Wait for background processes
wait $API_PID "${WORKER_PIDS[@]}"
