import json
import re
from typing import List, Dict, Any, Optional

# Assuming these constants and imports exist in the broader system context
# from config import MAX_TASKS, DEFAULT_K_FACTOR
# from utils import parse_json_response

class Planner:
    """
    Orchestrates the decomposition of user queries into executable tasks.
    Implements complexity detection to prevent over-decomposition of simple queries.
    """

    def __init__(self):
        self.system_prompt = """
You are an expert task planner for a multi-agent AI system. Your goal is to decompose user requests into minimal, efficient, and parallelizable sub-tasks.

Guidelines:
1. Analyze the complexity of the user's request.
2. If the request is simple (e.g., asking for a joke, a short fact, or a direct answer), create exactly ONE task with k_factor=1. Do not decompose further.
3. For complex requests, break them down into logical sub-tasks. Keep the plan minimal; avoid unnecessary parallelism if sequential steps are clearer.
4. Output valid JSON matching the schema: {"tasks": [{"id": int, "description": str, "k_factor": int, "dependencies": list[int]}]}
"""

    def _detect_intent_complexity(self, query: str) -> bool:
        """
        Heuristic check to determine if a query is simple.
        Returns True if the query appears simple and should not be decomposed.
        """
        simple_indicators = [
            "tell me a joke", 
            "what is", 
            "who is", 
            "define", 
            "translate", 
            "summarize this:", # Short summaries might be single task
            "write a short email"
        ]
        
        query_lower = query.lower().strip()
        
        # Check for direct simple patterns
        for indicator in simple_indicators:
            if indicator in query_lower:
                return True
        
        # If length is very short and no complex connectors, likely simple
        if len(query.split()) < 10 and '?' not in query and ',' not in query:
            return True
            
        return False

    def plan(self, user_query: str, available_files: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Generates a task plan based on the user query.
        
        Args:
            user_query: The original user request.
            available_files: List of file metadata/contents available in workspace.
            
        Returns:
            A dictionary containing the structured task plan.
        """
        
        # Insight 1: Enforce hard limit for simple queries
        if self._detect_intent_complexity(user_query):
            return {
                "tasks": [
                    {
                        "id": 1,
                        "description": user_query,
                        "k_factor": 1,
                        "dependencies": [],
                        "relevant_files": [] # No files needed for trivial queries usually
                    }
                ]
            }

        # For complex queries, use the LLM to generate a plan
        try:
            response = self._call_llm(self.system_prompt, user_query)
            tasks = self._parse_tasks(response)
            
            # Attach relevant files based on task dependencies/content analysis
            # Insight 2: Filter files instead of dumping all. 
            # Here we assume a simple heuristic or LLM call to select files.
            enriched_tasks = []
            for task in tasks:
                # Simple relevance filter: Check if task description mentions file names
                # In production, this might be more sophisticated (embedding similarity)
                relevant_files = self._filter_relevant_files(task["description"], available_files)
                task["relevant_files"] = relevant_files
                enriched_tasks.append(task)
                
            return {"tasks": enriched_tasks}
            
        except Exception as e:
            # Fallback to single task if planning fails
            return {
                "tasks": [
                    {
                        "id": 1,
                        "description": user_query,
                        "k_factor": 1,
                        "dependencies": [],
                        "relevant_files": []
                    }
                ]
            }

    def _call_llm(self, system_prompt: str, user_query: str) -> str:
        # Placeholder for actual LLM API call
        pass

    def _parse_tasks(self, response: str) -> List[Dict]:
        # Placeholder for JSON parsing logic
        try:
            data = json.loads(response)
            return data.get("tasks", [])
        except json.JSONDecodeError:
            return []

    def _filter_relevant_files(self, task_description: str, available_files: List[Dict]) -> List[str]:
        """
        Insight 2 Implementation: Only attach files explicitly referenced or highly relevant.
        Returns a list of file identifiers/paths to include in the worker prompt.
        """
        if not available_files:
            return []
        
        # Simple keyword matching for demonstration
        # In reality, use embeddings or explicit dependency links from Planner
        keywords = set(task_description.lower().split())
        relevant = []
        
        for file_info in available_files:
            fname = file_info.get("name", "").lower()
            # Check if filename appears in task description
            if fname in keywords:
                relevant.append(file_info["id"])
                
        return relevant