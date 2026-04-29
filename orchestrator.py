"""
Orchestrator Module for Multi-Agent Synthesis System.

This module defines the core agents (Planner, Worker, Summarizer, Synthesizer)
and the orchestration logic to manage task decomposition, parallel execution,
summarization, and final synthesis.
"""

import asyncio
import json
import os
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field

# Assuming these are defined in separate modules or as part of the system
# For this implementation, we define minimal stubs for dependencies
from llm_client import LLMClient  # Hypothetical client wrapper
from models import Task, AgentResponse  # Hypothetical models

# --- Prompt Definitions ---

PLANNER_PROMPT = """\
You are a Planner. Your goal is to decompose the user's request into sub-tasks 
and determine the appropriate complexity factor (k_factor).

Instructions:
1. Analyze the user request for complexity and ambiguity.
2. Determine k_factor based on these rules:
   - Simple/Clear: 1-2
   - Moderate/Standard: 3-4
   - Complex/Nuanced: 5+
3. Output a JSON object with 'sub_tasks' (list of strings) and 'k_factor' (int).

User Request: {request}
"""

WORKER_PROMPT = """\
You are a Worker Agent. Your task is to solve the following sub-task using your 
specific expertise. Provide a detailed, high-quality response.

Sub-Task: {sub_task}
Expertise Context: {expertise}
"""

# Improvement Insight #2: Pre-synthesis summarization prompt
SUMMARIZER_PROMPT = """\
You are a Summarizer. Your goal is to condense the following raw output into 
a concise summary of maximum 150 words, preserving key insights and facts.

Raw Output:
{raw_output}

Summary:
"""

SYNTHESIZER_PROMPT = """\
You are a Synthesizer. Your goal is to combine multiple summarized responses 
into a single, cohesive final answer.

Context:
{replicas}

Final Answer:
"""


class Orchestrator:
    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client
        self.planner = Planner(llm_client)
        self.worker = Worker(llm_client)
        self.summarizer = Summarizer(llm_client)  # New Component
        self.synthesizer = Synthesizer(llm_client)

    async def execute(self, request: str) -> str:
        """
        Main execution loop.
        1. Plan task and determine k_factor.
        2. Spawn parallel workers.
        3. Summarize worker outputs (New Step).
        4. Synthesize final result.
        """
        # Step 1: Planning
        plan = await self.planner.plan(request)
        sub_tasks = plan['sub_tasks']
        k_factor = plan['k_factor']

        print(f"Planned {len(sub_tasks)} sub-tasks with k_factor={k_factor}")

        # Step 2: Parallel Execution
        # We spawn workers for each sub-task, potentially multiple times if k_factor > 1
        worker_tasks = []
        for i in range(len(sub_tasks)):
            for j in range(k_factor):
                task = asyncio.create_task(
                    self.worker.execute(sub_tasks[i], expertise=f"Expert {i+1}")
                )
                worker_tasks.append(task)

        # Gather all raw results
        raw_results = await asyncio.gather(*worker_tasks)

        # Step 3: Summarization (Improvement Insight #2)
        # Instead of passing raw verbose outputs, we summarize them first.
        print("Summarizing worker outputs...")
        summary_tasks = []
        for idx, result in enumerate(raw_results):
            summary_task = asyncio.create_task(
                self.summarizer.summarize(result.content)
            )
            summary_tasks.append(summary_task)

        summarized_replicas = await asyncio.gather(*summary_tasks)
        
        # Format replicas for synthesizer input
        replica_context = "\n\n".join([
            f"Replica {i+1}: {rep}" for i, rep in enumerate(summarized_replicas)
        ])

        # Step 4: Synthesis
        print("Synthesizing final answer...")
        final_answer = await self.synthesizer.synthesize(replica_context)

        return final_answer


class Planner:
    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client

    async def plan(self, request: str) -> Dict[str, Any]:
        prompt = PLANNER_PROMPT.format(request=request)
        response = await self.llm.generate(prompt, temperature=0.2)
        # Parse JSON from response (simplified for example)
        try:
            return json.loads(response.strip())
        except json.JSONDecodeError:
            return {"sub_tasks": [request], "k_factor": 1}


class Worker:
    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client

    async def execute(self, sub_task: str, expertise: str) -> AgentResponse:
        prompt = WORKER_PROMPT.format(sub_task=sub_task, expertise=expertise)
        content = await self.llm.generate(prompt, temperature=0.7)
        return AgentResponse(content=content)


class Summarizer:
    """
    Improvement Insight #2 Implementation:
    Summarizes verbose worker outputs to reduce context window usage 
    and improve signal-to-noise ratio for the Synthesizer.
    """
    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client

    async def summarize(self, raw_output: str) -> str:
        prompt = SUMMARIZER_PROMPT.format(raw_output=raw_output[:4000])  # Limit input to avoid truncation issues
        summary = await self.llm.generate(prompt, temperature=0.1)
        return summary.strip()


class Synthesizer:
    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client

    async def synthesize(self, replicas: str) -> str:
        prompt = SYNTHESIZER_PROMPT.format(replicas=replicas)
        return await self.llm.generate(prompt, temperature=0.2)