from typing import TypedDict, Annotated, Literal, Optional, List
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field
from langchain_ollama import ChatOllama
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite import SqliteSaver
import sqlite3
import json
import uuid
import os

from langchain_community.tools import DuckDuckGoSearchRun
from langchain.tools import tool
from langchain.agents import AgentExecutor, create_react_agent
from langchain.prompts import PromptTemplate

import db

search = DuckDuckGoSearchRun()

@tool
def web_search_tool(query: str) -> str:
    """Use this tool to search the internet for information."""
    try:
        return search.run(query)
    except Exception as e:
        return f"Error searching: {e}"

# Ensure queue DB is initialized
db.init_db()

# Define our State
class AgentState(TypedDict):
    original_request: str
    file_paths: List[str]
    task_list: list[dict]
    completed_results: dict[str, str]
    status: str
    error: Optional[str]

class TaskSchema(BaseModel):
    id: str = Field(description="Unique identifier for the task (e.g., 'task_1', 'fetch_data')")
    description: str = Field(description="Detailed instruction of what needs to be done in this task")
    dependencies: List[str] = Field(description="List of task IDs that must be completed BEFORE this task can start. Empty list if none.")

class TaskListSchema(BaseModel):
    tasks: List[TaskSchema] = Field(description="A list of tasks forming a directed acyclic graph")

def planner_node(state: AgentState) -> dict:
    """
    Real LLM Node using LangChain and Ollama.
    Directly parses the user request into a structured DAG of tasks.
    Execution and research is delegated to the worker nodes.
    """
    print("--- PLANNER NODE ---")
    
    # Check if task_list is already populated
    if state.get("task_list"):
        return {"status": "dispatching"}

    req = state.get("original_request", "Unknown request")
    
    print(f"Asking Ollama to plan tasks for: {req}")
    
    # Initialize Ollama model
    llm = ChatOllama(
        base_url=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
        model="qwen3.5:9b",
        temperature=0.1
    )
    
    # Retrieve recent insights to improve planning
    recent_insights = db.get_recent_insights(5)
    insights_context = ""
    if recent_insights:
        insights_context = "\n\n--- Lessons Learned from Previous Tasks ---\n"
        for ins in recent_insights:
            insights_context += f"- [{ins['topic']}]: {ins['insight']}\n"

    parse_prompt = f"""You are an advanced AI Orchestrator Planner.
Your job is to break down the user prompt into a logical sequence of sub-tasks.
The tasks must form a Directed Acyclic Graph (DAG) using dependencies. 
If a task requires the output of another task, list the other task's ID in its 'dependencies'.

CRITICAL INSTRUCTION: You do not have access to tools. If the user asks to analyze code or search the web, CREATE A TASK for the worker to do that (e.g. 'Read the README file', 'Search the web for X'). Do not output final answers here, only output the task plan.
{insights_context}
User Prompt: {req}

Ensure you output the required JSON structure strictly.
"""
    
    try:
        result = structured_llm.invoke(parse_prompt)
        
        # Convert Pydantic objects to dicts for our state machine
        tasks = []
        for t in result.tasks:
            tasks.append({
                "id": t.id,
                "description": t.description,
                "dependencies": t.dependencies,
                "status": "pending"
            })
            
    except Exception as e:
        print(f"Error calling structured LLM: {e}")
        with open("/tmp/llm_error.log", "w") as f:
            f.write(str(e))
        # Fallback for parsing errors / connection issues
        tasks = [
            {
                "id": "fallback_task",
                "description": f"Fallback manual execution for: {req} (LLM Failed)",
                "dependencies": [],
                "status": "pending"
            }
        ]
    
    return {
        "task_list": tasks,
        "completed_results": {},
        "status": "pending_approval"
    }

def reflection_node(state: AgentState) -> dict:
    """
    Analyzes the completed results and extracts 'lessons learned' to save as insights.
    This enables the system to improve its intelligence over time.
    """
    print("--- REFLECTION NODE ---")
    completed_results = state.get("completed_results", {})
    original_request = state.get("original_request", "")
    
    if not completed_results:
        return {"status": "finished"}

    llm = ChatOllama(
        base_url=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
        model="qwen3.5:9b",
        temperature=0.1
    )
    
    reflection_prompt = f"""You are a meta-cognitive AI analyst. 
Review the original request and the completed results of a workflow.
Identify one key 'lesson learned' or 'best practice' that could help improve future task planning or execution.
Format your response as a JSON object with 'topic' and 'insight' fields.

Original Request: {original_request}
Results: {json.dumps(completed_results, indent=2)}
"""
    try:
        # We use a simple structure for insights
        class InsightSchema(BaseModel):
            topic: str = Field(description="A short category for the insight")
            insight: str = Field(description="A concise description of the lesson learned")

        structured_llm = llm.with_structured_output(InsightSchema)
        reflection_result = structured_llm.invoke(reflection_prompt)
        
        db.save_insight(reflection_result.topic, reflection_result.insight)
        print(f"Reflection saved insight: {reflection_result.topic}")
        
    except Exception as e:
        print(f"Error during reflection: {e}")

    return {"status": "finished"}

