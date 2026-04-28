import json
import time
from typing import List, Dict, Any, Optional

# Assuming these imports exist in your project structure
# from llm_client import call_llm
# from prompts import PLANNER_PROMPT, CRITIQUE_PROMPT, SYNTHESIZER_PROMPT
# from worker import execute_task

class Orchestrator:
    def __init__(self, max_retries: int = 3):
        self.max_retries = max_retries
        self.retry_count = 0

    def run(self, initial_query: str) -> str:
        """
        Main execution loop for the OCO system.
        """
        while True:
            # 1. Planning Phase
            plan_response = call_llm(PLANNER_PROMPT.format(query=initial_query))
            try:
                task_list = json.loads(plan_response)
            except json.JSONDecodeError:
                task_list = []

            if not task_list:
                return "No tasks generated."

            # 2. Pre-Execution Plan Validation (Insight #2)
            critique_response = call_llm(CRITIQUE_PROMPT.format(task_list=json.dumps(task_list)))
            try:
                critique_result = json.loads(critique_response)
                if not critique_result.get("is_valid", False):
                    # Re-plan with error context to break deadlock/improve quality
                    initial_query += f"\nPrevious plan failed validation: {critique_result.get('reason', 'Unknown')}. Please regenerate."
                    continue 
            except json.JSONDecodeError:
                pass  # Proceed if critique fails, though ideally it shouldn't

            # 3. Execution Phase
            completed_results = []
            
            # Dispatch tasks to workers (simulated here)
            for task in task_list:
                result = execute_task(task)
                completed_results.append(result)

            # 4. Stall Detection & Recovery (Insight #1)
            if len(completed_results) == 0:
                self.retry_count += 1
                if self.retry_count >= self.max_retries:
                    return "System stalled: Max retries exceeded."
                
                # Transition to retry state: trigger re-planning with explicit error context
                initial_query += "\nPrevious execution failed silently or returned no results. Please re-plan considering potential worker failures."
                continue

            # 5. Synthesis Phase
            # Insight #3 handled within the synthesizer logic/prompt, 
            # but we pass the raw results here.
            synthesis_input = {
                "query": initial_query,
                "replicas": completed_results
            }
            
            final_response = call_llm(SYNTHESIZER_PROMPT.format(
                query=initial_query,
                replicas=json.dumps(completed_results)
            ))
            
            return final_response

    def reset(self):
        self.retry_count = 0
EOF