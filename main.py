from fastapi import FastAPI, BackgroundTasks, HTTPException, Form, File, UploadFile
from fastapi.responses import RedirectResponse
from typing import List, Optional
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import os
import json
import sqlite3
import subprocess
from datetime import datetime
import uuid
import threading
from collections import defaultdict

import db
from graph import graph

app = FastAPI(title="OpenColab Orchestrator", version="0.1.0")

# Locks to prevent concurrent graph.invoke calls on the same thread
thread_locks = defaultdict(threading.Lock)
git_lock = threading.Lock()

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
app.mount("/static", StaticFiles(directory="static", html=True), name="static")

@app.get("/")
async def read_root():
    return RedirectResponse(url="/static/")

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

class HeartbeatRequest(BaseModel):
    worker_id: str
    model: str
    status: Optional[str] = "idle"
    metadata: Optional[dict] = None

class ClaimRequest(BaseModel):
    worker_id: str

class TraceRequest(BaseModel):
    task_id: str
    thread_id: str
    prompt: str
    reasoning: str
    result: str

class MemorySaveRequest(BaseModel):
    topic: str
    content: str

class MemorySearchRequest(BaseModel):
    query: str

@app.post("/api/worker/heartbeat")
async def worker_heartbeat(req: HeartbeatRequest):
    db.heartbeat(req.worker_id, req.status, req.model, req.metadata)
    return {"status": "success"}

@app.post("/api/worker/poll")
async def worker_poll(req: ClaimRequest):
    """Atomically poll and claim a task for a specific worker."""
    task = db.poll_task(req.worker_id)
    if task:
        return {"status": "assigned", "task": task}
    return {"status": "empty"}

@app.post("/api/worker/submit")
async def worker_submit(req: WebhookRequest, background_tasks: BackgroundTasks):
    """Unified endpoint for external agents to submit results."""
    return await webhook_result(req, background_tasks)

@app.post("/api/worker/trace")
async def worker_trace(req: TraceRequest):
    db.save_trace(req.task_id, req.thread_id, req.prompt, req.reasoning, req.result)
    return {"status": "success"}

@app.post("/api/worker/memory/save")
async def worker_memory_save(req: MemorySaveRequest):
    db.save_memory(req.topic, req.content)
    return {"status": "success"}

