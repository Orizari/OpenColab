from typing import TypedDict, Annotated, Literal, Optional, List
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite import SqliteSaver
import sqlite3
import json
import uuid
import os
import sys
import subprocess
from pathlib import Path
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("graph")

def validate_paths(paths: List[str]) -> List[str]:
    valid = []
    for p in paths:
        try:
            path_obj = Path(p).resolve()
            if path_obj.exists():
                valid.append(str(path_obj))
            else:
                logger.warning(f"File path does not exist: {p}")
        except Exception as e:
            logger.error(f"Error validating path {p}: {e}")
    return valid

import db
db.init_db()

class AgentState(TypedDict):
    original_request: str
    file_paths: List[str]
    task_list: list[dict]
    completed_results: dict[str, str]
    replica_results: dict[str, list[str]] # task_id -> list of raw results
    k_factor: int # Global default
    critiques: dict[str, dict]
    status: str
    error: Optional[str]

class TaskSchema(BaseModel):
    id: str = Field(description="Unique identifier for the task")
    description: str = Field(description="Instruction for the task")
    dependencies: List[str] = Field(description="Task IDs that must be completed first")
    k_factor: Optional[int] = Field(default=1, description="Number of worker replicas (1=simple, 3=needs consensus)")


class TaskListSchema(BaseModel):
    tasks: List[TaskSchema]

# =====================================================================
# PROMPT TEMPLATES — Structured prompts for each orchestration role
# =====================================================================

PLANNER_PROMPT = """You are the OCO Architect — a task planning specialist.

Your job is to break down the user's request into a clear, executable list of tasks.

## Rules
1. Each task must be **atomic** — it should do ONE thing well.
2. Each task description must be **self-contained** — a worker reading just the description should understand exactly what to do.
3. Keep the plan **minimal** — use the fewest tasks needed. Do NOT over-decompose simple requests.
4. For simple requests (jokes, short answers, single questions), use just 1 task.
5. Set `k_factor` to 1 for most tasks. Only use 2-3 for tasks where multiple perspectives would genuinely improve quality (e.g., creative writing, complex analysis).
6. Use `dependencies` to define execution order. Tasks without dependencies run first.

## Output Format — STRICT JSON ONLY
```json
{{
  "tasks": [
    {{
      "id": "task_1",
      "description": "A clear, detailed instruction for what this task should produce",
      "dependencies": [],
      "k_factor": 1
    }}
  ]
}}
```

## User Request
{request}

OUTPUT ONLY THE JSON. No explanation, no markdown outside the JSON block."""

SYNTHESIZER_PROMPT = """You are the OCO Synthesizer — an expert at combining multiple AI responses into one optimal answer.

You have received {k_count} independent responses to the same task. Your job is to produce the single best answer.

## Task Description
{task_description}

## Evaluation Criteria
1. **Accuracy**: Prefer factually correct information. If responses conflict, use the most well-reasoned one.
2. **Completeness**: Include all valuable points from across the responses.
3. **Clarity**: The final answer should be clear, well-structured, and easy to read.
4. **No meta-commentary**: Do NOT mention that you are synthesizing multiple responses. Just provide the best answer directly.

## Responses to Synthesize
{replicas}

## Your Synthesized Answer:"""

CRITIQUE_PROMPT = """You are the OCO Quality Auditor.

Evaluate the following task result on these criteria:
1. **Relevance** (0-3): Does it actually answer what was asked?
2. **Quality** (0-4): Is the answer accurate, detailed, and well-structured?
3. **Completeness** (0-3): Is anything important missing?

Task: {task_description}
Result: {result}

Provide your assessment and a final Score: X/10."""

REFLECTION_PROMPT = """You are the OCO Strategist.

Review these completed task results and extract 1-3 concise, actionable insights about how the system performed. Focus on:
- What worked well
- What could be improved in future task decomposition
- Any patterns in quality issues

Results:
{results}

Provide brief, actionable insights (not a summary of the results themselves)."""

EVOLUTION_PROMPT = """You are the OCO Evolutionary Strategist.

Review the following aggregate system data, including recent execution traces, pending improvement suggestions, and the current system source code. 

Your goal is to synthesize 1-2 high-impact, architectural improvements to the system itself (logic, GUI, optimization, or strategy).

## Aggregate Context
{context}

## System Source Code
{source_code}

## Task
1. Analyze the patterns in the traces (failures, successes, bottlenecks).
2. Look for common themes in the pending suggestions.
3. Propose a specific, code-level improvement. 
4. **Safety First**: Prefer `FILE_PATCH` for modifying specific blocks. Use `FILE_WRITE` only when creating new files.

## Output Convention
### To update an existing file (Surgical Edit):
FILE_PATCH: filename.py
SEARCH:
[exact code block to find]
REPLACE:
[new code block to insert]
END_FILE_PATCH

### To create or overwrite a full file:
FILE_WRITE: filename.py
CONTENT:
[full content]
END_FILE_WRITE

Format your response as a clear, technical proposal."""


