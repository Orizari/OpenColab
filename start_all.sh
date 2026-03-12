#!/bin/bash

# OpenColab Startup Script
# This script starts both the API server (uvicorn) and the mock worker.

# Configuration
export OLLAMA_BASE_URL="http://10.0.0.126:11434"
VENV_PATH="./venv/bin/activate"

echo "🚀 Starting OpenColab Services..."

# Check if venv exists
if [ ! -f "$VENV_PATH" ]; then
    echo "❌ Error: Virtual environment not found at $VENV_PATH"
    exit 1
fi

# Function to clean up processes on exit
cleanup() {
    echo ""
    echo "🛑 Shutting down services..."
    kill $API_PID $WORKER_PID 2>/dev/null
    wait $API_PID $WORKER_PID 2>/dev/null
    echo "✅ Done."
    exit
}

# Trap Ctrl+C (SIGINT) and SIGTERM
trap cleanup SIGINT SIGTERM

# Activate virtual environment
source "$VENV_PATH"

# Start API Server
echo "📡 Starting API Server (uvicorn)..."
uvicorn main:app --reload --port 8000 &
API_PID=$!

# Give API a moment to start
sleep 2

# Start Worker
echo "🤖 Starting Worker (mock_worker.py)..."
python mock_worker.py &
WORKER_PID=$!

echo "✨ Both services are running!"
echo "   - API: http://localhost:8000"
echo "   - Logs will appear below (Shift+G to scroll to bottom in some pagers)"
echo "   - Press Ctrl+C to stop both services."
echo ""

# Wait for background processes
wait
