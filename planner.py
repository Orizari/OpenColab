import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

class Planner:
    def __init__(self, prompt_template: str, critique_prompt_template: str):
        self.prompt_template = prompt_template
        self.critique_prompt_template = critique_prompt_template

    def plan(self, query: str, error_context: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Generates a list of tasks based on the query.
        
        Args:
            query: The user's input query.
            error_context: Optional context from previous failures to guide re-planning.
            
        Returns:
            A list of task dictionaries.
        """
        try:
            prompt = self.prompt_template.format(
                query=query,
                error_context=error_context if error_context else "None"
            )
            
            response = self._call_llm(prompt)
            tasks = self._parse_tasks(response)
            return tasks
            
        except Exception as e:
            logger.error(f"Planning failed: {str(e)}")
            return []

    def critique(self, task_list: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Validates the generated plan for structural integrity.
        
        Args:
            task_list: The list of tasks to validate.
            
        Returns:
            A dictionary with 'is_valid' boolean and potentially 'reason' or 'corrected_tasks'.
        """
        try:
            prompt = self.critique_prompt_template.format(
                task_list=task_list
            )
            
            response = self._call_llm(prompt)
            result = self._parse_critique(response)
            return result
            
        except Exception as e:
            logger.error(f"Critique failed: {str(e)}")
            return {"is_valid": False, "reason": "Critique process failed"}

    def _parse_tasks(self, response: str) -> List[Dict[str, Any]]:
        """Parse LLM response into task list."""
        # Placeholder for JSON parsing or regex extraction
        try:
            import json
            return json.loads(response)
        except json.JSONDecodeError:
            logger.warning("Failed to parse tasks from LLM response")
            return []

    def _parse_critique(self, response: str) -> Dict[str, Any]:
        """Parse LLM critique response."""
        # Placeholder for JSON parsing
        try:
            import json
            return json.loads(response)
        except json.JSONDecodeError:
            logger.warning("Failed to parse critique from LLM response")
            return {"is_valid": False}

    def _call_llm(self, prompt: str) -> str:
        """Abstract method to call the LLM."""
        pass