def planner_node(state: AgentState, config: RunnableConfig) -> dict:
    """
    Instructions: Orchestrator generates the prompt.
    Calculation: Worker executes it.
    """
    print("--- PLANNER NODE ---")
    thread_id = config["configurable"]["thread_id"]
    completed_results = state.get("completed_results", {})
    plan_task_id = f"SYSTEM_PLANNER_{thread_id}"
    
    # If the swarm already finished the plan, parse it
    if plan_task_id in completed_results:
        print("Planner: Parsing worker plan output...")
        try:
            raw_plan = completed_results[plan_task_id]
            # Extract JSON from potential markdown wrapping
            if "```json" in raw_plan:
                raw_plan = raw_plan.split("```json")[1].split("```")[0]
            elif "```" in raw_plan:
                raw_plan = raw_plan.split("```")[1].split("```")[0]
            
            data = json.loads(raw_plan.strip())
            tasks = []
            for t in data.get("tasks", []):
                tasks.append({
                    "id": t["id"], 
                    "description": t["description"], 
                    "dependencies": t.get("dependencies", []), 
                    "k_factor": min(t.get("k_factor", 1), 5),  # Cap at 5
                    "status": "pending"
                })
            
            if not tasks:
                return {"status": "error", "error": "Planner produced empty task list"}
            
            print(f"Planner: Created {len(tasks)} tasks")
            return {"task_list": tasks, "status": "pending_approval"}
        except Exception as e:
            print(f"Error parsing swarm plan: {e}")
            return {"status": "error", "error": f"Plan Parsing Failed: {e}"}

    # Otherwise, dispatch the planning job
    req = state.get("original_request", "")
    print(f"Planner: Dispatching planning task for: '{req}'")
    prompt_tpl = db.get_system_prompt("PLANNER_PROMPT", PLANNER_PROMPT)
    
    # Inject historical context
    historical_context = db.get_relevant_insights(req)
    
    if "{historical_context}" in prompt_tpl:
        instruction = prompt_tpl.format(request=req, historical_context=historical_context)
    else:
        # Append if placeholder missing but we have context
        instruction = prompt_tpl.format(request=req)
        instruction += f"\n\n## HISTORICAL CONTEXT (LESSONS LEARNED)\n{historical_context}"

    db.push_task(plan_task_id, thread_id, {"description": instruction})
    return {"status": "sleeping"}

def critique_node(state: AgentState, config: RunnableConfig) -> dict:
    """
    Pure Swarm Critique: Dispatches critique tasks to workers.
    """
    print("--- CRITIQUE NODE ---")
    thread_id = config["configurable"]["thread_id"]
    completed_results = state.get("completed_results", {})
    critiques = state.get("critiques", {})
    new_critiques = {}
    
    for task_id, result in completed_results.items():
        # Only critique user tasks, not system tasks or replicas
        if task_id.startswith("SYSTEM_") or "_rep" in task_id:
            continue
        if task_id in critiques:
            continue
            
        crit_task_id = f"SYSTEM_CRITIQUE_{task_id}"
        
        if crit_task_id in completed_results:
            raw_crit = completed_results[crit_task_id]
            score = 10
            try:
                if "score:" in raw_crit.lower(): 
                    # Extract number after "Score:"
                    score_text = raw_crit.lower().split("score:")[1].strip()[:5]
                    score = int(''.join(c for c in score_text if c.isdigit())[:2])
            except: pass
            new_critiques[task_id] = {"score": score, "text": raw_crit}
            continue

        # Find the task description for context
        task_list = state.get("task_list", [])
        task_desc = next((t["description"] for t in task_list if t["id"] == task_id), "Unknown task")
        
        print(f"Critique: Dispatching review for {task_id}")
        prompt_tpl = db.get_system_prompt("CRITIQUE_PROMPT", CRITIQUE_PROMPT)
        instruction = prompt_tpl.format(task_description=task_desc, result=result[:2000])
        db.push_task(crit_task_id, thread_id, {"description": instruction})

    return {"critiques": {**critiques, **new_critiques}, "status": "dispatching"}

