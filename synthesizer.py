import json
import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

class Synthesizer:
    def __init__(self, model_name: str):
        self.model_name = model_name

    def synthesize(self, query: str, replicas: List[str], k_factor: int) -> str:
        """
        Synthesizes final answer from worker replicas.
        
        Insight #3: Synthesizer Input Robustness
        Explicitly handles cases where len(replicas) < k_factor or all replicas are empty.
        Instructs the model to return "No valid results" rather than hallucinating.
        """
        
        # Check for insufficient or empty replicas
        if not replicas or len(replicas) < k_factor:
            logger.warning(f"Insufficient valid replicas: {len(replicas)} provided, required {k_factor}.")
            return "No valid results"

        # Filter out empty strings from replicas to ensure quality input
        valid_replicas = [r for r in replicas if r and len(r.strip()) > 0]
        
        if len(valid_replicas) < k_factor:
            logger.warning(f"Insufficient non-empty replicas: {len(valid_replicas)} provided, required {k_factor}.")
            return "No valid results"

        # Construct the synthesizer prompt
        synthesizer_prompt = f"""
        You are an expert synthesizer agent. Your task is to generate a comprehensive answer 
        based on the following query and multiple independent worker replicas.
        
        Query: {query}
        
        Worker Replicas (must have at least {k_factor} valid entries):
        {json.dumps(valid_replicas, indent=2)}

        Instructions:
        1. Analyze all replicas for consistency and accuracy.
        2. Synthesize a single, coherent final answer.
        3. If the replicas are contradictory, prioritize the most detailed and logically sound information.
        4. Do NOT hallucinate information not present in the replicas.
        
        Output the final answer directly:
        """

        # Call LLM to synthesize
        response = self._llm_call(synthesizer_prompt)
        return response

    def _llm_call(self, prompt: str) -> str:
        """
        Placeholder for actual LLM API call.
        """
        # In a real implementation, this would call the LLM provider
        logger.debug(f"Synthesizer LLM Call Prompt: {prompt[:100]}...")
        return "Synthesized Answer Placeholder"  # Mock response