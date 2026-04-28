import time
import requests
import json
import uuid
import sys
import threading
import queue
import os
from langchain_openai import ChatOpenAI

# Configuration
OCO_API_URL = os.environ.get("OCO_API_URL", "http://localhost:8000")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://10.0.0.126:8001/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "Qwen3.6-35B-A3B-UD-IQ3_S.gguf")

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
        while not self.stop_event.is_set():
            batch = ""
            try:
                while True:
                    msg = self.log_queue.get_nowait()
                    batch += msg
            except queue.Empty:
                pass
            
            if batch:
                try:
                    requests.post(
                        f"{OCO_API_URL}/webhook/log",
                        json={
                            "thread_id": self.thread_id,
                            "task_id": self.task_id,
                            "message": batch
                        },
                        timeout=2
                    )
                except: pass
            time.sleep(1)

    def stop(self):
        self.stop_event.set()
        self.worker_thread.join(timeout=2)


def run_worker():
    worker_id = f"worker_{str(uuid.uuid4())[:6]}"
    print(f"[{worker_id}] Connecting to {OCO_API_URL}...")
    print(f"[{worker_id}] Using LLM: {LLM_BASE_URL} / {LLM_MODEL}")
    
    # Heartbeat thread
    def heartbeat_loop():
        while True:
            try:
                requests.post(f"{OCO_API_URL}/api/worker/heartbeat", json={
                    "worker_id": worker_id, 
                    "model": LLM_MODEL, 
                    "status": "ready"
                })
            except: pass
            time.sleep(5)
    threading.Thread(target=heartbeat_loop, daemon=True).start()
    
    while True:
        try:
            resp = requests.post(f"{OCO_API_URL}/api/worker/poll", json={"worker_id": worker_id})
            data = resp.json()
            if data.get("status") != "assigned":
                time.sleep(2)
                continue
            
            task = data["task"]
            task_id, thread_id, payload = task["task_id"], task["thread_id"], task["payload"]
            if isinstance(payload, str): payload = json.loads(payload)
            
            print(f"Task Received: {task_id}")
            
            original_stdout = sys.stdout
            streamer = WebhookStreamOut(task_id, thread_id, original_stdout)
            sys.stdout = streamer
            
            try:
                task_desc = payload.get("description", "")
                file_paths = payload.get("file_paths", [])
                
                llm = ChatOpenAI(
                    base_url=LLM_BASE_URL,
                    api_key="sk-no-key-required",
                    model=LLM_MODEL,
                    temperature=0.2,
                    request_timeout=300,
                )

                # Detect task type for appropriate handling
                system_roles = ["OCO Architect", "OCO Synthesizer", "OCO Quality Auditor", "OCO Strategist"]
                is_system_task = any(role in task_desc for role in system_roles)
                
                if is_system_task:
                    role_name = next(r for r in system_roles if r in task_desc)
                    print(f"⚙️  System task: {role_name}")
                    messages = [{"role": "user", "content": task_desc}]
                else:
                    print(f"🔧 Worker task: {task_id}")
                    # Build context with file attachments
                    full_prompt = task_desc
                    for fpath in file_paths:
                        if os.path.exists(fpath):
                            try:
                                with open(fpath, "r", encoding='utf-8', errors='replace') as f:
                                    content = f.read()[:10000]
                                    full_prompt += f"\n\n--- Attached File: {fpath} ---\n{content}"
                            except Exception as e:
                                full_prompt += f"\n\n--- Error reading {fpath}: {e} ---"
                    
                    messages = [
                        {"role": "system", "content": "You are an expert AI worker. Complete the given task thoroughly and accurately. Provide clear, well-structured output. If the task requires code, provide working code. If it requires analysis, be thorough and specific."},
                        {"role": "user", "content": full_prompt}
                    ]
                
                print(f"Sending to LLM ({LLM_MODEL})...")
                res = llm.invoke(messages)
                result_text = res.content
                print(f"LLM response received ({len(result_text)} chars)")
                
                if not result_text or result_text.strip() == "":
                    raise ValueError("LLM returned an empty response. This might be due to context window limits or server issues.")

                # Trace
                requests.post(f"{OCO_API_URL}/api/worker/trace", json={
                    "task_id": task_id, "thread_id": thread_id, 
                    "prompt": task_desc[:500], "reasoning": "Direct inference", "result": result_text[:1000]
                })
                
                # Submit
                requests.post(f"{OCO_API_URL}/api/worker/submit", json={"thread_id": thread_id, "task_id": task_id, "result": result_text})
                print(f"✅ Task {task_id} Completed.")

                # Autonomous System Application Logic
                if "APPLY_TASK_" in task_id:
                    print(f"[{worker_id}] Detected Application Task. Parsing for system changes...")
                    # 1. Check for File Writes
                    if "FILE_WRITE:" in result_text and "CONTENT:" in result_text:
                        try:
                            parts = result_text.split("FILE_WRITE:")
                            for part in parts[1:]:
                                filename = part.split("CONTENT:")[0].strip()
                                content = part.split("CONTENT:")[1].split("END_FILE_WRITE")[0].strip()
                                if filename and content:
                                    print(f"[{worker_id}] AUTONOMOUS EDIT: Writing to {filename}")
                                    with open(filename, "w") as f:
                                        f.write(content)
                                    print(f"[{worker_id}] File {filename} updated successfully.")
                        except Exception as e:
                            print(f"[{worker_id}] Error during file write: {e}")

                    # 2. Check for Prompt Updates
                    if "PROMPT_UPDATE:" in result_text and "NEW_PROMPT:" in result_text:
                        try:
                            prompt_name = result_text.split("PROMPT_UPDATE:")[1].split("NEW_PROMPT:")[0].strip()
                            new_prompt = result_text.split("NEW_PROMPT:")[1].split("END_PROMPT_UPDATE")[0].strip()
                            if prompt_name and new_prompt:
                                print(f"[{worker_id}] AUTONOMOUS EDIT: Updating prompt {prompt_name}")
                                requests.post(f"{OCO_API_URL}/api/system/prompts/{prompt_name}", json={"content": new_prompt})
                        except Exception as e:
                            print(f"[{worker_id}] Error during prompt update: {e}")
                
            except Exception as e:
                print(f"❌ Worker Error: {e}")
                requests.post(f"{OCO_API_URL}/api/worker/submit", json={"thread_id": thread_id, "task_id": task_id, "result": f"Error: {e}"})
            finally:
                sys.stdout = original_stdout
                streamer.stop()
                
        except Exception as e:
            print(f"Connection Error: {e}")
            time.sleep(5)

if __name__ == "__main__":
    run_worker()
