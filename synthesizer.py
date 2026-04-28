import logging
from typing import List, Dict, Any, Optional

# Assuming this import exists in your project structure
# from prompts import SYNTHESIZER_PROMPT

logger = logging.getLogger(__name__)

class Synthesizer:
    def __init__(self, llm_client):
        self.llm_client = llm_client

    def synthesize(self, user_query: str, replicas: List[Dict[str, Any]]) -> str:
        """
        Synthesizes results from multiple workers into a final answer.
        """
        # Insight #3: Robustness Check for Empty/Insufficient Replicas
        if not replicas or len(replicas) == 0:
            return "No valid results were found to synthesize."

        # Filter out failed/empty results if necessary, but keep track of count
        valid_replicas = [r for r in replicas if r.get("status") == "success" and r.get("content")]
        
        k_factor = 1  # Minimum required valid replicas
        
        if len(valid_replicas) < k_factor:
            logger.warning(f"Insufficient valid replicas ({len(valid_replicas)}) for synthesis.")
            return "No valid results were found to synthesize."

        # Prepare data for prompt
        prompt_data = {
            "user_query": user_query,
            "replicas": valid_replicas
        }
        
        # Generate final answer using LLM
        try:
            response = self.llm_client.generate(
                prompt=self._build_synthesis_prompt(prompt_data),
                temperature=0.1
            )
            return response
        except Exception as e:
            logger.error(f"Synthesis failed: {str(e)}")
            return "Failed to synthesize results."

    def _build_synthesis_prompt(self, data: Dict[str, Any]) -> str:
        """
        Builds the synthesis prompt with robustness instructions.
        """
        # Placeholder for actual prompt template
        # In a real implementation, this would use a template string
        
        replicas_json = [r.get("content", "") for r in data["replicas"]]
        
        prompt_template = f"""
        You are an expert synthesizer. Your task is to combine the following results into a coherent answer.

        User Query: {data['user_query']}
        
        Results from workers:
        {replicas_json}

        IMPORTANT INSTRUCTIONS:
        1. If all replicas are empty or invalid, explicitly return: "No valid results were found."
        2. Do not hallucinate information if the provided results do not contain enough context.
        3. Synthesize a concise and accurate answer based ONLY on the provided replicas.
        """
        
        return prompt_template