@app.post("/api/worker/memory/search")
async def worker_memory_search(req: MemorySearchRequest):
    results = db.search_memory(req.query)
    return {"status": "success", "results": results}

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
        if files and not hasattr(files, "default"): 
            os.makedirs(f"workspace/{thread_id}", exist_ok=True)
            for idx, file in enumerate(files):
                if hasattr(file, "filename") and file.filename:
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
        "critiques": {},
        "error": None
    }
    
    print(f"Starting thread {thread_id} for request: {prompt}")
    db.register_thread(thread_id, prompt)
    db.push_log("System", thread_id, f"Initializing Orchestrator for: {prompt}")
    
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
    
    # Check for errors and handle retries
    if req.result.startswith("Error:"):
        print(f"⚠️ Task {req.task_id} failed with error. Checking for retry...")
        if db.fail_task(req.task_id):
            db.push_log(req.task_id, req.thread_id, f"⚠️ Task failed: {req.result}. Retrying...")
            return {"status": "retrying", "message": "Task failed and reset to pending for retry."}
        else:
            print(f"❌ Task {req.task_id} reached max attempts.")
            db.push_log(req.task_id, req.thread_id, f"❌ Task failed after max attempts: {req.result}")
            # Fall through to mark as complete with error

    db.complete_task(req.task_id)
    
    def resume_graph():
        with thread_locks[req.thread_id]:
            try:
                locked_state = graph.get_state(config)
                completed_results = dict(locked_state.values.get("completed_results", {}))
                completed_results[req.task_id] = req.result
                
                # Instruction/Calculation Routing for Pure Swarm
                next_status = "aggregating"
                if req.task_id.startswith("SYSTEM_PLANNER"):
                    next_status = "planning"
                elif req.task_id.startswith("SYSTEM_AGGREGATOR"):
                    next_status = "aggregating"
                elif req.task_id.startswith("SYSTEM_CRITIQUE"):
                    next_status = "critiquing"
                elif req.task_id.startswith("SYSTEM_REFLECTION"):
                    next_status = "reflecting"
                
                new_state = {
                    "completed_results": completed_results,
                    "status": next_status
                }
                
                graph.invoke(new_state, config)
                print(f"Graph execution resumed and settled for thread {req.thread_id} with status {next_status}")
            except Exception as e:
                print(f"Error resuming graph for thread {req.thread_id}: {e}")

    background_tasks.add_task(resume_graph)
    
    # Autonomous Version Control for Improvements
    if req.task_id.startswith("APPLY_TASK_") and not req.result.startswith("Error:"):
        def autonomous_commit():
            with git_lock:
                print(f"🚀 Improvement {req.task_id} applied. Committing to GitHub...")
                try:
                    imp_id = req.task_id.replace("APPLY_TASK_", "")
                    imp = db.get_improvement(int(imp_id))
                    raw_desc = imp['description'] if imp else "Unknown improvement"
                    clean_desc = "".join(c for c in raw_desc[:80] if c.isalnum() or c in " -_.").strip()
                    
                    # 1. Check if there are actual changes
                    status_proc = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
                    if not status_proc.stdout.strip():
                        print(f"ℹ️ No changes detected for improvement {req.task_id}. Skipping commit.")
                        db.push_log(req.task_id, req.thread_id, "ℹ️ No file changes detected. System state remains synchronized.")
                        return

                    # 2. Stage, Commit and Push
                    subprocess.run(["git", "add", "."], check=True)
                    subprocess.run(["git", "commit", "-m", f"Autonomous Upgrade: {clean_desc}"], check=True)
                    subprocess.run(["git", "push", "origin", "main"], check=True)
                    print(f"✅ Changes for {req.task_id} committed and pushed.")
                    db.push_log(req.task_id, req.thread_id, "✅ Changes committed and pushed to GitHub.")
                except Exception as e:
                    print(f"❌ Failed to commit autonomous changes: {e}")
                    db.push_log(req.task_id, req.thread_id, f"⚠️ Failed to push to GitHub: {e}")

        background_tasks.add_task(autonomous_commit)

    return {"status": "success", "message": "Result processed and graph resumed."}

@app.get("/api/threads")
async def get_threads():
    return db.get_recent_threads()

@app.get("/api/telemetry")
async def get_telemetry():
    return db.get_telemetry()

@app.get("/api/improvements")
async def get_improvements():
    """Returns the list of proposed system improvements sorted by votes."""
    return db.get_top_improvements()

@app.get("/api/status/{thread_id}")
async def get_status(thread_id: str):
    config = {"configurable": {"thread_id": thread_id}}
    state = graph.get_state(config)
    
    if not state or not state.values:
        raise HTTPException(status_code=404, detail="Thread not found")
        
    vals = state.values
    realtime_tasks = db.get_task_statuses(thread_id)
    task_list = vals.get("task_list", [])
    
    ui_tasks = []
    any_processing = False
    for t in task_list:
        task_id = t["id"] if isinstance(t, dict) else t.id
        task_dict = dict(t) if isinstance(t, dict) else t.model_dump()
        
        # Merge real-time status from queue DB if it exists
        if task_id in realtime_tasks:
            rt = realtime_tasks[task_id]
            q_status = rt['status']
            # Map 'pending' in queue to 'queued' in UI if the graph dispatched it
            if q_status == 'pending' and task_dict.get('status') == 'dispatched':
                task_dict['status'] = 'queued'
            else:
                task_dict['status'] = q_status
            task_dict['assigned_worker_id'] = rt['worker_id']
            task_dict['attempts'] = rt.get('attempts', 0)
            
            # Add replica stats
            task_dict['replica_stats'] = db.get_replica_stats(task_id, thread_id)
            
            if q_status == 'processing':
                any_processing = True
        
        ui_tasks.append(task_dict)
    
    # If the graph is sleeping but workers are working, show as 'executing'
    ui_status = vals.get("status", "idle")
    if ui_status == 'sleeping' and any_processing:
        ui_status = 'processing'
    elif ui_status == 'sleeping' and not any_processing:
        # Check if there are still queued tasks
        if any(t['status'] == 'queued' for t in ui_tasks):
            ui_status = 'dispatched'
    
    thread_info = db.get_thread_info(thread_id)
    
    # Override status if thread is explicitly paused in DB
    if thread_info and thread_info.get("status") == "paused" and ui_status not in ["completed", "finished", "error"]:
        ui_status = "paused"

    return {
        "status": ui_status,
        "original_request": thread_info['prompt'] if thread_info else "Unknown",
        "task_list": ui_tasks,
        "completed_results": vals.get("completed_results", {}),
        "error": vals.get("error"),
        "priority": thread_info.get("priority", 0) if thread_info else 0
    }

