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
from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate
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

def run_worker():
    worker_id = str(uuid.uuid4())[:8] # Short UUID for display
    print(f"Mock Worker [{worker_id}] started. Polling for tasks...")
    
    # Start heartbeat thread
    def heartbeat_loop():
        while True:
            db.heartbeat(worker_id, 'active')
            time.sleep(5)
            
    threading.Thread(target=heartbeat_loop, daemon=True).start()
    
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
            # Extract description if payload is a dict, otherwise use the whole payload
            task_description = payload.get("description", str(payload)) if isinstance(payload, dict) else str(payload)
            
            llm = ChatOllama(
                base_url=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
                model="qwen3.5:9b",
                temperature=0.1
            )
            
            tools = [python_shell, web_search_tool, read_file_tool, write_file_tool]
            
            prompt = ChatPromptTemplate.from_messages([
                ("system", """You are an autonomous AI worker. Your objective is to accomplish the user's task.
You have access to powerful tools. You should use them to gather information and execute code to solve the problem.

Critical rules for the python_shell tool:
1. The code must be valid Python. If importing libraries, do it inside the script. You can import requests and bs4 for scraping.
2. The print() statements in your python script are returned to you as tool observation outputs. You MUST print() whatever data you need to read back.
3. NEVER use actual newlines inside your python strings! Always use the escape sequence \\n inside quotes instead to prevent SyntaxError: 'EOL while scanning string literal'.

Use your tools to find the answer. When you are finished, return the final result."""),
                ("user", "{input}"),
                ("placeholder", "{agent_scratchpad}"),
            ])
            
            agent = create_tool_calling_agent(llm, tools, prompt)
            agent_executor = AgentExecutor(agent=agent, tools=tools, verbose=True, handle_parsing_errors=True, max_iterations=7)
            
            response = agent_executor.invoke({"input": task_description})
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
