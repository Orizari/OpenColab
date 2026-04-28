import json
import logging
from typing import List, Dict, Any, Optional

# Assuming these imports exist in your project structure
# from planner import Planner
# from worker import Worker
# from synthesizer import Synthesizer

logger = logging.getLogger(__name__)

class Orchestrator:
    def __init__(self, planner, worker, synthesizer):
        self.planner = planner
        self.worker = worker
        self.synthesizer = synthesizer
        # Configuration for retry logic
        self.max_retries = 3
        self.retry_count = 0

    def execute(self, query: str) -> str:
        """
        Executes the full pipeline: Plan -> Dispatch -> Synthesize.
        Includes stall detection and recovery mechanisms.
        """
        try:
            # Step 1: Planning with Pre-Execution Validation
            task_list = self._plan_and_validate(query)
            
            if not task_list:
                return "No valid tasks generated."

            # Step 2: Dispatching to Workers
            completed_results = self._dispatch_tasks(task_list)
            
            # Stall Detection & Recovery
            if len(completed_results) == 0:
                logger.warning("Stall detected: No results returned from workers.")
                return self._handle_stall(query, task_list)

            # Step 3: Synthesis with Robust Input Handling
            final_answer = self.synthesizer.synthesize(
                query=query,
                replicas=completed_results
            )
            
            return final_answer

        except Exception as e:
            logger.error(f"Orchestrator execution failed: {str(e)}")
            return f"Error during execution: {str(e)}"

    def _plan_and_validate(self, query: str) -> List[Dict[str, Any]]:
        """
        Generates tasks and validates them using a Critique Prompt.
        Returns an empty list if validation fails or no valid tasks are found.
        """
        # Generate initial plan
        raw_plan = self.planner.plan(query)
        
        # Parse the plan (assuming it returns JSON or a structured string)
        try:
            task_list = json.loads(raw_plan) if isinstance(raw_plan, str) else raw_plan
        except json.JSONDecodeError:
            logger.error("Failed to parse planner output as JSON.")
            return []

        # Pre-Execution Plan Validation via Critique Prompt
        validated_tasks = self._validate_task_list(task_list, query)
        
        if not validated_tasks:
            logger.warning("Task list failed validation.")
            
        return validated_tasks

    def _validate_task_list(self, task_list: List[Dict[str, Any]], original_query: str) -> List[Dict[str, Any]]:
        """
        Uses a lightweight critique prompt to check for circular dependencies, 
        missing descriptions, or over-decomposition.
        """
        critique_prompt = f"""
        You are an expert task validator. Review the following generated task list against the original query.
        
        Original Query: "{original_query}"
        Generated Tasks: {json.dumps(task_list)}

        Check for:
        1. Circular dependencies (Task A depends on B, B depends on A).
        2. Missing descriptions or IDs.
        3. Over-decomposition (tasks that are too trivial to warrant separate execution).
        
        If the task list is valid, return the exact same JSON list.
        If invalid, return an empty list [] and explain why in a comment if possible.
        
        Output only the JSON list.
        """
        
        try:
            critique_response = self.planner.plan(critique_prompt) # Reusing planner for critique logic
            validated_list = json.loads(critique_response) if isinstance(critique_response, str) else critique_response
            
            # Ensure it's a list
            if not isinstance(validated_list, list):
                return []
                
            return validated_list
        except Exception as e:
            logger.error(f"Validation failed: {str(e)}")
            return []

    def _dispatch_tasks(self, task_list: List[Dict[str, Any]]) -> List[Any]:
        """
        Dispatches tasks to workers and collects results.
        Filters out empty or failed results if necessary, but primarily 
        returns what the workers provide.
        """
        completed_results = []
        
        for task in task_list:
            try:
                result = self.worker.execute(task)
                completed_results.append(result)
            except Exception as e:
                logger.error(f"Worker failed for task {task.get('id', 'unknown')}: {str(e)}")
                # Depending on strategy, you might append None or skip. 
                # Here we append the error/None to maintain index alignment if needed, 
                # but stall detection checks len(completed_results) == 0.
                completed_results.append(None)
                
        return completed_results

    def _handle_stall(self, query: str, failed_task_list: List[Dict[str, Any]]) -> str:
        """
        Handles the case where no results were returned (stall detection).
        Transitions to retry state and triggers re-planning with error context.
        """
        self.retry_count += 1
        
        if self.retry_count >= self.max_retries:
            logger.critical("Max retries reached for stall condition.")
            return "System stalled after multiple retries. Please check worker health."

        # Construct error context for re-planning
        error_context = f"Previous attempt failed with empty results. Tasks attempted: {json.dumps(failed_task_list)}. Retry count: {self.retry_count}."
        
        logger.info(f"Retrying with error context. Attempt {self.retry_count}")
        
        # Re-plan with explicit error context to break deadlock
        # We call plan again, potentially modifying the planner's behavior or prompt 
        # based on the error context.
        retry_query = f"{query}\n\nIMPORTANT CONTEXT: The previous execution stalled with no results. Please re-plan considering potential worker failures or dependency issues."
        
        # Reset retry count for this specific cycle if we want to limit per-query retries, 
        # or keep it global depending on architecture. Here we assume per-query logic 
        # might need a different approach, but for simplicity, we just recurse/re-run plan.
        
        # Re-execute the pipeline from planning
        return self.execute(retry_query)

    def reset(self):
        """Reset retry counters."""
        self.retry_count = 0
EOF