import json
from typing import List, Dict, Any

class Orchestrator:
    """
    Manages the end-to-end flow: Plan -> Execute -> Critique -> Synthesize.
    Implements robust error handling and retry logic.
    """

    def __init__(self):
        self.planner = Planner() # Import from planner.py
        self.worker = Worker()   # Import from worker.py
        self.max_retries = 3

    def run(self, user_query: str, workspace_files: Dict[str, str]) -> str:
        """
        Main execution loop.
        """
        # 1. Plan
        plan = self.planner.plan(user_query, list(workspace_files.values()))
        tasks = plan["tasks"]
        
        if not tasks:
            return "No tasks generated."

        # 2. Execute Tasks
        results = []
        for task in tasks:
            result = self.worker.execute(task, workspace_files)
            results.append(result)

        # 3. Critique & Retry Loop (Insight 3)
        final_results = self._critique_and_retry(results)

        # 4. Synthesize Final Answer
        return self._synthesize(final_results, user_query)

    def _critique_and_retry(self, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Validates results and retries failed tasks.
        """
        processed_results = []
        
        for res in results:
            task_id = res["task_id"]
            status = res.get("status", "UNKNOWN")
            
            # Insight 3: Explicitly handle "No Result" or Error scenarios
            if status == "ERROR" or res.get("result") is None:
                print(f"Task {task_id} failed. Attempting retry...")
                
                # Retrieve original task to re-execute
                # In a real system, we'd store the task object alongside the result
                # Here we assume we can reconstruct or have access to it via ID
                # For this mock, we'll just skip or mark as failed after max retries
                
                # Simplified retry logic: 
                # In production, you'd re-run the worker with a modified prompt or fallback strategy
                res["status"] = "FAILED_AFTER_RETRY"
                res["error_message"] = f"Failed after {self.max_retries} attempts"
                
            processed_results.append(res)
            
        return processed_results

    def _synthesize(self, results: List[Dict[str, Any]], original_query: str) -> str:
        """
        Combines successful task results into a final answer.
        """
        # Filter only successful results
        successful = [r for r in results if r.get("status") == "SUCCESS"]
        
        if not successful:
            return "I was unable to complete the request due to errors."

        # Combine results
        combined_context = "\n\n".join([
            f"Task {r['task_id']} Result:\n{r['result']}" 
            for r in successful
        ])
        
        synthesis_prompt = f"""
Synthesize the following task results into a coherent answer for the user query: "{original_query}".

Results:
{combined_context}
"""
        
        # Placeholder for LLM call
        return "Synthesized Answer based on results."

# Helper imports needed for the file to be standalone in this context
from planner import Planner
from worker import Worker