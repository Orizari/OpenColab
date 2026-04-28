import json
import time
import random
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from enum import Enum

# Mocking external dependencies for demonstration purposes
class LLMClient:
    def __init__(self):
        self.model = "gpt-4"
    
    def generate(self, prompt: str) -> str:
        # Simulate LLM response with slight randomness to simulate variance
        return f"Response from {self.model} for prompt: {prompt[:20]}..."

class CodeExecutor:
    @staticmethod
    def execute(code: str) -> Dict[str, Any]:
        # Simulate code execution result
        return {"status": "success", "output": "Executed successfully"}

@dataclass
class TaskNode:
    id: str
    description: str
    dependencies: List[str] = field(default_factory=list)
    k_factor: int = 1  # Number of replicas
    status: str = "pending"
    result: Optional[Any] = None
    confidence_score: float = 0.0

@dataclass
class TaskGraph:
    nodes: Dict[str, TaskNode] = field(default_factory=dict)
    edges: List[tuple] = field(default_factory=list)

    def add_node(self, node: TaskNode):
        self.nodes[node.id] = node

    def get_ready_nodes(self) -> List[TaskNode]:
        return [n for n in self.nodes.values() if n.status == "pending" and all(
            self.nodes[dep].status == "completed" for dep in n.dependencies
        )]

class OCOSystem:
    def __init__(self):
        self.llm = LLMClient()
        self.executor = CodeExecutor()
        self.planner_prompt = """
        You are an expert task planner. Decompose the following user request into atomic tasks.
        
        User Request: {user_request}
        
        Output format: JSON list of objects with keys: 'id', 'description', 'dependencies' (list of ids), 'k_factor'.
        Ensure tasks are atomic and dependencies are valid.
        """

        # Improvement 2: Critique-and-Replan Loop for Planner
        self.planner_critique_prompt = """
        You are a task graph critic. Review the following planned tasks for atomicity, completeness, and logical dependency correctness.
        
        Original Request: {user_request}
        Planned Tasks: {planned_tasks_json}
        
        If the plan is flawed, provide a corrected JSON list of tasks. If it is correct, return the original list.
        """

        # Improvement 1: External Verification for Auditor
        self.auditor_prompt = """
        You are an OCO Quality Auditor. Evaluate the following task result based on the original request and any provided context.
        
        Original Request: {user_request}
        Task Result: {task_result}
        Context (if applicable): {context}
        
        Output format: JSON with 'score' (0-1) and 'feedback'.
        """

    def plan_tasks(self, user_request: str) -> TaskGraph:
        # Step 1: Initial Planning
        prompt = self.planner_prompt.format(user_request=user_request)
        raw_response = self.llm.generate(prompt)
        
        try:
            planned_tasks = json.loads(raw_response)
        except json.JSONDecodeError:
            return TaskGraph()

        # Construct initial graph
        graph = TaskGraph()
        for task in planned_tasks:
            node = TaskNode(
                id=task['id'],
                description=task['description'],
                dependencies=task.get('dependencies', []),
                k_factor=task.get('k_factor', 1)
            )
            graph.add_node(node)

        # Improvement 2: Critique and Replan Loop
        max_iterations = 3
        for i in range(max_iterations):
            critique_prompt = self.planner_critique_prompt.format(
                user_request=user_request,
                planned_tasks_json=json.dumps(planned_tasks)
            )
            critique_response = self.llm.generate(critique_prompt)
            
            try:
                refined_tasks = json.loads(critique_response)
                # If the plan didn't change significantly, break loop
                if refined_tasks == planned_tasks:
                    break
                
                # Update graph with refined tasks
                new_graph = TaskGraph()
                for task in refined_tasks:
                    node = TaskNode(
                        id=task['id'],
                        description=task['description'],
                        dependencies=task.get('dependencies', []),
                        k_factor=task.get('k_factor', 1)
                    )
                    new_graph.add_node(node)
                
                graph = new_graph
                planned_tasks = refined_tasks
                
            except json.JSONDecodeError:
                # If critique fails to parse, keep current plan
                break

        return graph

    def execute_task(self, node: TaskNode) -> Dict[str, Any]:
        # Improvement 3: Dynamic Resource Allocation based on initial confidence/complexity
        # In a real scenario, this would fetch real-time metrics. Here we simulate variance.
        
        # Simulate getting initial responses from replicas
        initial_responses = []
        for _ in range(node.k_factor):
            response = self.llm.generate(node.description)
            initial_responses.append(response)
        
        # Calculate variance among initial responses to determine need for more resources or verification
        # Simple heuristic: if responses are identical, low variance. If different, high variance.
        unique_responses = len(set(initial_responses))
        variance_score = 1.0 - (unique_responses / node.k_factor) if node.k_factor > 0 else 0
        
        # Improvement 3: Dynamic Adjustment
        # If variance is high (responses differ), increase replicas for robustness
        effective_k = node.k_factor
        if variance_score > 0.5:
            effective_k = min(node.k_factor * 2, 10) # Cap at 10
        
        final_responses = []
        for _ in range(effective_k):
            response = self.llu.generate(node.description)
            final_responses.append(response)
        
        # Aggregate result (simple majority vote or last one for demo)
        aggregated_result = final_responses[-1] if final_responses else "No result"

        return {
            "result": aggregated_result,
            "variance_score": variance_score,
            "effective_k": effective_k
        }

    def audit_task(self, node: TaskNode, user_request: str) -> float:
        # Improvement 1: External Verification
        # Check if task involves code execution
        if "code" in node.description.lower() or "execute" in node.description.lower():
            exec_result = self.executor.execute(node.result)
            if exec_result["status"] != "success":
                return 0.0
        
        prompt = self.auditor_prompt.format(
            user_request=user_request,
            task_result=node.result,
            context="N/A" # Could be retrieved documents here
        )
        
        audit_response = self.llm.generate(prompt)
        try:
            audit_json = json.loads(audit_response)
            return audit_json.get('score', 0.0)
        except json.JSONDecodeError:
            return 0.0

    def run(self, user_request: str) -> Any:
        graph = self.plan_tasks(user_request)
        
        # Topological sort execution
        while any(n.status == "pending" for n in graph.nodes.values()):
            ready_nodes = graph.get_ready_nodes()
            
            if not ready_nodes:
                raise ValueError("Circular dependency detected or invalid graph")
            
            for node in ready_nodes:
                # Execute
                exec_result = self.execute_task(node)
                node.result = exec_result["result"]
                
                # Audit
                confidence = self.audit_task(node, user_request)
                node.confidence_score = confidence
                
                if confidence < 0.5:
                    # Improvement 1: Retry or flag for human review
                    print(f"Low confidence on task {node.id}. Retrying...")
                    node.result = self.execute_task(node)["result"]
                    confidence = self.audit_task(node, user_request)
                    node.confidence_score = confidence
                
                node.status = "completed"

        # Return the result of the final node (simplified for demo)
        final_nodes = [n for n in graph.nodes.values() if not any(
            n.id == dep for _, dep in graph.edges
        )]
        
        if final_nodes:
            return final_nodes[-1].result
        return None

# Example Usage
if __name__ == "__main__":
    oco = OCOSystem()
    request = "Write a python function to calculate fibonacci and execute it."
    result = oco.run(request)
    print(f"Final Result: {result}")