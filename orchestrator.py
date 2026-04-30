import json
import time
from typing import List, Dict, Any, Optional

# Assuming these are imported from your project's modules
# from llm_client import LLMClient
# from worker_pool import WorkerPool
# from prompts import PLANNER_PROMPT, CRITIQUE_PROMPT, SYNTHESIZER_PROMPT

class Orchestrator:
    def __init__(self, llm_client, worker_pool):
        self.llm_client = llm_client
        self.worker_pool = worker_pool
        self.state = "idle"
        
    def run(self, initial_query: str) -> str:
        """
        Main execution loop for the OCO system.
        """
        self.state = "planning"
        
        # 1. Planning Phase
        plan_response = self.llm_client.generate(PLANNER_PROMPT.format(query=initial_query))
        try:
            task_list = json.loads(plan_response)
        except json.JSONDecodeError:
            return "Error: Failed to parse planner output."

        # 2. Pre-Execution Plan Validation (Insight #2)
        critique_response = self.llm_client.generate(CRITIQUE_PROMPT.format(task_list=json.dumps(task_list)))
        try:
            critique_result = json.loads(critique_response)
            if not critique_result.get("is_valid", False):
                # Re-plan with error context
                error_context = critique_result.get("feedback", "Invalid plan structure")
                self.state = "re-planning"
                plan_response = self.llm_client.generate(PLANNER_PROMPT.format(query=initial_query, feedback=error_context))
                try:
                    task_list = json.loads(plan_response)
                except json.JSONDecodeError:
                    return "Error: Failed to parse re-planner output."
        except json.JSONDecodeError:
            # If critique fails, proceed with caution or fail safe
            pass

        self.state = "executing"
        
        # 3. Execution Phase
        completed_results = []
        for task in task_list:
            result = self.worker_pool.execute(task)
            completed_results.append(result)
            
        # 4. Stall Detection & Recovery (Insight #1)
        if len(completed_results) == 0 or all(r is None for r in completed_results):
            self.state = "retry"
            error_context = "All workers returned empty results or failed silently."
            # Trigger re-planning with explicit error context to break deadlock
            plan_response = self.llm_client.generate(PLANNER_PROMPT.format(query=initial_query, feedback=error_context))
            try:
                task_list = json.loads(plan_response)
                # Re-execute the new plan
                completed_results = []
                for task in task_list:
                    result = self.worker_pool.execute(task)
                    completed_results.append(result)
            except json.JSONDecodeError:
                return "Error: Failed to parse re-planner output after stall detection."

        # 5. Synthesis Phase
        self.state = "synthesizing"
        
        # Filter out None/empty results if necessary, but pass what we have
        valid_replicas = [r for r in completed_results if r and isinstance(r, str) and len(r.strip()) > 0]
        
        synthesis_response = self.llm_client.generate(
            SYNTHESIZER_PROMPT.format(
                query=initial_query, 
                replicas=json.dumps(valid_replicas),
                k_factor=len(task_list) # Example k_factor logic
            )
        )
        
        return synthesis_response

    def get_state(self):
        return self.state