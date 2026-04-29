"""
OCO System Implementation - Updated with Error Propagation Handling
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
import json

# --- Constants and Prompts (Simplified for context) ---

PLANNER_PROMPT = """
You are a task planner. Decompose the user request into atomic tasks.
Each task must have:
- id: unique identifier
- description: clear instruction
- dependencies: list of task IDs required before this can run
- k_factor: number of replicas to generate (1 for factual, >1 for creative/analytical)

Current Request: {request}
"""

SYNTHESIZER_PROMPT = """
You are a synthesizer. Combine the results from {k_count} replicas of task {task_id}.
Replicas: {replicas}
Task Description: {description}
"""

# --- Data Models ---

@dataclass
class TaskSchema:
    """
    Represents an atomic task in the OCO system.
    Updated to support explicit failure handling and fallbacks.
    """
    id: str
    description: str
    dependencies: List[str] = field(default_factory=list)
    k_factor: int = 1
    
    # New fields for robustness
    status: str = "pending"  # pending, running, completed, failed
    error_message: Optional[str] = None
    fallback_value: Optional[str] = None  # Default value if task fails

@dataclass
class AgentState:
    """
    Manages the state of the OCO system execution.
    """
    tasks: Dict[str, TaskSchema] = field(default_factory=dict)
    results: Dict[str, Any] = field(default_factory=dict)
    
    def add_task(self, task: TaskSchema):
        self.tasks[task.id] = task

# --- Execution Logic ---

class OCOOrchestrator:
    """
    Orchestrates the execution of tasks with error propagation handling.
    """
    
    def __init__(self):
        self.state = AgentState()
        
    def plan(self, request: str) -> List[TaskSchema]:
        """
        Placeholder for LLM-based planning. 
        In a real system, this would parse the PLANNER_PROMPT output.
        """
        # Simulating a planned set of tasks
        task1 = TaskSchema(id="t1", description="Read file content", dependencies=[], k_factor=1)
        task2 = TaskSchema(id="t2", description="Analyze sentiment", dependencies=["t1"], k_factor=3)
        task3 = TaskSchema(id="t3", description="Format output", dependencies=["t2"], k_factor=1)
        
        self.state.add_task(task1)
        self.state.add_task(task2)
        self.state.add_task(task3)
        
        return [task1, task2, task3]

    def execute_task(self, task: TaskSchema) -> str:
        """
        Executes a single task. 
        Simulates potential failure and returns appropriate status/fallback.
        """
        try:
            # Simulate execution logic
            if task.id == "t1":
                # Simulate a silent failure (e.g., file not found)
                raise FileNotFoundError("File 'data.txt' not found")
            
            elif task.id == "t2":
                # Simulate successful analysis
                return "Positive sentiment detected with 85% confidence."
            
            elif task.id == "t3":
                # This task depends on t2, which succeeded
                return "**Summary:** Positive sentiment detected with 85% confidence."
            
            else:
                raise ValueError("Unknown task ID")
                
        except Exception as e:
            # Handle error explicitly
            task.status = "failed"
            task.error_message = str(e)
            
            # Return fallback value if available, otherwise empty string with error context
            if task.fallback_value:
                return task.fallback_value
            else:
                return f"[ERROR] Task {task.id} failed: {str(e)}"

    def execute(self, request: str) -> str:
        """
        Executes the entire plan, respecting dependencies and handling failures.
        """
        tasks = self.plan(request)
        
        # Topological sort or iterative execution based on dependencies
        executed_ids = set()
        
        while len(executed_ids) < len(tasks):
            for task in tasks:
                if task.id in executed_ids:
                    continue
                
                # Check if all dependencies are met and successful
                deps_met = True
                for dep_id in task.dependencies:
                    if dep_id not in self.state.results:
                        deps_met = False
                        break
                    
                    # Check if dependency failed
                    if self.state.tasks[dep_id].status == "failed":
                        deps_met = False
                        # Propagate failure to current task
                        task.status = "failed"
                        task.error_message = f"Dependency {dep_id} failed: {self.state.tasks[dep_id].error_message}"
                        break
                
                if not deps_met:
                    continue
                
                # Execute task
                result = self.execute_task(task)
                
                # Store result and status
                self.state.results[task.id] = result
                executed_ids.add(task.id)
        
        # Synthesize final answer
        return self.synthesize(tasks[-1])

    def synthesize(self, final_task: TaskSchema) -> str:
        """
        Synthesizes the final result.
        If the final task failed, it returns the error message or fallback.
        """
        if final_task.status == "failed":
            return f"[Synthesis Error] Final task failed: {final_task.error_message}"
        
        # In a real system, this would call LLM with SYNTHESIZER_PROMPT
        result = self.state.results.get(final_task.id, "")
        return result

# --- Main Execution ---

if __name__ == "__main__":
    orchestrator = OCOOrchestrator()
    
    # Add fallback to t1 to prevent total collapse
    orchestrator.state.tasks["t1"].fallback_value = "Default content: No file found."
    
    response = orchestrator.execute("Analyze the latest report.")
    print(f"Final Response:\n{response}")