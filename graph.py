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
    Step 1: Uses a ReAct agent with Web Search to research the request.
    Step 2: Parses the research output into a structured DAG of tasks.
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
    
    # --- Step 1: Research Agent ---
    print("Running Research Phase...")
    tools = [web_search_tool]
    prompt_template = """You are an Orchestrator Planner. Before generating a plan, research the user's request.
Your objective is to find any necessary context (like package names, endpoints, or facts) needed to build a plan.

You have access to the following tools:

{tools}

Use the following format:
Thought: Do I need to use a tool? Yes
Action: the action to take, should be one of [{tool_names}]
Action Input: the input to the action
Observation: the result of the action

When you have found enough context, or if you don't need a tool, output your final findings:
Thought: Do I need to use a tool? No
Final Answer: [Detailed context and proposed approach to solve the user's request]

User Request: {input}
Thought:{agent_scratchpad}"""

    prompt = PromptTemplate.from_template(prompt_template)
    agent = create_react_agent(llm, tools, prompt)
    agent_executor = AgentExecutor(agent=agent, tools=tools, verbose=True, handle_parsing_errors=True, max_iterations=3)
    
    try:
        research_response = agent_executor.invoke({"input": req})
        research_context = research_response.get("output", "No context found.")
    except Exception as e:
        print(f"Research failed: {e}")
        research_context = "Research failed. Proceeding with original request."

    print(f"Research Context:\n{research_context}")
    
    # --- Step 2: Structured Parsing ---
    print("Running Structured Parsing Phase...")
    structured_llm = llm.with_structured_output(TaskListSchema)
    
    parse_prompt = f"""You are an advanced AI Orchestrator Planner.
Your job is to break down the user prompt into a logical sequence of sub-tasks, GIVEN the research context.
The tasks must form a Directed Acyclic Graph (DAG) using dependencies. 
If a task requires the output of another task, list the other task's ID in its 'dependencies'.

User Prompt: {req}
Research Context: {research_context}

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
        "status": "dispatching"
    }

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
                
                # Push to queue
                db.push_task(t["id"], thread_id, {
                    "description": t["description"] + dependency_context
                })
                # Update status
                t["status"] = "dispatched"
                dispatched_any = True
        new_task_list.append(t)
                
    if not dispatched_any and all(t["status"] == "completed" for t in task_list):
        return {"status": "finished"}

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
    - If status == "sleeping", graph pauses (returns END but checkpointer saves state).
      Actually, the correct way to pause in LangGraph is to simply reach END, or wait for human-in-the-loop.
      Since we want external webhook to resume, going to END is fine if we use `thread_id` to resume.
      Wait, if we go to '__end__', LangGraph finishes. To suspend, LangGraph has `interrupt()` or we can just pause.
      Let's use `__end__`. A webhook call will just resume the graph from `aggregator_node` using `StateGraph` and updating state.
    """
    status = state.get("status")
    if status == "finished":
        return END
    elif status == "sleeping":
        return END
    elif status == "dispatching":
        return "dispatcher_node"
    elif status == "aggregating":
        return "aggregator_node"
    return END

# Build Graph
builder = StateGraph(AgentState)

builder.add_node("planner_node", planner_node)
builder.add_node("dispatcher_node", dispatcher_node)
builder.add_node("aggregator_node", aggregator_node)

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


# Checkpointer
conn = sqlite3.connect("checkpoints.db", check_same_thread=False)
checkpointer = SqliteSaver(conn)

graph = builder.compile(checkpointer=checkpointer)
