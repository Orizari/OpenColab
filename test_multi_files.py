import requests
import time
from PIL import Image

# 1. Create a dummy image
img = Image.new('RGB', (100, 100), color = 'red')
img.save('test_mock_red.png')

# 2. Create a dummy text document
with open('test_mock_text.txt', 'w') as f:
    f.write("The secret password is: OPENCOLAB_ROCKS")

url = "http://localhost:8000/submit"
data = {"prompt": "Look at the attached files. What color is the image? And what is the secret password in the text document?"}

# We pass multiple files with the same key 'files' to simulate FormData
files = [
    ("files", ("test_mock_red.png", open("test_mock_red.png", "rb"), "image/png")),
    ("files", ("test_mock_text.txt", open("test_mock_text.txt", "rb"), "text/plain"))
]

print("Submitting multi-file task to Orchestrator...")
response = requests.post(url, data=data, files=files)
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
