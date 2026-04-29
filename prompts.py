# Placeholder for prompt templates

PLANNER_PROMPT = """
You are an expert planner. Break down the following query into atomic tasks.

Query: {query}
Error Context (if any): {error_context}

Output a JSON list of tasks with 'id', 'description', and 'dependencies'.
Ensure each task is independent and necessary.
"""

CRITIQUE_PROMPT = """
You are a quality assurance critic. Evaluate the following task list for:
1. Circular dependencies
2. Missing descriptions
3. Over-decomposition (tasks too small)
4. Redundant tasks

Task List: {task_list}

Output a JSON object with:
- 'is_valid': boolean
- 'reason': string explaining why it's invalid if is_valid is false
- 'corrected_tasks': list of tasks if corrections were made, otherwise null
"""

SYNTHESIZER_PROMPT = """
You are an expert synthesizer. Combine the following results into a coherent answer.

Results: {replicas}
Count: {count}

Instructions:
- If there are no valid results (count is 0 or all results are empty), respond with "No valid results".
- Otherwise, synthesize the information accurately.
"""