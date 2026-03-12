import time
import requests
import uuid
import sys
import threading
import queue
import os
import db
from langchain_community.tools import DuckDuckGoSearchRun
from langchain_experimental.utilities import PythonREPL
from langchain.tools import tool
from langchain.agents import AgentExecutor, create_react_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import HumanMessage
import base64
import subprocess
from langchain_ollama import ChatOllama

repl = PythonREPL()
search = DuckDuckGoSearchRun()

class WebhookStreamOut:
    """Redirects stdout to a queue and a separate thread sends to webhook."""
    def __init__(self, task_id, thread_id, original_stdout):
        self.original_stdout = original_stdout
        self.task_id = task_id
        self.thread_id = thread_id
        self.log_queue = queue.Queue()
        self.stop_event = threading.Event()
        self.worker_thread = threading.Thread(target=self._send_logs, daemon=True)
        self.worker_thread.start()

    def write(self, message):
        self.original_stdout.write(message)
        if message.strip():
            self.log_queue.put(message)

    def flush(self):
        self.original_stdout.flush()

    def _send_logs(self):
        import time
        while not self.stop_event.is_set():
            batch = ""
            try:
                # Batch everything currently in queue
                while True:
                    msg = self.log_queue.get_nowait()
                    batch += msg
            except queue.Empty:
                pass
            
            if batch:
                # 1. Write the batch to a local file for easy debugging
                log_file_path = f"logs/{self.thread_id}.log"
                try:
                    with open(log_file_path, "a") as f:
                        f.write(batch)
                except Exception as e:
                    print(f"\n[Worker] Warning: Could not write to log file {log_file_path}: {e}")

                # 2. Send the batch to the Orchestrator webhook for the UI
                try:
                    requests.post(
                        "http://localhost:8000/webhook/log",
                        json={
                            "thread_id": self.thread_id,
                            "task_id": self.task_id,
                            "message": batch
                        },
                        timeout=2
                    )
                except Exception:
                    pass
                    
            time.sleep(1) # Send batch every 1s

    def stop(self):
        self.stop_event.set()
        self.worker_thread.join(timeout=2)


@tool
def python_shell(code: str) -> str:
    """A Python shell. Use this to execute python scripts. Input MUST be valid python code."""
    import re
    try:
        # Local LLMs often screw up multi-line by literal escaping \n
        if "\\n" in code:
            code = code.replace("\\n", "\n")
            
        # Remove markdown quotes if hallucinated
        if code.startswith("```python"):
            code = code[9:]
        if code.startswith("```"):
            code = code[3:]
        if code.endswith("```"):
            code = code[:-3]
            
        code = code.strip()

        # Try to unwrap kwarg format if the LLM output `Action Input: code="..."`
        if code.startswith("code='") and code.endswith("'"):
            code = code[6:-1]
        elif code.startswith('code="') and code.endswith('"'):
            code = code[6:-1]
            
        code = code.strip()
        
        # Sometimes they output ACTUAL newlines inside a single quote string (e.g. print("foo \n"))
        # which breaks python with SyntaxError: EOL while scanning string literal.
        # This regex tries to fix unescaped newlines at the end of print statements before closing quotes
        code = re.sub(r'([^\\])\n"', r'\1\\n"', code)
        code = re.sub(r"([^\\])\n'", r"\1\\n'", code)

    except Exception:
        pass
    
    print(f"Executing Python Code:\n{code}")
    return repl.run(code)

@tool
def web_search_tool(query: str) -> str:
    """Use this tool to search the internet for information."""
    print(f"Searching web for: {query}")
    try:
        return search.run(query)
    except Exception as e:
        return f"Error searching: {e}"

@tool
def read_file_tool(file_path: str) -> str:
    """Read the contents of a file."""
    print(f"Reading file: {file_path}")
    try:
        with open(file_path, "r") as f:
            return f.read()
    except Exception as e:
        return f"Error reading: {e}"

