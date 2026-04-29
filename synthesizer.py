"""
Synthesizer Module
Handles the aggregation and synthesis of results from multiple worker replicas.
"""

import json
from typing import List, Dict, Any
from dataclasses import dataclass, field

# Assuming these imports exist in your project structure
# from llm_client import LLMClient
# from prompts import SYNTHESIZER_PROMPT_TEMPLATE, SUMMARIZER_PROMPT_TEMPLATE


@dataclass
class SynthesisResult:
    """Container for the final synthesized result."""
    original_task: str
    synthesized_answer: str
    confidence_score: float = 0.0
    sources_used: List[str] = field(default_factory=list)

class Synthesizer:
    """
    The Synthesizer role acts as a sequential bottleneck after parallel worker replicas complete.
    
    It aggregates responses from multiple workers and produces a single, coherent final answer.
    """

    def __init__(self, llm_client: Any):
        """
        Initialize the Synthesizer with an LLM client.
        
        Args:
            llm_client: An instance of the LLM client used for generating responses.
        """
        self.llm_client = llm_client

    def synthesize(self, task: str, replicas: List[Dict[str, Any]]) -> SynthesisResult:
        """
        Perform synthesis on the results from worker replicas.
        
        This method first summarizes each replica's output to manage context window size
        and improve signal-to-noise ratio, then performs the final synthesis.
        
        Args:
            task: The original user task/question.
            replicas: A list of dictionaries containing worker outputs. 
                      Expected format: [{'worker_id': str, 'output': str}, ...]
                      
        Returns:
            SynthesisResult: The final synthesized answer.
        """
        if not replicas:
            return SynthesisResult(
                original_task=task,
                synthesized_answer="No results available to synthesize.",
                confidence_score=0.0
            )

        # Step 1: Pre-synthesis summarization to prevent context window exhaustion
        summarized_replicas = self._summarize_replicas(replicas)

        # Step 2: Construct the synthesis prompt with summarized data
        synthesis_prompt = self._build_synthesis_prompt(task, summarized_replicas)

        # Step 3: Generate the final answer
        try:
            response = self.llm_client.generate(synthesis_prompt)
            # Parse response if necessary (e.g., JSON extraction)
            result_data = self._parse_llm_response(response)
            
            return SynthesisResult(
                original_task=task,
                synthesized_answer=result_data.get('answer', response),
                confidence_score=result_data.get('confidence', 0.5),
                sources_used=[r['worker_id'] for r in summarized_replicas]
            )
        except Exception as e:
            return SynthesisResult(
                original_task=task,
                synthesized_answer=f"Error during synthesis: {str(e)}",
                confidence_score=0.0
            )

    def _summarize_replicas(self, replicas: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Summarize each replica's output to a fixed length for consistent input size.
        
        Args:
            replicas: Raw list of worker outputs.
            
        Returns:
            List of dictionaries with summarized outputs.
        """
        summarized = []
        
        # Define max tokens/words for summary if needed, or rely on LLM to condense
        for replica in replicas:
            worker_id = replica.get('worker_id', 'unknown')
            raw_output = replica.get('output', '')
            
            # Create a prompt to summarize this specific replica's output
            summarizer_prompt = f"""
            Summarize the following response from a worker agent concisely, preserving key facts and reasoning steps.
            Limit the summary to approximately 100 words.

            Worker ID: {worker_id}
            Raw Output:
            ---
            {raw_output}
            ---
            
            Summary:
            """
            
            try:
                summary = self.llm_client.generate(summarizer_prompt)
                summarized.append({
                    'worker_id': worker_id,
                    'summary': summary.strip()
                })
            except Exception as e:
                # Fallback to raw output if summarization fails
                summarized.append({
                    'worker_id': worker_id,
                    'summary': f"[Error summarizing]: {str(e)}"
                })
                
        return summarized

    def _build_synthesis_prompt(self, task: str, summarized_replicas: List[Dict[str, Any]]) -> str:
        """
        Build the final prompt for the synthesizer using summarized replica outputs.
        
        Args:
            task: The original user task.
            summarized_replicas: List of summarized worker outputs.
            
        Returns:
            str: The formatted prompt string.
        """
        # Format replicas into a structured string for the prompt
        replicas_str = ""
        for i, replica in enumerate(summarized_replicas):
            replicas_str += f"--- Replica {i+1} (Worker ID: {replica['worker_id']}) ---\n{replica['summary']}\n\n"

        # Use a template if available, otherwise construct manually
        prompt_template = """
        You are an expert Synthesizer. Your goal is to combine the insights from multiple worker agents 
        to provide the best possible answer to the user's task.

        User Task:
        {task}

        Worker Insights (Summarized):
        {replicas}

        Instructions:
        1. Analyze all provided summaries.
        2. Identify common themes, contradictions, and unique insights.
        3. Synthesize a single, coherent, and high-quality response.
        4. If there are contradictions, prefer the most logical or well-supported argument.
        5. Output your final answer in JSON format with keys: 'answer' (string) and 'confidence' (float between 0 and 1).

        Final Answer:
        """
        
        return prompt_template.format(task=task, replicas=replicas_str)

    def _parse_llm_response(self, response: str) -> Dict[str, Any]:
        """
        Parse the LLM response into a dictionary.
        
        Args:
            response: Raw string from LLM.
            
        Returns:
            Dict with 'answer' and 'confidence'.
        """
        try:
            # Try to parse as JSON
            data = json.loads(response)
            return {
                'answer': data.get('answer', response),
                'confidence': float(data.get('confidence', 0.5))
            }
        except json.JSONDecodeError:
            # If not valid JSON, assume the whole text is the answer and confidence is medium
            return {
                'answer': response,
                'confidence': 0.5
            }

    def synthesize_streaming(self, task: str, replicas_generator):
        """
        Placeholder for future streaming implementation (Insight #1).
        
        This method would allow the synthesizer to begin processing as replicas arrive.
        For now, it behaves like the standard synthesize method but accepts a generator.
        """
        # Convert generator to list to maintain compatibility with current logic
        # In a future iteration, this would be replaced by true streaming logic
        replicas_list = list(replicas_generator)
        return self.synthesize(task, replicas_list)