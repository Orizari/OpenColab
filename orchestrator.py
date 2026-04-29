import json
import time
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed
import re

# Assuming these imports exist in the broader system context
# from llm_client import LLMClient
# from prompts import SYNTHESIZER_PROMPT, PLANNER_PROMPT, WORKER_PROMPT_TEMPLATE

@dataclass
class TaskResult:
    task_id: str
    result: Any
    metadata: Dict[str, Any] = field(default_factory=dict)

class Orchestrator:
    def __init__(self, llm_client):
        self.llm_client = lllm_client
        # Placeholder for the pre-flight classifier
        self.complexity_classifier = ComplexityClassifier(llm_client)

    def execute(self, user_query: str) -> Dict[str, Any]:
        """
        Executes a task by planning, decomposing into parallel workers, 
        and synthesizing results.
        """
        start_time = time.time()
        
        # 1. Pre-flight Complexity Analysis (Improvement #3)
        complexity_score = self.complexity_classifier.analyze(user_query)
        
        # 2. Planning & Decomposition
        plan = self._plan_task(user_query, complexity_score)
        tasks = plan['tasks']
        
        # 3. Parallel Execution with Partial Synthesis Support (Improvement #1)
        results = self._execute_parallel_with_streaming(tasks, complexity_score)
        
        # 4. Synthesis
        final_result = self._synthesize(results, user_query)
        
        end_time = time.time()
        return {
            "result": final_result,
            "metadata": {
                "execution_time": end_time - start_time,
                "complexity_score": complexity_score,
                "tasks_executed": len(tasks)
            }
        }

    def _plan_task(self, query: str, complexity_score: float) -> Dict[str, Any]:
        """
        Uses planner to decompose task. 
        Complexity score influences k_factor dynamically.
        """
        prompt = PLANNER_PROMPT.format(
            query=query,
            complexity_score=complexity_score
        )
        response = self.llm_client.generate(prompt)
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            # Fallback to default k_factor if parsing fails
            return {"tasks": [{"id": "1", "instruction": query}], "k_factor": 2}

    def _execute_parallel_with_streaming(self, tasks: List[Dict], complexity_score: float) -> List[TaskResult]:
        """
        Executes worker replicas. 
        Implements partial synthesis logic if k_factor is high and latency is critical.
        """
        # Determine k_factor based on dynamic complexity (Improvement #3 implementation detail)
        k_factor = self._calculate_dynamic_k_factor(complexity_score)
        
        results = []
        
        # For Improvement #1: If k_factor is large, we might want to start synthesizing 
        # as soon as N/2 results arrive. However, for simplicity in this single-file 
        # representation, we will collect all first but note the bottleneck.
        # In a real async implementation, we would use an event loop or queue here.
        
        with ThreadPoolExecutor(max_workers=k_factor) as executor:
            futures = {}
            for task in tasks:
                future = executor.submit(self._execute_worker, task['instruction'], k_factor)
                futures[future] = task
            
            for future in as_completed(futures):
                try:
                    result_data = future.result()
                    results.append(result_data)
                    
                    # Improvement #1: Check if we can start partial synthesis
                    # This is a simplified check. In production, this would trigger 
                    # the synthesizer asynchronously.
                    if len(results) >= k_factor // 2 + 1 and not hasattr(self, '_partial_synthesis_started'):
                        self._partial_synthesis_started = True
                        # In a real system, we'd pass partial results to synthesizer here
                        
                except Exception as e:
                    results.append(TaskResult(
                        task_id=futures[future]['id'],
                        result=None,
                        metadata={"error": str(e)}
                    ))
        
        return results

    def _calculate_dynamic_k_factor(self, complexity_score: float) -> int:
        """
        Improvement #3: Replaces static heuristics with dynamic calculation.
        Higher complexity -> higher k_factor for better coverage.
        """
        if complexity_score < 0.3:
            return 2
        elif complexity_score < 0.7:
            return 3
        else:
            return 5

    def _execute_worker(self, instruction: str, k_factor: int) -> TaskResult:
        """
        Executes a single worker replica.
        """
        prompt = WORKER_PROMPT_TEMPLATE.format(
            instruction=instruction,
            k_factor=k_factor
        )
        response = self.llm_client.generate(prompt)
        return TaskResult(
            task_id="worker_1", # Simplified ID
            result=response,
            metadata={"k_factor": k_factor}
        )

    def _synthesize(self, results: List[TaskResult], original_query: str) -> str:
        """
        Synthesizes final answer from worker results.
        
        Improvement #2: Pre-synthesis summarization is applied here 
        to manage context window usage.
        """
        if not results:
            return "No results generated."

        # Improvement #2: Summarize each replica before passing to synthesizer
        summarized_replicas = []
        for res in results:
            if res.result:
                summary = self._summarize_result(res.result)
                summarized_replicas.append(summary)
            else:
                summarized_replicas.append("Worker failed to produce output.")

        # Construct the prompt with summarized data
        replicas_json = json.dumps(summarized_replicas, indent=2)
        
        prompt = SYNTHESIZER_PROMPT.format(
            original_query=original_query,
            replicas=replicas_json
        )
        
        final_answer = self.llm_client.generate(prompt)
        return final_answer

    def _summarize_result(self, raw_result: str) -> str:
        """
        Improvement #2: Summarizes verbose worker output to fixed length.
        """
        # Simple truncation for demonstration; in production, use an LLM call
        if len(raw_result) > 500:
            return raw_result[:500] + "... [truncated]"
        return raw_result

class ComplexityClassifier:
    def __init__(self, llm_client):
        self.llm_client = llm_client

    def analyze(self, query: str) -> float:
        """
        Improvement #3: Estimates task complexity.
        Returns a score between 0 and 1.
        """
        prompt = f"""
        Analyze the complexity of the following user query. 
        Return a single float between 0.0 (simple) and 1.0 (complex).
        
        Query: {query}
        
        Score:
        """
        try:
            response = self.llm_client.generate(prompt)
            # Extract float from response
            match = re.search(r'[\d\.]+', response)
            if match:
                return float(match.group())
            return 0.5 # Default
        except Exception:
            return 0.5

# Placeholder for LLM Client
class LLMClient:
    def generate(self, prompt: str) -> str:
        # Simulate LLM call
        return f"Response to: {prompt[:50]}..."