@tool
def write_file_tool(input_str: str) -> str:
    """Write contents to a file. Pass 'file_path|content' as string."""
    import os
    try:
        file_path, content = input_str.split("|", 1)
        file_path = file_path.strip()
        print(f"Writing to file: {file_path}")
        
        # Create directories if they don't exist
        os.makedirs(os.path.dirname(file_path) or ".", exist_ok=True)
        
        with open(file_path, "w") as f:
            if content.startswith("\\n"):
                content = content[2:]
            f.write(content.replace("\\n", "\n"))
            
        return "File written successfully."
    except ValueError:
        return "Error! Format must be exactly: file_path|content"
    except Exception as e:
        return f"System Error writing file: {e}"

@tool
def read_lines_tool(input_str: str) -> str:
    """Read specific lines from a file. Format: 'file_path|start_line|end_line'. Line numbers are 1-indexed. Use end_line=0 to read to the end of the file."""
    try:
        parts = input_str.split("|")
        file_path = parts[0].strip()
        start = int(parts[1])
        end = int(parts[2])
        
        with open(file_path, "r") as f:
            lines = f.readlines()
            
        end_idx = end if end > 0 else len(lines)
        selected_lines = lines[start-1:end_idx]
        
        output = ""
        for i, line in enumerate(selected_lines):
            output += f"{start + i}: {line}"
        return output
    except ValueError:
        return "Error! Format must be exactly: file_path|start_line|end_line"
    except FileNotFoundError:
        return "Error! File not found."
    except Exception as e:
        return f"System Error reading file: {e}"

@tool
def replace_lines_tool(input_str: str) -> str:
    """Replace specific lines in a file. Format: 'file_path|start_line|end_line|new_content'. Replaces lines from start_line to end_line (inclusive). Use end_line=0 to replace until the end. Note that you don't need to specify the original text."""
    import os
    try:
        parts = input_str.split("|", 3)
        file_path = parts[0].strip()
        start = int(parts[1])
        end = int(parts[2])
        content = parts[3]
        
        if content.startswith("\\n"):
            content = content[2:]
        content = content.replace("\\n", "\n")
        
        os.makedirs(os.path.dirname(file_path) or ".", exist_ok=True)
        
        lines = []
        if os.path.exists(file_path):
            with open(file_path, "r") as f:
                lines = f.readlines()
        
        end_idx = end if end > 0 else len(lines)
        if start - 1 > len(lines):
            lines.extend(["\n"] * (start - 1 - len(lines)))
            
        lines[start-1:end_idx] = [content + ("" if content.endswith("\n") else "\n")]
        
        with open(file_path, "w") as f:
            f.writelines(lines)
            
        return "Lines replaced successfully."
    except ValueError:
        return "Error! Format must be exactly: file_path|start_line|end_line|new_content"
    except Exception as e:
        return f"System Error replacing lines: {e}"

@tool
def save_memory_tool(input_str: str) -> str:
    """Save important information to your long-term memory. Format: 'Topic|The information to remember'. Example: 'user_name|John Doe'."""
    try:
        topic, info = input_str.split("|", 1)
        db.save_memory(topic.strip(), info.strip())
        return f"Saved to memory under topic '{topic}'."
    except ValueError:
        return "Error! Format must be exactly: Topic|The information to remember"
    except Exception as e:
        return f"System Error saving memory: {e}"

@tool
def search_memory_tool(query: str) -> str:
    """Search your long-term memory for previously saved information using a keyword query."""
    try:
        results = db.search_memory(query)
        if not results:
            return "No relevant memories found."
        
        memories = [f"Topic: {r['topic']} - {r['content']}" for r in results]
        return "Found memories:\n" + "\n".join(memories)
    except Exception as e:
        return f"Error searching memory: {e}"

@tool
def list_files_tool(directory: str = ".") -> str:
    """Recursively list files in a directory, ignoring common ignored folders like .git and venv."""
    print(f"Listing files in: {directory}")
    ignored = {".git", "venv", "__pycache__", ".DS_Store", "logs"}
    file_list = []
    try:
        for root, dirs, files in os.walk(directory):
            # Prune ignored directories
            dirs[:] = [d for d in dirs if d not in ignored]
            for file in files:
                if file not in ignored:
                    rel_path = os.path.relpath(os.path.join(root, file), directory)
                    file_list.append(rel_path)
        return "\n".join(file_list) if file_list else "No files found."
    except Exception as e:
        return f"Error listing files: {e}"

