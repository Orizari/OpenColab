import requests
import time
from PIL import Image

# Create a dummy image
img = Image.new('RGB', (100, 100), color = 'blue')
img.save('test_mock.png')

url = "http://localhost:8000/submit"
data = {"prompt": "Look at the attached image. What is the dominant color of the image? Is it blue?"}
files = {"image": open("test_mock.png", "rb")}

print("Submitting task to Orchestrator...")
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
        print("Final State:", s_json)
        break
        
    time.sleep(3)
