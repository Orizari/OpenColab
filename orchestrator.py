import json
import logging
from typing import List, Dict, Any, Optional

# Assuming these imports exist in your project structure
from planner import Planner
from worker import WorkerPool
from synthesizer import Synthesizer

logger = logging.getLogger(__name__)

class Orchestrator:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.planner = Planner(config.get("planner_model", "gpt-4"))
        self.worker_pool = WorkerPool(
            num_workers=config.get("num_workers", 4),
            worker_model=config.get("worker_model", "gpt-3.5-turbo")
        )
        self.synthesizer = Synthesizer(config.get("synthesizer_model", "gpt-4"))
        
        # Configuration for retry logic and validation
        self.max_retries = config.get("max_retries", 3)
        self.retry_count = 0
        self.k_factor = config.get("k_factor", 3)  # Minimum valid replicas required

    def execute(self, query: str) -> str:
        """
        Executes the full OCO pipeline with improved stall detection and validation.
        """
        try:
            # Step 1: Generate Plan
            plan = self.planner.generate_plan(query)
            
            # Step 2: Validate Plan (Insight #2)
            validated_tasks = self._validate_plan(plan)
            if not validated_tasks:
                return "Error: Failed to generate a valid task plan."

            # Step 3: Dispatch and Collect Results
            completed_results = self.worker_pool.execute_tasks(validated_tasks)
            
            # Step 4: Stall Detection & Recovery (Insight #1)
            if len(completed_results) == 0:
                logger.warning("Stall detected: No results returned from workers.")
                return self._handle_stall(query, validated_tasks)

            # Step 5: Synthesize Final Answer (Insight #3)
            final_answer = self.synthesizer.synthesize(
                query=query,
                replicas=completed_results,
                k_factor=self.k_factor
            )
            
            return final_answer

        except Exception as e:
            logger.error(f"Orchestrator execution failed: {str(e)}")
            return f"System Error: An unexpected error occurred. Details: {str(e)}"

    def _validate_plan(self, plan: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Validates the generated plan for structural integrity and quality.
        
        Insight #2: Pre-Execution Plan Validation
        Uses a critique step to check for circular dependencies, missing descriptions, 
        or over-decomposition before dispatching.
        """
        task_list = plan.get("task_list", [])
        
        if not task_list:
            return []

        # Construct the critique prompt
        critique_prompt = f"""
        You are a strict quality assurance agent for a multi-agent planning system. 
        Your job is to validate the following task list for structural integrity and logical coherence.
        
        Input Task List:
        {json.dumps(task_list, indent=2)}

        Criteria for rejection:
        1. Circular Dependencies: Ensure no task depends on itself or creates a loop.
        2. Missing Descriptions: Every task must have a clear 'description' field.
        3. Over-decomposition: Tasks should not be trivially small (e.g., "fetch URL" if the next step is "parse HTML"). Combine atomic steps where appropriate.
        
        If the plan is valid, return exactly: {{"valid": true}}
        If invalid, return exactly: {{"valid": false, "reason": "<specific reason>"}}
        
        Output JSON only:
        """

        # Call LLM for critique (assuming a generic llm_call function exists)
        critique_response = self.planner.llm_call(critique_prompt)
        
        try:
            critique_result = json.loads(critique_response)
            if not critique_result.get("valid", False):
                logger.warning(f"Plan validation failed: {critique_result.get('reason')}")
                return [] # Return empty list to trigger stall/retry logic or error
            
            return task_list
        except json.JSONDecodeError:
            logger.error("Failed to parse plan critique response.")
            return []

    def _handle_stall(self, query: str, failed_tasks: List[Dict[str, Any]]) -> str:
        """
        Handles stalls by retrying with error context.
        
        Insight #1: Stall Detection & Recovery
        If no results are returned, transition to retry state and re-plan with explicit error context.
        """
        self.retry_count += 1
        
        if self.retry_count >= self.max_retries:
            logger.error("Max retries reached. Giving up.")
            return "Error: System stalled after multiple retries. Please check worker health."

        # Generate error context for re-planning
        error_context = f"Previous attempt failed with empty results. Tasks attempted: {json.dumps(failed_tasks)}. Ensure new tasks are robust and workers are healthy."
        
        logger.info(f"Retrying execution (Attempt {self.retry_count})...")
        
        # Re-plan with error context
        retry_plan = self.planner.generate_plan(query, extra_context=error_context)
        validated_retry_tasks = self._validate_plan(retry_plan)
        
        if not validated_retry_tasks:
            return "Error: Re-planning failed to generate valid tasks."

        # Retry execution
        retry_results = self.worker_pool.execute_tasks(validated_retry_tasks)
        
        if len(retry_results) == 0:
            # If still empty, recurse or fail based on policy. Here we let the main execute loop handle it 
            # by returning an empty string which might be caught again, but better to return explicit error 
            # if max retries are close.
            if self.retry_count >= self.max_retries - 1:
                return "Error: System stalled after multiple retries."
            return self._handle_stall(query, validated_retry_tasks)

        # Synthesize from retry results
        final_answer = self.synthesizer.synthesize(
            query=query,
            replicas=retry_results,
            k_factor=self.k_factor
        )
        return final_answer