@tool
def grep_search_tool(input_str: str) -> str:
    """Search for a pattern in files. Format: 'directory|pattern'. Pattern can be a simple string or regex."""
    try:
        directory, pattern = input_str.split("|", 1)
        directory = directory.strip()
        print(f"Searching for '{pattern}' in {directory}")
        
        results = []
        ignored = {".git", "venv", "__pycache__", ".DS_Store", "logs"}
        import re
        
        for root, dirs, files in os.walk(directory):
            dirs[:] = [d for d in dirs if d not in ignored]
            for file in files:
                if file not in ignored:
                    file_path = os.path.join(root, file)
                    try:
                        with open(file_path, "r", encoding='utf-8', errors='ignore') as f:
                            for i, line in enumerate(f, 1):
                                if re.search(pattern, line):
                                    rel_path = os.path.relpath(file_path, directory)
                                    results.append(f"{rel_path}:{i}: {line.strip()}")
                    except Exception:
                        continue
        
        if not results:
            return "No matches found."
        return "\n".join(results[:50]) # Cap at 50 results
    except ValueError:
        return "Error! Format must be exactly: directory|pattern"
    except Exception as e:
        return f"System Error searching: {e}"

@tool
def run_tests_tool(command: str = "pytest") -> str:
    """Run a test command (e.g., 'pytest' or 'python test_script.py') and return the output."""
    print(f"Running test command: {command}")
    try:
        # We limit execution to simple test commands for safety
        allowed_commands = ["pytest", "python", "python3"]
        cmd_parts = command.split()
        if not cmd_parts or cmd_parts[0] not in allowed_commands:
            return f"Error: Command '{command}' not allowed. Use pytest or python script.py."
            
        result = subprocess.run(
            command, 
            shell=True, 
            capture_output=True, 
            text=True, 
            timeout=30
        )
        return f"STDOUT:\n{result.stdout}\n\nSTDERR:\n{result.stderr}"
    except subprocess.TimeoutExpired:
        return "Error: Test command timed out after 30 seconds."
    except Exception as e:
        return f"System Error running tests: {e}"

