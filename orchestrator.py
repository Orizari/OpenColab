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

    def execute(self, user_query: str) -> str:
        """
        Executes the full OCO pipeline: Plan -> Execute -> Synthesize.
        Includes stall detection and recovery mechanisms.
        """
        try:
            # Step 1: Planning with Pre-Execution Validation
            task_list = self._plan_with_validation(user_query)
            
            if not task_list:
                return "No valid tasks generated."

            # Step 2: Execution with Stall Detection
            completed_results = self._execute_tasks(task_list)
            
            # Insight 1: Stall Detection & Recovery
            if len(completed_results) == 0:
                logger.warning("Stall detected: No results returned from workers.")
                return self._handle_stall(user_query, task_list)

            # Step 3: Synthesis with Robustness Checks
            final_answer = self.synthesizer.synthesize(
                user_query=user_query,
                replicas=completed_results
            )
            
            return final_answer

        except Exception as e:
            logger.error(f"Orchestrator execution failed: {str(e)}")
            return f"Error during execution: {str(e)}"

    def _plan_with_validation(self, user_query: str) -> List[Dict[str, Any]]:
        """
        Generates tasks and validates them before dispatch.
        
        Insight 2: Pre-Execution Plan Validation
        Inserts a lightweight critique step to evaluate task_list for 
        circular dependencies, missing descriptions, or over-decomposition.
        """
        # Generate initial plan
        raw_plan = self.planner.plan(user_query)
        
        # Validate the plan using a critique prompt/logic
        validated_tasks = self._validate_plan(raw_plan)
        
        return validated_tasks

    def _validate_plan(self, raw_plan: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Validates the generated task list for structural integrity.
        """
        if not raw_plan:
            return []

        # Insight 2 Implementation: Critique Step
        # In a real implementation, this might call an LLM via a CRITIQUE_PROMPT
        # For now, we implement basic structural checks and filtering
        
        valid_tasks = []
        for task in raw_plan:
            # Check for missing descriptions
            if not task.get('description') or len(task['description'].strip()) == 0:
                logger.warning(f"Skipping task with missing description: {task}")
                continue
            
            # Check for over-decomposition (e.g., tasks too small to be useful)
            # This is a heuristic; could be enhanced with LLM critique
            if len(task.get('description', '')) < 5: 
                logger.warning(f"Skipping potentially over-decomposed task: {task}")
                continue
                
            valid_tasks.append(task)
            
        return valid_tasks

    def _execute_tasks(self, task_list: List[Dict[str, Any]]) -> List[Any]:
        """
        Dispatches tasks to workers and collects results.
        
        Insight 1 Implementation: Stall Detection
        Checks if the result list is empty after dispatch.
        """
        completed_results = []
        
        for task in task_list:
            try:
                result = self.worker.execute(task)
                # Filter out None or empty string results from workers
                if result is not None and str(result).strip() != "":
                    completed_results.append(result)
            except Exception as e:
                logger.error(f"Worker failed for task {task.get('id')}: {str(e)}")
                continue
                
        return completed_results

    def _handle_stall(self, user_query: str, failed_task_list: List[Dict[str, Any]]) -> str:
        """
        Handles the case where no results were returned.
        
        Insight 1 Implementation: Retry State & Re-planning
        Transitions to a retry state and triggers re-planning with explicit error context.
        """
        self.retry_count += 1
        
        if self.retry_count >= self.max_retries:
            logger.error("Max retries reached. Giving up.")
            return "Failed to generate results after multiple attempts."

        # Generate error context for re-planning
        error_context = {
            "previous_tasks": failed_task_list,
            "error_message": "All workers returned empty or invalid results.",
            "retry_attempt": self.retry_count
        }
        
        logger.info(f"Retrying with error context. Attempt {self.retry_count}")
        
        # Re-plan with explicit error context to break deadlock
        # This assumes the planner accepts an optional 'error_context' argument
        try:
            new_query = f"{user_query} [Previous attempt failed: {json.dumps(error_context)}]"
            new_task_list = self._plan_with_validation(new_query)
            
            if not new_task_list:
                return "Re-planning failed to generate valid tasks."
                
            # Retry execution with the new task list
            new_results = self._execute_tasks(new_task_list)
            
            if len(new_results) == 0:
                # If still empty, recurse or handle further
                return self._handle_stall(user_query, new_task_list)
            
            # Synthesize the new results
            final_answer = self.synthesizer.synthesize(
                user_query=user_query,
                replicas=new_results
            )
            return final_answer
            
        except Exception as e:
            logger.error(f"Re-planning failed: {str(e)}")
            return "Failed to recover from stall."

    def reset(self):
        """Reset orchestrator state."""
        self.retry_count = 0
EOF