def reflection_node(state: AgentState, config: RunnableConfig) -> dict:
    """
    Code-Aware Reflection: Dispatches insight extraction with system context.
    """
    print("--- REFLECTION NODE ---")
    thread_id = config["configurable"]["thread_id"]
    completed_results = state.get("completed_results", {})
    ref_task_id = f"SYSTEM_REFLECTION_{thread_id}"
    
    if ref_task_id in completed_results:
        db.save_improvement(completed_results[ref_task_id])
        return {"status": "evolving"}

    # Gather system source code for context
    source_files = ["graph.py", "main.py", "db.py", "mock_worker.py", "static/app.js"]
    source_ctx = "\n\n## SYSTEM SOURCE CODE FOR REFERENCE\n"
    for fpath in source_files:
        try:
            with open(fpath, "r") as f:
                source_ctx += f"\n--- File: {fpath} ---\n{f.read()[:5000]}\n"
        except: pass

    # Only include user task results, not system task results
    user_results = {k: v[:500] for k, v in completed_results.items() if not k.startswith("SYSTEM_") and "_rep" not in k}
    
    # Load dynamic prompt
    prompt_tpl = db.get_system_prompt("REFLECTION_PROMPT", REFLECTION_PROMPT)
    instruction = prompt_tpl.format(results=json.dumps(user_results, indent=2)) + source_ctx
    
    # Avoid re-pushing if already waiting
    if state.get("status") == "awaiting_reflection":
        return {"status": "awaiting_reflection"}

    db.push_task(ref_task_id, thread_id, {"description": instruction})
    return {"status": "awaiting_reflection"}

def evolution_node(state: AgentState, config: RunnableConfig) -> dict:
    """
    Aggregate Evolution: Synthesizes system-wide improvements from history.
    """
    print("--- EVOLUTION NODE ---")
    thread_id = config["configurable"]["thread_id"]
    completed_results = state.get("completed_results", {})
    evo_task_id = f"SYSTEM_EVOLUTION_{thread_id}"
    
    if evo_task_id in completed_results:
        # Save the synthesized evolution as a top-level improvement
        db.save_improvement(f"ARCHITECTURAL UPGRADE: {completed_results[evo_task_id][:500]}...", patch_data=completed_results[evo_task_id])
        return {"status": "finished"}

    # Get aggregate context (traces + other suggestions)
    agg_ctx = db.get_evolution_context(limit=20)
    
    # Gather core system code
    core_files = ["graph.py", "main.py", "db.py"]
    core_ctx = ""
    for fpath in core_files:
        try:
            with open(fpath, "r") as f:
                core_ctx += f"\n--- File: {fpath} ---\n{f.read()[:8000]}\n"
        except: pass

    prompt_tpl = db.get_system_prompt("EVOLUTION_PROMPT", EVOLUTION_PROMPT)
    instruction = prompt_tpl.format(context=agg_ctx, source_code=core_ctx)
    
    # Avoid re-pushing if already waiting
    if state.get("status") == "awaiting_evolution":
        return {"status": "awaiting_evolution"}

    db.push_task(evo_task_id, thread_id, {"description": instruction})
    return {"status": "awaiting_evolution"}

def dispatcher_node(state: AgentState, config: RunnableConfig) -> dict:
    """
    Dispatcher: Pushes tasks with their k replicas to the worker queue.
    """
    print("--- DISPATCHER NODE ---")
    task_list = state.get("task_list", [])
    completed_results = state.get("completed_results", {})
    thread_id = config["configurable"]["thread_id"]
    k_global = state.get("k_factor", 1)
    dispatched_any = False
    
    for t in task_list:
        if t["status"] == "pending":
            if all(dep in completed_results for dep in t["dependencies"]):
                k_task = t.get("k_factor", k_global)
                print(f"Dispatcher: Dispatching k={k_task} for {t['id']}")
                t["status"] = "dispatched"
                dispatched_any = True
                
                # Build context from completed dependencies
                ctx = ""
                if t["dependencies"]:
                    ctx = "\n\n--- Context from previous tasks ---\n"
                    for d in t["dependencies"]:
                        ctx += f"Result of {d}:\n{completed_results.get(d, 'N/A')}\n\n"
                
                for i in range(1, k_task + 1):
                    rid = f"{t['id']}_rep{i}"
                    desc = t['description'] + ctx
                    if k_task > 1:
                        desc = f"[Replica {i}/{k_task}] {desc}"
                    payload = {"description": desc}
                    fps = validate_paths(state.get("file_paths", []))
                    if fps: payload["file_paths"] = fps
                    db.push_task(rid, thread_id, payload)
    
    if dispatched_any:
        return {"task_list": task_list, "status": "sleeping"}
    
    if all(t["status"] == "completed" for t in task_list):
        return {"status": "reflecting"}
        
    return {"status": "sleeping"}

