from fastapi import FastAPI, BackgroundTasks, HTTPException, Form, File, UploadFile
from typing import List, Optional
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uuid
import os
import threading
from collections import defaultdict

import db
from graph import graph

app = FastAPI(title="OpenColab Orchestrator", version="0.1.0")

# Locks to prevent concurrent graph.invoke calls on the same thread
thread_locks = defaultdict(threading.Lock)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def add_no_cache_header(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

# Mount static directory for the Mission Control dashboard
os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

class SubmitRequest(BaseModel):
    prompt: str

class WebhookRequest(BaseModel):
    thread_id: str
    task_id: str
    result: str

class LogRequest(BaseModel):
    thread_id: str
    task_id: str
    message: str

class PriorityRequest(BaseModel):
    priority: int

from typing import List

@app.post("/submit")
async def submit_task(
    background_tasks: BackgroundTasks, 
    prompt: str = Form(...), 
    files: Optional[List[UploadFile]] = File(None), 
    paths: Optional[List[str]] = Form(None),
    file_paths: Optional[List[str]] = None
):
    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}
    
    # If file_paths is not provided (usual web submit), we process uploaded files
    if file_paths is None:
        file_paths = []
        # When called directly (e.g. from restart_thread), files might be a File/Form metadata object
        # if not explicitly passed as None or a list.
        if files and not hasattr(files, "default"): 
            os.makedirs(f"workspace/{thread_id}", exist_ok=True)
            for idx, file in enumerate(files):
                if hasattr(file, "filename") and file.filename:
                    # Use the provided relative path if available
                    rel_path = paths[idx] if paths and not hasattr(paths, "default") and idx < len(paths) else f"upload_{idx}_{file.filename}"
                    safe_rel_path = os.path.normpath(rel_path.lstrip('/'))
                    if safe_rel_path.startswith('..'):
                        safe_rel_path = f"upload_{idx}_{file.filename}"
                    
                    path = f"workspace/{thread_id}/{safe_rel_path}"
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    
                    with open(path, "wb") as f:
                        f.write(await file.read())
                    file_paths.append(path)
                    print(f"Saved uploaded file to {path}")

    initial_state = {
        "original_request": prompt,
        "file_paths": file_paths,
        "status": "planning",
        "task_list": [],
        "completed_results": {},
        "error": None
    }
    
    print(f"Starting thread {thread_id} for request: {prompt}")
    db.register_thread(thread_id, prompt)
    
    def _run_graph():
        try:
            graph.invoke(initial_state, config)
        except Exception as e:
            print(f"Graph invocation failed: {e}")
            
    background_tasks.add_task(_run_graph)
    return {"status": "accepted", "thread_id": thread_id}

@app.post("/webhook/result")
async def webhook_result(req: WebhookRequest, background_tasks: BackgroundTasks):
    config = {"configurable": {"thread_id": req.thread_id}}
    current_state = graph.get_state(config)
    
    if not current_state or not current_state.values:
        raise HTTPException(status_code=404, detail="Thread not found")
        
    print(f"Received result for task {req.task_id} on thread {req.thread_id}")

    # Mark task as completed in the DB so it's fully cleared
    db.complete_task(req.task_id)
    
    def resume_graph():
        # Prevent race condition where multiple workers finish simultaneously 
        # and overwrite each other's completed_results
        with thread_locks[req.thread_id]:
            try:
                # Re-fetch state inside lock to get the freshest data
                locked_state = graph.get_state(config)
                completed_results = dict(locked_state.values.get("completed_results", {}))
                completed_results[req.task_id] = req.result
                
                new_state = {
                    "completed_results": completed_results,
                    "status": "aggregating"
                }
                
                graph.invoke(new_state, config)
                print(f"Graph execution resumed and settled for thread {req.thread_id}")
            except Exception as e:
                print(f"Error resuming graph for thread {req.thread_id}: {e}")

    background_tasks.add_task(resume_graph)
    
    return {"status": "success", "message": "Result processed and graph resumed."}

@app.get("/api/threads")
async def get_threads():
    return db.get_recent_threads()

@app.get("/api/telemetry")
async def get_telemetry():
    return db.get_telemetry()

