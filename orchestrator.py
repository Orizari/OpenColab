import json
import time
from typing import List, Dict, Any, Optional

# Assuming these imports exist in your project structure
# from llm_client import call_llm
# from prompts import PLANNER_PROMPT, CRITIQUE_PROMPT, SYNTHESIZER_PROMPT, WORKER_PROMPT

class Orchestrator:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.max_retries = config.get('max_retries', 3)
        self.k_factor = config.get('k_factor', 3) # Number of replicas for synthesis

    def run(self, query: str) -> str:
        """
        Main execution loop for the OCO system.
        """
        current_query = query
        retry_count = 0
        
        while retry_count < self.max_retries:
            # 1. Planning Phase
            plan_json = call_llm(PLANNER_PROMPT.format(query=current_query))
            try:
                plan_data = json.loads(plan_json)
                task_list = plan_data.get('task_list', [])
            except json.JSONDecodeError:
                return "Error: Failed to parse planner output."

            if not task_list:
                retry_count += 1
                continue

            # 2. Pre-Execution Plan Validation (Insight #2)
            critique_result = call_llm(CRITIQUE_PROMPT.format(task_list=json.dumps(task_list)))
            try:
                critique_data = json.loads(critique_result)
                if not critique_data.get('is_valid', False):
                    # Re-plan with error context
                    current_query += f"\nPrevious plan failed validation: {critique_data.get('reason', 'Unknown reason')}"
                    retry_count += 1
                    continue
                # Update task list if critique suggests modifications (optional, depending on implementation)
                task_list = critique_data.get('refined_task_list', task_list)
            except json.JSONDecodeError:
                pass # Proceed with original plan if critique fails

            # 3. Worker Execution Phase
            completed_results = []
            for task in task_list:
                result = call_llm(WORKER_PROMPT.format(task=task['description']))
                completed_results.append({
                    'task': task,
                    'result': result
                })

            # 4. Stall Detection & Recovery (Insight #1)
            if len(completed_results) == 0:
                current_query += "\nSystem stalled: No results returned from workers."
                retry_count += 1
                continue
            
            # Check for empty results from all workers
            non_empty_results = [r for r in completed_results if r['result'].strip()]
            if len(non_empty_results) == 0:
                current_query += "\nSystem stalled: All workers returned empty results."
                retry_count += 1
                continue

            # 5. Synthesis Phase (Insight #3)
            replicas = [r['result'] for r in completed_results]
            
            # Check if we have enough valid replicas
            valid_replicas = [r for r in replicas if r.strip()]
            
            if len(valid_replicas) < self.k_factor:
                return "No valid results"

            final_answer = call_llm(SYNTHESIZER_PROMPT.format(
                query=current_query,
                replicas=json.dumps(valid_replicas[:self.k_factor])
            ))
            
            return final_answer

        return "Error: Max retries exceeded."

# Placeholder for LLM client and prompts to make the file syntactically complete
def call_llm(prompt: str) -> str:
    # Implementation depends on actual LLM provider
    return "{}" 

PLANNER_PROMPT = "You are a planner. Break down this query into tasks: {query}"
CRITIQUE_PROMPT = "Critique this task list for circular dependencies or missing info: {task_list}"
WORKER_PROMPT = "Execute this task: {task}"
SYNTHESIZER_PROMPT = "Synthesize these results: {replicas} for query: {query}"