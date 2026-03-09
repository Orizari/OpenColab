# OpenColab Orchestrator (Phase 1)

This project implements the Phase 1 Local Development Orchestrator for OpenColab. It is a robust state machine using Python, FastAPI, LangGraph, and SQLite that takes a complex user prompt, splits it into dependencies, dispatches them, pauses, and resumes when a worker completes a sub-task.

## Architecture Architecture

- **FastAPI Core**: Serves as the API interface (`POST /submit`, `POST /webhook/result`).
- **LangGraph State Machine**: Models the orchestrator's decision-making process (planning, dispatching, aggregating).
- **SQLite Database**:
  - Acts as a local queue for pending tasks (`queue.db`).
  - Stores the state machine checkpoints for LangGraph, allowing execution threads to sleep and run asynchronously (`checkpoints.db`).
- **Mock Worker**: A separate standalone script polling the local queue DB and simulating async work.

## Local Setup & Run Instructions

### 1. Environment Setup
Requires Python 3.11+.

```bash
cd /Users/mendejurukovski/Documents/OpenColab

# Create a virtual environment
python3 -m venv venv

# Activate it (on macOS/Linux)
source venv/bin/activate

# Install requirements
pip install -r requirements.txt
```

### 2. Run the Main API Server
In terminal 1, run the FastAPI application:
```bash
# Ensure you are in the virtual environment
source venv/bin/activate
uvicorn main:app --reload
```
*This will create the SQLite databases (`queue.db` and checkpoints) automatically.*

### 3. Run the Mock Worker
In terminal 2, run the simulated external worker:
```bash
# Ensure you are in the virtual environment
source venv/bin/activate
python mock_worker.py
```

### 4. Execute a Test Request
In terminal 3, send a complex problem to the orchestrator:
```bash
curl -X POST http://localhost:8000/submit \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Generate a detailed report on local AI orchestration"}'
```

### 5. Observe the execution
- The FastAPI server will create the workflow graph, dispatch Task 1, and go to **sleep**.
- The `mock_worker.py` script will poll Task 1, wait for 5 seconds to simulate work, and then execute an HTTP POST to the `/webhook/result` endpoint.
- The FastAPI server wakes up via the webhook, processes the result for Task 1, and subsequently dispatches Tasks 2 and 3 concurrently.
- The mock worker sequentially polls and processes Tasks 2 and 3.
- The workflow finishes gracefully.
