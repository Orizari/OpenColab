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
            # 1. Planning Phase with Pre-Execution Validation
            task_list = self.planner.plan(query)
            
            # Insight 2: Pre-Execution Plan Validation
            validated_task_list = self._validate_plan(task_list, query)
            
            if not validated_task_list:
                logger.warning("Plan validation failed after retries. Returning default response.")
                return "Error: Could not generate a valid plan."

            # 2. Dispatch Phase with Stall Detection
            completed_results = []
            
            # Insight 1: Stall Detection & Recovery
            for attempt in range(self.max_retries):
                completed_results = self.worker.dispatch(validated_task_list)
                
                # Check for empty results (stall condition)
                if len(completed_results) == 0:
                    logger.warning(f"Stall detected: Empty results after dispatch. Attempt {attempt + 1}/{self.max_retries}")
                    
                    if attempt < self.max_retries - 1:
                        # Trigger re-planning with error context
                        error_context = "Workers returned empty results. Please regenerate tasks."
                        validated_task_list = self._validate_plan(
                            self.planner.plan(query, error_context=error_context), 
                            query
                        )
                        if not validated_task_list:
                            logger.error("Re-planning failed to produce valid tasks.")
                            break
                    else:
                        logger.error("Max retries reached for stall detection.")
                        return "Error: System stalled during task execution."
                else:
                    # Success, break out of retry loop
                    break
            
            # 3. Synthesis Phase with Robustness Check
            if not completed_results:
                return "No results available to synthesize."

            # Insight 3: Synthesizer Input Robustness
            final_answer = self.synthesizer.synthesize(completed_results, query)
            
            return final_answer

        except Exception as e:
            logger.error(f"Orchestrator execution failed: {str(e)}")
            return f"System Error: {str(e)}"

    def _validate_plan(self, task_list: List[Dict[str, Any]], original_query: str) -> List[Dict[str, Any]]:
        """
        Insight 2: Lightweight critique of the generated plan.
        Returns validated task list or None if invalid after internal checks.
        """
        if not task_list:
            return []

        # Basic structural validation before sending to workers
        valid_tasks = []
        for task in task_list:
            # Check for missing descriptions
            if not task.get('description') or len(task['description'].strip()) == 0:
                logger.warning("Skipping task with missing description")
                continue
            
            # Check for over-decomposition (e.g., too short/atomic tasks that might be noise)
            # This is a placeholder for more complex logic depending on your definition of 'over-decomposed'
            if len(task['description']) < 5: 
                logger.warning("Skipping potentially over-decomposed task")
                continue
                
            valid_tasks.append(task)

        return valid_tasks if valid_tasks else []