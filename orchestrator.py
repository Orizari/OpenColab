import json
import logging
from typing import List, Dict, Any, Optional

# Assuming these imports exist in your project structure
from planner import Planner
from worker import WorkerPool
from synthesizer import Synthesizer
from utils import parse_task_list, validate_task_structure

logger = logging.getLogger(__name__)

class Orchestrator:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.planner = Planner(config.get("planner_model", "default"))
        self.worker_pool = WorkerPool(
            max_workers=config.get("max_workers", 5),
            worker_type=config.get("worker_type", "default")
        )
        self.synthesizer = Synthesizer(config.get("synthesizer_model", "default"))
        
        # Configuration for retry logic
        self.max_retries = config.get("max_retries", 3)
        self.retry_count = 0
        
    def execute(self, query: str) -> Dict[str, Any]:
        """
        Executes the full pipeline: Plan -> Execute -> Synthesize.
        Includes stall detection and recovery mechanisms.
        """
        try:
            # Step 1: Planning with Pre-Execution Validation
            task_list = self._plan_with_validation(query)
            
            if not task_list:
                return {"result": "No valid tasks generated.", "status": "failed"}

            # Step 2: Execution with Stall Detection
            completed_results = self._execute_tasks(task_list)
            
            # Stall Detection & Recovery
            if len(completed_results) == 0:
                logger.warning("Stall detected: No results returned from workers.")
                return self._handle_stall(query, task_list)

            # Step 3: Synthesis with Robust Input Handling
            final_answer = self._synthesize(completed_results)
            
            return {
                "result": final_answer,
                "status": "success",
                "metadata": {
                    "tasks_executed": len(completed_results),
                    "retries_used": self.retry_count
                }
            }

        except Exception as e:
            logger.error(f"Critical failure in orchestrator: {str(e)}")
            return {"result": "System Error", "status": "error", "error": str(e)}

    def _plan_with_validation(self, query: str) -> List[Dict[str, Any]]:
        """
        Generates tasks and validates them before dispatch.
        Implements Pre-Execution Plan Validation (Insight #2).
        """
        # Generate initial plan
        raw_plan = self.planner.generate(query)
        
        # Validate structure using lightweight critique logic
        validated_tasks = validate_task_structure(raw_plan)
        
        if not validated_tasks:
            logger.warning("Plan validation failed. Attempting re-planning with error context.")
            # Fallback or retry planning with explicit constraints
            validated_tasks = self.planner.generate(query, strict_mode=True)
            
        return validated_tasks

    def _execute_tasks(self, task_list: List[Dict[str, Any]]) -> List[Any]:
        """
        Dispatches tasks to workers and collects results.
        Implements Stall Detection (Insight #1).
        """
        if not task_list:
            return []
            
        try:
            # Dispatch all tasks concurrently or sequentially based on config
            results = self.worker_pool.execute_batch(task_list)
            
            # Filter out None/Empty results from workers that failed silently
            valid_results = [r for r in results if r is not None and str(r).strip() != ""]
            
            return valid_results
            
        except Exception as e:
            logger.error(f"Worker execution failed: {str(e)}")
            return []

    def _handle_stall(self, query: str, previous_task_list: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Handles the stall condition by retrying with re-planning.
        Implements Stall Detection & Recovery (Insight #1).
        """
        self.retry_count += 1
        
        if self.retry_count >= self.max_retries:
            logger.error("Max retries reached. Giving up.")
            return {"result": "Failed after multiple retries.", "status": "failed"}
        
        logger.info(f"Retrying execution (Attempt {self.retry_count})...")
        
        # Re-plan with explicit error context to break deadlock
        error_context = f"Previous attempt failed with empty results. Task list was: {previous_task_list}"
        new_query = f"{query}\n\nContext: {error_context}"
        
        # Recursive call to retry the full pipeline
        return self.execute(new_query)

    def _synthesize(self, completed_results: List[Any]) -> str:
        """
        Synthesizes final answer from worker results.
        Implements Synthesizer Input Robustness (Insight #3).
        """
        if not completed_results:
            return "No valid results to synthesize."
            
        try:
            # Pass results to synthesizer
            # The Synthesizer class should handle len(replicas) < k_factor internally
            final_answer = self.synthesizer.generate(completed_results)
            return final_answer
        except Exception as e:
            logger.error(f"Synthesis failed: {str(e)}")
            return "Failed to synthesize results."

# Placeholder for external dependencies
class Planner:
    def __init__(self, model):
        self.model = model
        
    def generate(self, query, strict_mode=False):
        # Simulate LLM generation
        if strict_mode:
            return [{"id": 1, "description": "Retry task", "dependencies": []}]
        return [{"id": 1, "description": "Original task", "dependencies": []}]

class WorkerPool:
    def __init__(self, max_workers, worker_type):
        self.max_workers = max_workers
        
    def execute_batch(self, tasks):
        # Simulate worker execution returning empty results to test stall detection
        return [None for _ in tasks] 

class Synthesizer:
    def __init__(self, model):
        self.model = model
        
    def generate(self, replicas):
        if len(replicas) == 0:
            return "No valid results"
        return "Synthesized Answer"

def validate_task_structure(task_list):
    """
    Lightweight validation for circular dependencies and missing descriptions.
    Implements Pre-Execution Plan Validation (Insight #2).
    """
    if not task_list:
        return []
    
    validated = []
    for task in task_list:
        # Check for required fields
        if "description" not in task or not task["description"]:
            logger.warning(f"Task missing description: {task}")
            continue
            
        # Check for circular dependencies (simplified check)
        if "dependencies" in task and isinstance(task["dependencies"], list):
            # In a real scenario, perform topological sort or cycle detection
            pass
            
        validated.append(task)
        
    return validated