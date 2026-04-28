import json
import asyncio
from typing import List, Dict, Any, Optional

# Assuming these imports exist based on the context of "Synthesizer", "Worker", etc.
# In a real scenario, these would be imported from their respective modules.
from llm_client import LLMClient
from models import Task, WorkerResult, SynthesisRequest

class Orchestrator:
    def __init__(self, llm_client: LLMClient):
        self.llm_client = llm_client
        # Configuration for k_factor heuristics
        self.DEFAULT_K_FACTOR = 2
        
    async def execute_task(self, task: Task) -> Dict[str, Any]:
        """
        Executes a task by decomposing it into sub-tasks, running workers in parallel,
        and synthesizing the results.
        """
        # 1. Plan/Decompose (Simplified for this patch focus)
        # In a full implementation, this would involve dynamic k_factor assignment
        # based on a pre-flight classifier. For now, we use static heuristics.
        k_factor = self._determine_k_factor(task)
        
        # 2. Parallel Execution of Workers
        workers = [Worker(self.llm_client) for _ in range(k_factor)]
        tasks = [worker.run(task) for worker in workers]
        
        # Wait for all workers to complete
        results: List[WorkerResult] = await asyncio.gather(*tasks)
        
        # 3. Pre-Synthesis Summarization (Improvement #2)
        summarized_replicas = self._summarize_results(results)
        
        # 4. Synthesis
        synthesis_request = SynthesisRequest(
            original_task=task,
            replicas=summarized_replicas
        )
        final_result = await self.llm_client.synthesize(synthesis_request)
        
        return final_result

    def _determine_k_factor(self, task: Task) -> int:
        """
        Static heuristic for k_factor. 
        Improvement #3 suggests replacing this with a dynamic classifier.
        """
        if task.type == "creative":
            return 3
        elif task.type == "analytical":
            return 2
        else:
            return self.DEFAULT_K_FACTOR

    def _summarize_results(self, results: List[WorkerResult]) -> List[str]:
        """
        Summarizes each worker's output to a fixed length before passing to the synthesizer.
        This addresses Improvement #2: Context Window Exhaustion in Synthesizer.
        
        Args:
            results: List of raw outputs from parallel workers.
            
        Returns:
            List of summarized strings.
        """
        summaries = []
        for i, result in enumerate(results):
            # In a real implementation, this would call an LLM to summarize
            # or use a truncation strategy if the output is too long.
            # Here we simulate a summary by taking the first 500 chars 
            # and adding a note if truncated.
            raw_content = result.content
            
            if len(raw_content) > 500:
                summary = raw_content[:500] + "... [Truncated]"
            else:
                summary = raw_content
                
            summaries.append(summary)
            
        return summaries

class Worker:
    def __init__(self, llm_client: LLMClient):
        self.llm_client = llm_client
        
    async def run(self, task: Task) -> WorkerResult:
        """
        Executes a specific sub-task or perspective.
        
        Improvement #1 (Partial Synthesis/Streaming) is not directly implemented 
        here because the Orchestrator waits for all workers via asyncio.gather.
        To implement partial synthesis, the Orchestrator would need to use 
        an async queue or generator pattern instead of gather().
        """
        response = await self.llm_client.generate(task.prompt)
        return WorkerResult(
            worker_id=id(self),
            content=response
        )