def run_worker():
    worker_id = str(uuid.uuid4())[:8] # Short UUID for display
    print(f"Mock Worker [{worker_id}] started. Polling for tasks...")
    
    # Start heartbeat thread
    def heartbeat_loop():
        import time
        while True:
            try:
                db.heartbeat(worker_id, 'active')
            except Exception:
                pass
            time.sleep(5)
            
    h_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    h_thread.start()
    
    while True:
        task = db.poll_task(worker_id)
        if not task:
            time.sleep(1)
            continue
            
        task_id = task["task_id"]
        thread_id = task["thread_id"]
        payload = task["payload"]
        
        print(f"Worker [{worker_id}] picked up task: {task_id} on thread: {thread_id}")
        print(f"Payload: {payload}")
        print("Generating real AI response via autonomous Agent...")
        
        # Start intercepting logic logs
        original_stdout = sys.stdout
        streamer = WebhookStreamOut(task_id, thread_id, original_stdout)
        sys.stdout = streamer
        
        try:
            # Extract description and files if payload is a dict
            task_description = payload.get("description", str(payload)) if isinstance(payload, dict) else str(payload)
            file_paths = payload.get("file_paths", []) if isinstance(payload, dict) else []
            
            llm = ChatOllama(
                base_url=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
                model="qwen3.5:9b",
                temperature=0.1
            )
            
            tools = [
                python_shell, web_search_tool, read_file_tool, write_file_tool, 
                read_lines_tool, replace_lines_tool, save_memory_tool, search_memory_tool,
                list_files_tool, grep_search_tool, run_tests_tool
            ]
            
            system_prompt = """You are an autonomous AI worker. Your objective is to accomplish the user's task.
You have access to powerful tools. You should use them to gather information and execute code to solve the problem.

Critical rules for the python_shell tool:
1. The code must be valid Python. If importing libraries, do it inside the script. You can import requests and bs4 for scraping.
2. The print() statements in your python script are returned to you as tool observation outputs. You MUST print() whatever data you need to read back.
3. NEVER use actual newlines inside your python strings! Always use the escape sequence \\n inside quotes instead to prevent SyntaxError: 'EOL while scanning string literal'.

CRITICAL RULES FOR ATTACHED DOCUMENTS:
If a document or file is attached as text at the bottom of the prompt, YOU ALREADY HAVE ITS CONTENTS. You DO NOT need to use the `read_file_tool` to read it. Just read the text provided to you and formulate your final answer.

TOOLS:
------
You have access to the following tools:

{tools}

To use a tool, please use the exact following format:

```
Thought: Do I need to use a tool? Yes
Action: the action to take, should be one of [{tool_names}]
Action Input: the input to the action
Observation: the result of the action
```

CRITICAL FORMATTING RULES:
1. NEVER use function calling syntax like `Action: tool_name(args)`. This will crash the system!
2. You MUST put the tool name alone on the Action line.
3. You MUST put the arguments on the Action Input line.

Good Example:
Thought: I need to search the web format.
Action: web_search_tool
Action Input: weather in Melbourne

BAD Example (DO NOT DO THIS):
Thought: I need to search the web format.
Action: web_search_tool(query="weather in Melbourne")

When you have a response to say to the Human, or if you already know the answer (e.g. from an attached document), you MUST use the format:

```
Thought: Do I need to use a tool? No
Final Answer: [your response here]
```

Begin!

Thought:{agent_scratchpad}"""
            
            prompt = ChatPromptTemplate.from_messages([
                ("system", system_prompt),
                MessagesPlaceholder(variable_name="input")
            ])
            
            agent = create_react_agent(llm, tools, prompt)
            agent_executor = AgentExecutor(agent=agent, tools=tools, verbose=True, handle_parsing_errors=True, max_iterations=7)
            
            # Prepare multimodal input message
            message_content = [{"type": "text", "text": f"Task Details: {task_description}"}]
            
            for fpath in file_paths:
                if os.path.exists(fpath):
                    ext = fpath.split('.')[-1].lower()
                    
                    # If Image
                    if ext in ['png', 'jpeg', 'jpg', 'gif', 'webp']:
                        with open(fpath, "rb") as f:
                            b64 = base64.b64encode(f.read()).decode("utf-8")
                        mime = f"image/{ext}" if ext in ['png', 'jpeg', 'jpg', 'gif', 'webp'] else "image/jpeg"
                        message_content.append({
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{b64}"}
                        })
                        print(f"Attached parsed image to prompt: {fpath}")
                    
                    # If Text/Document
                    else:
                        with open(fpath, "r", encoding='utf-8', errors='replace') as f:
                            text_content = f.read()
                        
                        # We truncate severely long text blocks explicitly to not blow up context
                        if len(text_content) > 30000:
                            text_content = text_content[:30000] + "\n...[TRUNCATED]"
                            
                        # Use the relative path within workspace/{thread_id} so the LLM understands folder structure
                        path_parts = fpath.split('/')
                        display_name = "/".join(path_parts[2:]) if len(path_parts) > 2 and path_parts[0] == "workspace" else fpath
                            
                        message_content.append({
                            "type": "text", 
                            "text": f"\n\n--- Attachment ({display_name}) ---\n{text_content}"
                        })
                        print(f"Attached parsed text document to prompt: {display_name}")

            human_msg = HumanMessage(content=message_content)
            
            response = agent_executor.invoke({"input": [human_msg]})
            result_data = response.get("output", str(response))
            print("Successfully generated autonomous response.")
        except Exception as e:
            print(f"Error generating autonomous response: {e}")
            result_data = f"Error generating autonomous response: {e}"
        finally:
            # Stop intercepting
            sys.stdout = original_stdout
            streamer.stop()
        
        print(f"Work finished. Sending webhook for {task_id}...")
        
        try:
            resp = requests.post(
                "http://localhost:8000/webhook/result",
                json={
                    "thread_id": thread_id,
                    "task_id": task_id,
                    "result": result_data
                }
            )
            resp.raise_for_status()
            print(f"Successfully reported completion of {task_id}\n")
        except Exception as e:
            print(f"Error sending webhook for {task_id}: {e}\n")

if __name__ == "__main__":
    run_worker()
