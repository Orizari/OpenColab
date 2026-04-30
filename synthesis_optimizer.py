"""
Synthesis Optimizer Module

This module provides a pre-synthesis summarization step to address context window exhaustion
and attention dilution in the Synthesizer role. It summarizes verbose worker outputs
before passing them to the main synthesizer, ensuring consistent input size and higher
signal-to-noise ratio.
"""

from typing import List, Dict, Any
import asyncio


class PreSynthesisSummarizer:
    """
    Summarizes raw replica results before they are passed to the Synthesizer.
    
    This addresses Insight #2: Context Window Exhaustion in `Synthesizer`.
    By summarizing each replica to a fixed length, we prevent token limit issues
    and improve the quality of the final synthesis.
    """

    def __init__(self, llm_client, max_summary_tokens: int = 500):
        """
        Initialize the summarizer.

        Args:
            llm_client: An instance of an LLM client (e.g., OpenAI, Anthropic) 
                        capable of generating summaries.
            max_summary_tokens: The maximum number of tokens for each summary.
        """
        self.llm_client = llm_client
        self.max_summary_tokens = max_summary_tokens

    async def summarize_replica(self, replica_id: str, raw_content: str) -> str:
        """
        Generate a concise summary of a single replica's output.

        Args:
            replica_id: Identifier for the worker replica.
            raw_content: The full text output from the worker.

        Returns:
            A summarized string of the replica's output.
        """
        # In a real implementation, this would call the LLM with a specific summarization prompt.
        # Example prompt structure:
        # "Summarize the following response concisely, focusing on key arguments and conclusions. 
        # Max tokens: {max_summary_tokens}. \n\n Content: {raw_content}"
        
        # Placeholder for actual LLM call
        summary = await self.llm_client.generate(
            prompt=f"Summarize the following text concisely (max {self.max_summary_tokens} tokens):\n\n{raw_content}",
            max_tokens=self.max_summary_tokens,
            temperature=0.2  # Low temperature for consistent summarization
        )
        return summary

    async def process_replicas(self, replicas: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Process all replicas by summarizing their content in parallel.

        Args:
            replicas: A list of dictionaries containing replica data. 
                      Expected format: [{'replica_id': str, 'content': str}, ...]

        Returns:
            A list of dictionaries with summarized content.
        """
        async def _summarize_one(replica):
            summary = await self.summarize_replica(replica['replica_id'], replica['content'])
            return {
                'replica_id': replica['replica_id'],
                'summary': summary,
                'original_length': len(replica['content']),
                'summary_length': len(summary)
            }

        # Run all summarizations in parallel to minimize latency overhead
        summaries = await asyncio.gather(*[_summarize_one(r) for r in replicas])
        
        return summaries

    def format_for_synthesizer(self, summarized_replicas: List[Dict[str, Any]]) -> str:
        """
        Format the summarized replicas into a string suitable for the Synthesizer prompt.

        Args:
            summarized_replicas: The list of summarized replica data.

        Returns:
            A formatted string containing all summaries.
        """
        formatted_parts = []
        for item in summarized_replicas:
            formatted_parts.append(
                f"Replica {item['replica_id']} Summary:\n{item['summary']}\n---"
            )
        
        return "\n\n".join(formatted_parts)

# Example Usage:
# summarizer = PreSynthesisSummarizer(llm_client=openai_client, max_summary_tokens=300)
# raw_replicas = [{'replica_id': 'r1', 'content': '...'}, {'replica_id': 'r2', 'content': '...'}]
# summarized = await summarizer.process_replicas(raw_replicas)
# formatted_input = summarizer.format_for_synthesizer(summarized)
# final_result = synthesizer.synthesize(formatted_input)