@app.post("/webhook/log")
async def webhook_log(req: LogRequest):
    db.push_log(req.task_id, req.thread_id, req.message)
    return {"status": "success"}

@app.get("/api/logs/all")
async def get_all_logs(since: str = None):
    logs = db.get_all_logs(since)
    return {"logs": logs}

@app.get("/api/logs/{task_id}")
async def get_task_logs(task_id: str, thread_id: str = None):
    logs = db.get_task_logs(task_id, thread_id)
    return {"logs": logs}

@app.post("/api/threads/{thread_id}/pause")
async def pause_thread(thread_id: str):
    db.set_thread_status(thread_id, 'paused')
    return {"status": "success", "message": "Thread paused"}

@app.delete("/api/threads/{thread_id}")
async def delete_thread(thread_id: str):
    db.delete_thread_data(thread_id)
    return {"status": "success"}

@app.post("/api/tasks/{task_id}/restart")
async def restart_single_task(task_id: str, background_tasks: BackgroundTasks):
    thread_id = db.get_task_thread(task_id)
    if not thread_id:
        raise HTTPException(status_code=404, detail="Task or Thread not found")
    
    # Identify which IDs to restart in DB (could be a parent ID or a direct replica ID)
    replicas = db.get_replicas(task_id)
    ids_to_restart = replicas if replicas else [task_id]
    
    for rid in ids_to_restart:
        db.restart_task(rid)
    
    # Update LangGraph state to remove the old result (for parent and all replicas)
    config = {"configurable": {"thread_id": thread_id}}
    state = await graph.aget_state(config)
    completed_results = state.values.get("completed_results", {})
    
    keys_to_clear = [task_id] + replicas + [f"SYSTEM_AGGREGATOR_{task_id}"]
    changed = False
    for k in keys_to_clear:
        if k in completed_results:
            del completed_results[k]
            changed = True
    
    if changed:
        await graph.aupdate_state(config, {"completed_results": completed_results})
    
    # Resume graph to re-evaluate
    background_tasks.add_task(resume_graph, thread_id)
    return {"status": "success", "message": f"Task {task_id} and its {len(replicas)} replicas restarted."}

@app.post("/api/threads/{thread_id}/resume")
async def resume_thread(thread_id: str):
    db.set_thread_status(thread_id, 'active')
    return {"status": "success", "message": "Thread resumed"}

@app.post("/api/system/prompts/{name}")
async def system_update_prompt(name: str, req: dict):
    content = req.get("content")
    if not content:
        raise HTTPException(status_code=400, detail="Missing content")
    db.update_system_prompt(name, content)
    return {"status": "success"}

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
        
    config = {"configurable": {"thread_id": thread_id}}
    state = graph.get_state(config)
    original_file_paths = []
    if state and state.values:
        original_file_paths = state.values.get("file_paths", [])

    db.set_thread_status(thread_id, 'paused')
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
                new_state = {"status": "dispatching"}
                graph.invoke(new_state, config)
                print(f"Graph resumed (DAG Approved) for thread {thread_id}")
            except Exception as e:
                print(f"Error resuming graph for thread {thread_id}: {e}")

    background_tasks.add_task(resume_graph)
    return {"status": "success", "message": "DAG approved and execution resumed"}

