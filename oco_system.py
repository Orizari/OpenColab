import json
import os
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
import asyncio

# Mocking LLM calls for demonstration purposes
async def call_llm(system_prompt: str, user_prompt: str) -> str:
    """
    Simulates an LLM API call. In a real implementation, this would call OpenAI/Anthropic/etc.
    """
    # For demo, just return a placeholder based on input length to simulate processing
    return f"LLM Response for prompt length {len(user_prompt)}: [Summary/Synthesis Result]"

@dataclass
class TaskConfig:
    k_factor: int = 3
    max_tokens: int = 4096
    temperature: float = 0.7

@dataclass
class ReplicaResult:
    replica_id: int
    result: str
    tokens_used: int

class OCOSystem:
    def __init__(self, config: Optional[TaskConfig] = None):
        self.config = config or TaskConfig()
        
        # Define Prompts
        self.PLANNER_PROMPT = """
        You are a planner. Analyze the following task and determine the complexity.
        Return a JSON object with 'k_factor' (number of replicas needed, 1-5) and 'subtasks'.
        Task: {task}
        """
        
        # Improvement Insight #2: The SYNTHESIZER_PROMPT is now designed to receive pre-summarized inputs
        self.SYNTHESIZER_PROMPT = """
        You are a synthesizer. Review the following summarized results from multiple parallel workers.
        Synthesize a final, high-quality answer based on these summaries.
        
        Summarized Results:
        {replicas}
        
        Final Answer:
        """
        
        # New Prompt for Pre-Synthesis Summarization (Insight #2 Implementation)
        self.SUMMARIZER_PROMPT = """
        You are a summarizer. Condense the following worker result into a concise summary 
        of at most 100 words, preserving key facts and reasoning steps. Discard fluff.
        
        Worker Result:
        {result}
        """

    async def plan_task(self, task: str) -> Dict[str, Any]:
        """Step 1: Planner determines k_factor and subtasks."""
        # Insight #3 Improvement could be added here (dynamic classifier), 
        # but we focus on Insight #2 for this patch.
        prompt = self.PLANNER_PROMPT.format(task=task)
        response = await call_llm("Planner", prompt)
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            return {"k_factor": self.config.k_factor, "subtasks": [task]}

    async def execute_replicas(self, subtasks: List[str], k_factor: int) -> List[ReplicaResult]:
        """Step 2: Execute parallel workers."""
        async def run_single_replica(replica_id: int, subtask: str) -> ReplicaResult:
            # Simulate worker execution
            result = await call_llm(f"Worker {replica_id}", subtask)
            return ReplicaResult(
                replica_id=replica_id,
                result=result,
                tokens_used=len(result)
            )

        # Create tasks for all replicas
        tasks = []
        for i in range(k_factor):
            # Distribute subtasks or repeat if needed
            task_to_run = subtasks[i % len(subtasks)] if subtasks else subtasks[0]
            tasks.append(run_single_replica(i, task_to_run))
        
        results = await asyncio.gather(*tasks)
        return list(results)

    async def summarize_replicas(self, replicas: List[ReplicaResult]) -> List[str]:
        """
        Improvement Insight #2 Implementation:
        Pre-synthesis step to summarize verbose worker outputs before passing to the synthesizer.
        This prevents context window exhaustion and improves signal-to-noise ratio.
        """
        async def summarize_single(replica: ReplicaResult) -> str:
            prompt = self.SUMMARIZER_PROMPT.format(result=replica.result)
            summary = await call_llm("Summarizer", prompt)
            return summary

        summaries = await asyncio.gather(*[summarize_single(r) for r in replicas])
        return summaries

    async def synthesize(self, summaries: List[str]) -> str:
        """Step 3: Synthesizer combines summarized results."""
        # Format summaries into the prompt
        formatted_replicas = "\n---\n".join([f"Replica {i+1}: {s}" for i, s in enumerate(summaries)])
        prompt = self.SYNTHESIZER_PROMPT.format(replicas=formatted_replicas)
        
        final_answer = await call_llm("Synthesizer", prompt)
        return final_answer

    async def run(self, task: str) -> Dict[str, Any]:
        """Main execution flow."""
        # 1. Plan
        plan = await self.plan_task(task)
        k_factor = plan.get('k_factor', self.config.k_factor)
        subtasks = plan.get('subtasks', [task])

        # 2. Execute Replicas (Parallel)
        replicas = await self.execute_replicas(subtasks, k_factor)

        # 3. Pre-Synthesis Summarization (New Step for Insight #2)
        summaries = await self.summarize_replicas(replicas)

        # 4. Synthesize Final Answer
        final_answer = await self.synthesize(summaries)

        return {
            "task": task,
            "plan": plan,
            "final_answer": final_answer,
            "replica_count": len(replicas)
        }

# Example Usage
async def main():
    system = OCOSystem()
    result = await system.run("Write a comprehensive guide on quantum computing for beginners.")
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    asyncio.run(main())