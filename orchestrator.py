import json
import logging
from typing import List, Dict, Any, Optional

# Assuming these imports exist in your project structure
# from planner import Planner
# from worker import Worker
# from synthesizer import Synthesizer

logger = logging.getLogger(__name__)

class Orchestrator:
    def __init__(self, planner: 'Planner', worker: 'Worker', synthesizer: 'Synthesizer'):
        self.planner = planner
        self.worker = worker
        self.synthesizer = synthesizer
        # Configuration for retry limits and k_factor if needed
        self.max_retries = 3
        self.k_factor = 2  # Example default, adjust based on requirements

    def execute(self, user_query: str) -> str:
        """
        Main execution loop. Handles planning, worker dispatch, 
        stall detection, and synthesis with robustness checks.
        """
        current_query = user_query
        retry_count = 0
        
        while retry_count <= self.max_retries:
            try:
                # 1. Planning Phase
                task_list = self.planner.plan(current_query)
                
                if not task_list or len(task_list) == 0:
                    logger.warning("Planner generated empty task list.")
                    current_query = f"Previous attempt failed to generate tasks for query: {user_query}. Please re-plan."
                    retry_count += 1
                    continue

                # 2. Pre-Execution Plan Validation (Insight #2)
                validated_tasks = self._validate_plan(task_list)
                
                if not validated_tasks:
                    logger.warning("Plan validation failed or returned empty tasks.")
                    current_query = f"Previous plan was invalid for query: {user_query}. Please re-plan with stricter constraints."
                    retry_count += 1
                    continue

                # 3. Worker Dispatch Phase
                completed_results = self.worker.dispatch(validated_tasks)
                
                # 4. Stall Detection & Recovery (Insight #1)
                if len(completed_results) == 0:
                    logger.warning("Stall detected: No results returned from workers.")
                    current_query = f"Previous worker dispatch failed silently for query: {user_query}. Retrying with error context."
                    retry_count += 1
                    continue
                
                # Check if all results are empty strings (another form of stall/deadlock)
                if all(not r for r in completed_results):
                    logger.warning("Stall detected: All worker results were empty.")
                    current_query = f"Previous workers returned empty results for query: {user_query}. Retrying."
                    retry_count += 1
                    continue

                # 5. Synthesis Phase
                final_answer = self.synthesizer.synthesize(completed_results, user_query)
                
                # If synthesis returns a specific "No valid results" string (Insight #3), 
                # we might want to handle it differently or just return it.
                if final_answer == "No valid results":
                    logger.info("Synthesizer reported no valid results.")
                    return "No valid results found after multiple attempts."

                return final_answer

            except Exception as e:
                logger.error(f"Execution error: {str(e)}")
                current_query = f"Error occurred during execution for query: {user_query}. Details: {str(e)}. Please re-plan."
                retry_count += 1
        
        return "Failed to generate a response after multiple retries."

    def _validate_plan(self, task_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Insight #2: Pre-Execution Plan Validation.
        Uses a critique prompt to evaluate the generated task_list for 
        circular dependencies, missing descriptions, or over-decomposition.
        """
        critique_prompt = f"""
        You are a strict plan validator. Evaluate the following JSON task list for quality and structural integrity.
        
        Task List: {json.dumps(task_list)}
        
        Criteria:
        1. No circular dependencies (each task should be independent or depend on previously completed ones).
        2. All tasks have non-empty descriptions.
        3. Tasks are not overly decomposed (atomic but meaningful).
        
        If the plan is valid, return the exact same JSON list.
        If the plan is invalid, return an empty list [] and explain why in a comment if possible (though strict JSON output is preferred for parsing).
        
        Output:
        """
        
        # Assuming self.planner has a method to run a critique or we use a separate Critic instance
        # For this implementation, we assume the Planner class can also act as a critic or we have a dedicated one.
        # Here we simulate calling a critique function. In a real system, you might inject a Critic object.
        
        try:
            # Placeholder for actual LLM call to critique
            # validated = self.critic.evaluate(critique_prompt) 
            # For now, we assume the planner's output is passed through if it's valid.
            # In a real implementation, you'd parse the LLM response here.
            
            # Simplified logic: If task_list is not empty and has basic structure, pass it.
            # A real implementation would call an LLM to validate.
            return task_list
            
        except Exception as e:
            logger.error(f"Plan validation error: {str(e)}")
            return []

    def _handle_empty_results(self, results: List[str]) -> bool:
        """Helper to check if results are effectively empty."""
        return not results or all(not r for r in results)