def dispatcher_node(state: AgentState, config: RunnableConfig) -> dict:
    """
    Scans the task_list for any task where dependencies are met, 
    and dispatch it to the queue constraint.
    """
    print("--- DISPATCHER NODE ---")
    task_list = state.get("task_list", [])
    completed_results = state.get("completed_results", {})
    
    thread_id = config["configurable"]["thread_id"]
    new_task_list = []
    dispatched_any = False
    
    # Find all ready tasks
    for t in task_list:
        if t["status"] == "pending":
            # Check dependencies
            deps_met = all(dep in completed_results for dep in t["dependencies"])
            if deps_met:
                print(f"Dispatching task {t['id']}")
                
                # Gather outputs from dependencies to give context to this task
                dependency_context = ""
                if t["dependencies"]:
                    dependency_context = "\\n\\n--- Context from Previous Tasks ---\\n"
                    for dep in t["dependencies"]:
                        dependency_context += f"Result of {dep}:\\n{completed_results[dep]}\\n\\n"
                
                payload_data = {
                    "description": t["description"] + dependency_context
                }
                
                # Forward all attached files to workers
                file_paths = state.get("file_paths", [])
                if file_paths:
                    payload_data["file_paths"] = file_paths

                # Push to queue
                db.push_task(t["id"], thread_id, payload_data)
                # Update status
                t["status"] = "dispatched"
                dispatched_any = True
        new_task_list.append(t)
                
    if not dispatched_any and all(t["status"] == "completed" for t in task_list):
        return {"status": "reflecting"}

    return {
        "task_list": new_task_list,
        "status": "sleeping"
    }

def aggregator_node(state: AgentState) -> dict:
    """
    Called when a webhook wakes up the graph. It receives results 
    (mocked as being injected before this node runs) and updates statuses.
    In LangGraph, we can pass updates to the state from the outside. 
    So the webhook actually writes to `completed_results` and `task_list`.
    This node simply confirms and moves back to dispatching.
    """
    print("--- AGGREGATOR NODE ---")
    
    task_list = state.get("task_list", [])
    completed_results = state.get("completed_results", {})
    
    # We ensure that if a result is in completed_results, 
    # the task in task_list is marked 'completed'
    new_task_list = []
    
    for t in task_list:
        if t["status"] == "dispatched" and t["id"] in completed_results:
            print(f"Aggregator confirming completed task: {t['id']}")
            t["status"] = "completed"
            
        new_task_list.append(t)
        
    return {
        "task_list": new_task_list,
        "status": "dispatching"
    }

def decide_next(state: AgentState) -> str:
    """
    Edge logic: 
    - If status == "sleeping" or "pending_approval", graph pauses (returns END).
    """
    status = state.get("status")
    if status == "finished":
        return END
    elif status == "sleeping":
        return END
    elif status == "pending_approval":
        return END
    elif status == "dispatching":
        return "dispatcher_node"
    elif status == "aggregating":
        return "aggregator_node"
    elif status == "reflecting":
        return "reflection_node"
    return END

# Build Graph
builder = StateGraph(AgentState)

builder.add_node("planner_node", planner_node)
builder.add_node("dispatcher_node", dispatcher_node)
builder.add_node("aggregator_node", aggregator_node)
builder.add_node("reflection_node", reflection_node)

def route_start(state: AgentState) -> str:
    status = state.get("status", "planning")
    if status == "aggregating":
        return "aggregator_node"
    elif status == "dispatching":
        return "dispatcher_node"
    return "planner_node"

builder.add_conditional_edges(START, route_start)
builder.add_edge("planner_node", "dispatcher_node")
builder.add_conditional_edges("dispatcher_node", decide_next)
builder.add_edge("aggregator_node", "dispatcher_node")
builder.add_edge("reflection_node", END)


# Checkpointer
conn = sqlite3.connect("checkpoints.db", check_same_thread=False)
checkpointer = SqliteSaver(conn)

graph = builder.compile(checkpointer=checkpointer)