@app.get("/api/status/{thread_id}")
async def get_status(thread_id: str):
    config = {"configurable": {"thread_id": thread_id}}
    state = graph.get_state(config)
    
    if not state or not state.values:
        raise HTTPException(status_code=404, detail="Thread not found")
        
    vals = state.values
    
    
    # Inject active assigned workers from DB
    assignments = db.get_thread_assignments(thread_id)
    task_list = vals.get("task_list", [])
    
    # We mutate a copy for the UI
    ui_tasks = []
    for t in task_list:
        if isinstance(t, dict):
            task_dict = dict(t)
        else:
            task_dict = t.model_dump() # if pydantic
        
        task_dict['assigned_worker_id'] = assignments.get(task_dict['id'])
        ui_tasks.append(task_dict)
    
    
    # Check if thread is paused
    thread_info = db.get_thread_info(thread_id)
    thread_status = thread_info["status"] if thread_info else "active"
    
    status = vals.get("status", "planning")
    if thread_status == "paused" and status not in ["completed", "finished", "error"]:
        status = "paused"
    
    return {
        "status": status,
        "original_request": vals.get("original_request", ""),
        "task_list": ui_tasks,
        "completed_results": vals.get("completed_results", {}),
        "error": vals.get("error"),
        "priority": thread_info.get("priority", 0) if thread_info else 0
    }

@app.post("/webhook/log")
async def webhook_log(req: LogRequest):
    """Receives live execution logs from a worker."""
    db.push_log(req.task_id, req.thread_id, req.message)
    return {"status": "success"}

@app.get("/api/logs/{task_id}")
async def get_task_logs(task_id: str):
    """Returns the live execution logs for a specific task."""
    logs = db.get_task_logs(task_id)
    return {"logs": logs}

@app.post("/api/threads/{thread_id}/pause")
async def pause_thread(thread_id: str):
    db.set_thread_status(thread_id, 'paused')
    return {"status": "success", "message": "Thread paused"}

@app.post("/api/threads/{thread_id}/resume")
async def resume_thread(thread_id: str):
    db.set_thread_status(thread_id, 'active')
    return {"status": "success", "message": "Thread resumed"}

@app.post("/api/threads/{thread_id}/priority")
async def set_thread_priority(thread_id: str, req: PriorityRequest):
    db.set_thread_priority(thread_id, req.priority)
    return {"status": "success", "message": f"Priority set to {req.priority}"}

@app.post("/api/threads/{thread_id}/restart")
async def restart_thread(thread_id: str, background_tasks: BackgroundTasks):
    info = db.get_thread_info(thread_id)
    if not info:
        raise HTTPException(status_code=404, detail="Thread not found")
        
    prompt = info.get("prompt")
    if not prompt:
        raise HTTPException(status_code=400, detail="Cannot restart thread without original prompt")
        
    # Get original file paths from LangGraph state
    config = {"configurable": {"thread_id": thread_id}}
    state = graph.get_state(config)
    original_file_paths = []
    if state and state.values:
        original_file_paths = state.values.get("file_paths", [])

    # Pause the original thread so it stops consuming worker queue resources
    db.set_thread_status(thread_id, 'paused')
        
    # Launch as new job
    return await submit_task(background_tasks=background_tasks, prompt=prompt, file_paths=original_file_paths)

@app.post("/api/threads/{thread_id}/approve_dag")
async def approve_dag(thread_id: str, background_tasks: BackgroundTasks):
    config = {"configurable": {"thread_id": thread_id}}
    state = graph.get_state(config)
    
    if not state or not state.values:
        raise HTTPException(status_code=404, detail="Thread not found")
        
    vals = state.values
    if vals.get("status") != "pending_approval":
        raise HTTPException(status_code=400, detail="Thread is not waiting for DAG approval")
        
    def resume_graph():
        with thread_locks[thread_id]:
            try:
                new_state = {
                    "status": "dispatching"
                }
                graph.invoke(new_state, config)
                print(f"Graph resumed (DAG Approved) for thread {thread_id}")
            except Exception as e:
                print(f"Error resuming graph for thread {thread_id}: {e}")

    background_tasks.add_task(resume_graph)
    
    return {"status": "success", "message": "DAG approved and dispatched."}

