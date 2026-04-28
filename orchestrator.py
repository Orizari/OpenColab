import json
import time
from typing import List, Dict, Any, Optional

# Assuming these imports exist in your project structure
# from llm_client import call_llm
# from prompts import PLANNER_PROMPT, CRITIQUE_PROMPT, SYNTHESIZER_PROMPT

class Orchestrator:
    def __init__(self, planner_model="gpt-4", synthesizer_model="gpt-4"):
        self.planner_model = planner_model
        self.synthesizer_model = synthesizer_model
        # Configuration for retry logic
        self.max_retries = 3
        self.retry_delay = 1.0

    def run(self, query: str) -> str:
        """
        Main execution loop for the OCO system.
        """
        # Step 1: Planning with Validation (Insight #2)
        task_list = self._plan_and_validate(query)
        
        if not task_list:
            return "No valid tasks generated."

        # Step 2: Dispatch and Collect Results
        completed_results = self._dispatch_tasks(task_list)
        
        # Step 3: Stall Detection & Recovery (Insight #1)
        if len(completed_results) == 0:
            print("Warning: Empty results detected. Initiating stall recovery...")
            return self._handle_stall(query, task_list)

        # Step 4: Synthesis with Robustness (Insight #3)
        final_answer = self._synthesize(query, completed_results)
        return final_answer

    def _plan_and_validate(self, query: str) -> List[Dict[str, Any]]:
        """
        Generates tasks and validates them for structural integrity.
        Implements Insight #2: Pre-Execution Plan Validation.
        """
        # Initial Planning
        plan_json = self._call_llm(PLANNER_PROMPT.format(query=query))
        
        try:
            task_list = json.loads(plan_json)
        except json.JSONDecodeError:
            print("Error: Planner returned invalid JSON.")
            return []

        # Validation Step
        valid_task_list = self._validate_plan(task_list)
        
        if not valid_task_list:
            print("Warning: Generated plan failed validation. Re-planning with error context...")
            # Fallback or re-plan logic could go here, returning empty for now to trigger stall/retry in main loop
            return []
            
        return valid_task_list

    def _validate_plan(self, task_list: List[Dict[str, Any]]) -> Optional[List[Dict[str, Any]]]:
        """
        Uses a Critique Prompt to ensure atomicity, no circular deps, and completeness.
        """
        critique_input = {
            "task_list": json.dumps(task_list),
            "criteria": [
                "No circular dependencies",
                "Each task has a clear description",
                "Tasks are atomic and independent where possible"
            ]
        }
        
        critique_response = self._call_llm(CRITIQUE_PROMPT.format(
            task_list=json.dumps(task_list),
            criteria="\n".join(critique_input["criteria"])
        ))
        
        # Simple heuristic: if the LLM returns "VALID", we proceed. 
        # In a robust system, you might parse a JSON response from the critic.
        if "INVALID" in critique_response.upper() or "ERROR" in critique_response.upper():
            return None
        
        return task_list

    def _dispatch_tasks(self, task_list: List[Dict[str, Any]]) -> List[Any]:
        """
        Dispatches tasks to workers and collects results.
        """
        results = []
        for task in task_list:
            # Simulate worker execution
            try:
                result = self._execute_task(task)
                results.append(result)
            except Exception as e:
                print(f"Task {task.get('id', 'unknown')} failed: {e}")
                # Depending on strategy, you might append None or skip
        return results

    def _handle_stall(self, query: str, original_plan: List[Dict[str, Any]]) -> str:
        """
        Implements Insight #1: Stall Detection & Recovery.
        If no results are returned, it attempts to re-plan with error context.
        """
        for attempt in range(1, self.max_retries + 1):
            print(f"Retry attempt {attempt}/{self.max_retries}...")
            time.sleep(self.retry_delay)
            
            # Re-plan with explicit error context about the stall
            recovery_query = f"{query}. Previous plan failed to produce results. Generate a new, simpler plan."
            new_task_list = self._plan_and_validate(recovery_query)
            
            if new_task_list:
                completed_results = self._dispatch_tasks(new_task_list)
                if len(completed_results) > 0:
                    return self._synthesize(query, completed_results)
        
        return "System stalled: Could not recover from empty results after multiple retries."

    def _synthesize(self, query: str, replicas: List[Any]) -> str:
        """
        Synthesizes final answer from worker results.
        Implements Insight #3: Synthesizer Input Robustness.
        """
        # Check for insufficient or empty replicas
        if len(replicas) == 0:
            return "No valid results to synthesize."
        
        # Filter out empty strings/None values if necessary, or pass as is depending on prompt design
        # Here we assume the prompt handles empty strings gracefully, but we check count first.
        
        synthesis_input = {
            "query": query,
            "replicas": json.dumps(replicas)
        }
        
        response = self._call_llm(SYNTHESIZER_PROMPT.format(
            query=synthesis_input["query"],
            replicas=synthesis_input["replicas"]
        ))
        
        return response

    def _execute_task(self, task: Dict[str, Any]) -> Any:
        """
        Simulates worker execution. In real code, this calls the specific worker function.
        """
        # Placeholder for actual worker logic
        return f"Result for task {task.get('id')}"

    def _call_llm(self, prompt_text: str) -> str:
        """
        Wrapper for LLM API call.
        """
        # print(f"LLM Call: {prompt_text[:50]}...")
        return "Mock LLM Response"

# Placeholder Prompts for context
PLANNER_PROMPT = """You are a planner. Break down the following query into atomic tasks:
Query: {query}"""

CRITIQUE_PROMPT = """Critique the following task list for structural integrity:
Task List: {task_list}
Criteria:
{criteria}
Return 'VALID' if it passes, or 'INVALID' with reasons if not."""

SYNTHESIZER_PROMPT = """Synthesize the final answer based on the query and worker results.
Query: {query}
Replicas: {replicas}
If replicas are empty or insufficient, state that no valid results were found."""