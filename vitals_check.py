import sys
import subprocess
from pathlib import Path

def check_syntax():
    print("Checking system vitals...")
    files = ["main.py", "graph.py", "db.py"]
    for file in files:
        try:
            subprocess.run([sys.executable, '-m', 'py_compile', file], check=True, capture_output=True)
            print(f"  ✅ {file} syntax OK")
        except subprocess.CalledProcessError as e:
            print(f"  ❌ Syntax error in {file}:")
            print(e.stderr.decode())
            sys.exit(1)

def check_fastapi():
    try:
        from main import app
        print("  ✅ FastAPI 'app' instance found")
    except Exception as e:
        print(f"  ❌ Critical: FastAPI 'app' not found or import error: {e}")
        sys.exit(1)

def check_graph():
    try:
        from graph import graph
        if not hasattr(graph, 'get_state'):
            print("  ❌ Critical: graph.py missing CompiledStateGraph object 'graph'")
            sys.exit(1)
        print("  ✅ LangGraph 'graph' object verified")
    except Exception as e:
        print(f"  ❌ Critical: graph.py import failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    check_syntax()
    check_graph()
    check_fastapi()
    print("\nALL VITALS HEALTHY")
