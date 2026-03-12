import requests
import time
import os

# 1. Create a dummy nested folder structure
os.makedirs("test_project/src", exist_ok=True)
os.makedirs("test_project/assets", exist_ok=True)

with open('test_project/src/main.py', 'w') as f:
    f.write('def hello():\n    print("World")')

# Generate a fast text file instead of an image
with open('test_project/assets/secret.txt', 'w') as f:
    f.write('The password is OPENCOLAB.')

url = "http://localhost:8000/submit"
data = {"prompt": "Look at the attached project folder. What are the names of the files in the prompt?"}

# We pass multiple files and their corresponding relative paths
files = [
    ("files", ("main.py", open("test_project/src/main.py", "rb"), "text/plain")),
    ("files", ("secret.txt", open("test_project/assets/secret.txt", "rb"), "text/plain"))
]

# Provide the parallel paths array just like app.js does
data_multiform = [
    ("prompt", (None, "Look at the attached project folder. Read the string contents of main.py and secret.txt and report what they say.")),
    ("paths", (None, "test_project/src/main.py")),
    ("paths", (None, "test_project/assets/secret.txt"))
]

print("Submitting multi-file folder task to Orchestrator...")
response = requests.post(url, data=data_multiform, files=files)
res = response.json()
print("Submit response:", res)
thread_id = res['thread_id']

print(f"Monitoring thread: {thread_id}")

while True:
    status_res = requests.get(f"http://localhost:8000/api/status/{thread_id}")
    if not status_res.ok:
        print("Waiting for thread initialization...", status_res.text)
        time.sleep(1)
        continue
        
    s_json = status_res.json()
    status = s_json.get('status')
    print(f"Current Status: {status}")
    
    if status == 'pending_approval':
        print("DAG requires approval. Auto-approving...")
        requests.post(f"http://localhost:8000/api/threads/{thread_id}/approve_dag")
        
    if status in ['finished', 'completed', 'error']:
        print("\nFinal State:")
        import json
        print(json.dumps(s_json, indent=2))
        break
        
    time.sleep(3)