@app.post("/api/improvements/{item_id}/apply")
async def apply_improvement_endpoint(item_id: int, background_tasks: BackgroundTasks):
    imp = db.get_improvement(item_id)
    if not imp:
        raise HTTPException(status_code=404, detail="Improvement not found")
        
    # Create a system thread for implementation
    thread_id = f"SYSTEM_EVO_{uuid.uuid4().hex[:8]}"
    db.register_thread(thread_id, f"APPLY IMPROVEMENT: {imp['description'][:50]}...")
    
    # Task for the worker
    task_id = f"APPLY_TASK_{item_id}"
    prompt = f"""You are the OCO System Implementation Specialist.

TASK: Apply the following improvement to the codebase.
DESCRIPTION: {imp['description']}
PATCH DATA: {imp['patch_data'] or 'N/A'}

INSTRUCTIONS:
1. Review the current code and the proposed improvement.
2. If this is a prompt improvement, output:
   PROMPT_UPDATE: PROMPT_NAME
   NEW_PROMPT: 
   [Full Prompt Text]
   END_PROMPT_UPDATE

3. If this is a code improvement, output:
   FILE_WRITE: filename.py
   CONTENT:
   [Full File Content]
   END_FILE_WRITE

FORMAT: You MUST use the tags above to perform the changes."""

    db.push_task(task_id, thread_id, {"description": prompt})
    db.apply_improvement(item_id)
    
    # Initialize graph state for this system thread so UI can poll it
    initial_state = {
        "original_request": f"Apply Improvement: {imp['description']}",
        "file_paths": [],
        "status": "processing",
        "task_list": [{"id": task_id, "description": f"Apply {imp['description'][:100]}", "status": "dispatched", "dependencies": []}],
        "completed_results": {},
        "critiques": {},
        "error": None
    }
    graph.update_state({"configurable": {"thread_id": thread_id}}, initial_state)
    
    return {"status": "success", "thread_id": thread_id}

@app.get("/api/improvements/all")
async def get_all_improvements():
    return db.get_top_improvements()

@app.delete("/api/improvements/{item_id}")
async def delete_improvement(item_id: int):
    db.delete_improvement(item_id)
    return {"status": "success"}

@app.delete("/api/improvements")
async def clear_improvements():
    db.clear_all_improvements()
    return {"status": "success"}

@app.delete("/api/threads/{thread_id}")
async def delete_thread(thread_id: str):
    """Deletes all data for a thread, including DB entries and LangGraph checkpoints."""
    try:
        # 1. Delete specialized OCO data (logs, traces, tasks)
        db.delete_thread_data(thread_id)
        
        # 2. Delete LangGraph checkpoints from checkpoints.db
        with sqlite3.connect("checkpoints.db") as conn:
            conn.execute("DELETE FROM checkpoints WHERE thread_id = ?", (thread_id,))
            conn.execute("DELETE FROM writes WHERE thread_id = ?", (thread_id,))
            conn.commit()
            
        print(f"🗑️ Deleted thread and all data for: {thread_id}")
        return {"status": "success", "message": f"Thread {thread_id} and all associated data deleted."}
    except Exception as e:
        print(f"Error deleting thread {thread_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/improvements/{item_id}/apply")
async def apply_improvement(item_id: int):
    imps = db.get_top_improvements(100)
    improvement = next((i for i in imps if i["id"] == item_id), None)
    
    if not improvement:
        raise HTTPException(status_code=404, detail="Improvement not found")
        
    patch = improvement.get("patch_data")
    if not patch:
        raise HTTPException(status_code=400, detail="Improvement has no code patch")
        
    import subprocess
    try:
        # For this implementation, we simulate patch application by saving to a file.
        # In a real environment, we'd use 'git apply' or similar code mutation logic.
        patch_path = f"applied_improvement_{item_id}.patch"
        with open(patch_path, "w") as f:
            f.write(patch)
            
        print(f"Improvement {item_id} applied (simulated).")
        db.update_improvement_status(item_id, "applied", "Patch saved to workspace.")
        
        return {"status": "success", "message": f"Improvement {item_id} applied. Patch saved as {patch_path}."}
    except Exception as e:
        db.update_improvement_status(item_id, "failed", str(e))
        raise HTTPException(status_code=500, detail=str(e))