def aggregator_node(state: AgentState, config: RunnableConfig) -> dict:
    """
    Aggregator: Collects replica results and synthesizes them.
    For k=1 tasks, directly promotes the single result.
    """
    print("--- AGGREGATOR NODE ---")
    thread_id = config["configurable"]["thread_id"]
    task_list = state.get("task_list", [])
    completed_results = state.get("completed_results", {})
    k_global = state.get("k_factor", 1)
    new_completed = {}
    
    for t in task_list:
        if t["status"] == "dispatched":
            parent_id = t["id"]
            k_task = t.get("k_factor", k_global)
            agg_task_id = f"SYSTEM_AGGREGATOR_{parent_id}"
            
            # Check if synthesis is already done
            if agg_task_id in completed_results:
                t["status"] = "completed"
                new_completed[parent_id] = completed_results[agg_task_id]
                print(f"Task {parent_id} finalized via synthesis.")
                continue

            # Gather replicas
            reps = []
            for i in range(1, k_task + 1):
                rid = f"{parent_id}_rep{i}"
                if rid in completed_results:
                    reps.append(completed_results[rid])
            
            if len(reps) >= k_task:
                if k_task == 1:
                    # Single replica — promote directly, no synthesis needed
                    t["status"] = "completed"
                    new_completed[parent_id] = reps[0]
                    print(f"Task {parent_id} completed (single replica).")
                else:
                    # Multiple replicas — dispatch synthesis
                    print(f"Aggregator: Dispatching synthesis for {parent_id} ({k_task} replicas)")
                    rep_text = "\n".join([f"--- Response {i+1} ---\n{r}\n" for i, r in enumerate(reps)])
                    prompt_tpl = db.get_system_prompt("SYNTHESIZER_PROMPT", SYNTHESIZER_PROMPT)
                    instruction = prompt_tpl.format(
                        k_count=k_task,
                        task_description=t['description'],
                        replicas=rep_text
                    )
                    db.push_task(agg_task_id, thread_id, {"description": instruction})

    return {"task_list": task_list, "completed_results": {**completed_results, **new_completed}, "status": "awaiting_aggregation"}

def verification_node(state: AgentState, config: RunnableConfig) -> dict:
    print("--- VERIFICATION NODE ---")
    thread_id = config["configurable"]["thread_id"]
    try:
        proc = subprocess.run([sys.executable, "vitals_check.py"], capture_output=True, text=True)
        if proc.returncode == 0:
            db.push_log("System", thread_id, "✅ System Vitals: HEALTHY")
        else:
            db.push_log("System", thread_id, f"⚠️ System Vitals: UNHEALTHY\n{proc.stdout}")
    except Exception as e:
        db.push_log("System", thread_id, f"⚠️ Verification Error: {e}")
    return {"status": "dispatching"}

def decide_next(state: AgentState) -> str:
    status = state.get("status")
    if status == "finished": return END
    if status in ["sleeping", "pending_approval", "awaiting_reflection", "awaiting_evolution", "awaiting_aggregation"]: return END
    if status == "critiquing": return "critique_node"
    if status == "dispatching": return "dispatcher_node"
    if status == "aggregating": return "aggregator_node"
    if status == "reflecting": return "reflection_node"
    if status == "evolving": return "evolution_node"
    if status == "verifying": return "verification_node"
    return END

builder = StateGraph(AgentState)
builder.add_node("planner_node", planner_node)
builder.add_node("dispatcher_node", dispatcher_node)
builder.add_node("aggregator_node", aggregator_node)
builder.add_node("critique_node", critique_node)
builder.add_node("reflection_node", reflection_node)
builder.add_node("evolution_node", evolution_node)
builder.add_node("verification_node", verification_node)

def route_start(state: AgentState) -> str:
    status = state.get("status", "planning")
    if status in ["aggregating", "awaiting_aggregation"]: return "aggregator_node"
    if status == "dispatching": return "dispatcher_node"
    if status == "critiquing": return "critique_node"
    if status == "verifying": return "verification_node"
    if status in ["reflecting", "awaiting_reflection"]: return "reflection_node"
    if status in ["evolving", "awaiting_evolution"]: return "evolution_node"
    return "planner_node"

builder.add_conditional_edges(START, route_start)
builder.add_edge("planner_node", "dispatcher_node")
builder.add_conditional_edges("dispatcher_node", decide_next)
builder.add_edge("aggregator_node", "verification_node")
builder.add_edge("verification_node", "dispatcher_node")
builder.add_conditional_edges("reflection_node", decide_next)
builder.add_conditional_edges("evolution_node", decide_next)

conn = sqlite3.connect("checkpoints.db", check_same_thread=False)
checkpointer = SqliteSaver(conn)
graph = builder.compile(checkpointer=checkpointer)
