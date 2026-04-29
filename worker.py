import json
import os
from typing import Dict, Any, List

class Worker:
    """
    Executes individual tasks using an LLM.
    Implements relevance filtering to prevent context window bloat.
    """

    def __init__(self):
        self.system_prompt = """
You are an AI worker executing a specific sub-task. 
Use the provided context and files to generate the result.
If you cannot complete the task, return "ERROR: Unable to complete task".
"""

    def execute(self, task: Dict[str, Any], workspace_files: Dict[str, str]) -> Dict[str, Any]:
        """
        Executes a single task.
        
        Args:
            task: The task dictionary from the planner.
            workspace_files: Dictionary mapping file_id to full content string.
            
        Returns:
            A dictionary containing the result and status.
        """
        task_id = task["id"]
        description = task["description"]
        
        # Insight 2: Relevance Filter
        # Only inject files explicitly marked as relevant by the Planner
        relevant_file_ids = task.get("relevant_files", [])
        context_content = ""
        
        for file_id in relevant_file_ids:
            if file_id in workspace_files:
                content = workspace_files[file_id]
                # Truncate if too long to save tokens, but keep integrity
                if len(content) > 5000:
                    content = content[:5000] + "... [content truncated]"
                context_content += f"\n--- File {file_id} ---\n{content}\n"
            else:
                # Log warning if file is missing but referenced
                pass

        prompt = f"{self.system_prompt}\n\nTask Description:\n{description}\n\nContext:\n{context_content}"

        try:
            result_text = self._call_llm(prompt)
            
            # Insight 3: Pre-synthesis validation
            if not result_text or result_text.strip() == "":
                return {
                    "task_id": task_id,
                    "status": "ERROR",
                    "result": None,
                    "error_message": "Empty response from worker"
                }
            
            # Check for explicit error strings
            if "ERROR:" in result_text:
                return {
                    "task_id": task_id,
                    "status": "ERROR",
                    "result": None,
                    "error_message": result_text
                }

            return {
                "task_id": task_id,
                "status": "SUCCESS",
                "result": result_text.strip()
            }
            
        except Exception as e:
            return {
                "task_id": task_id,
                "status": "ERROR",
                "result": None,
                "error_message": str(e)
            }

    def _call_llm(self, prompt: str) -> str:
        # Placeholder for actual LLM API call
        return "Mock Result"