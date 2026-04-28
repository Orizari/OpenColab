import sys
import os
import subprocess
import importlib.util

def check_syntax():
    print("🔍 Checking Python syntax...")
    files = [f for f in os.listdir('.') if f.endswith('.py')]
    for file in files:
        try:
            subprocess.run([sys.executable, '-m', 'py_compile', file], check=True, capture_output=True)
            print(f"  ✅ {file} syntax OK")
        except subprocess.CalledProcessError as e:
            print(f"  ❌ Syntax error in {file}:")
            print(e.stderr.decode())
            return False
    return True

def check_imports():
    print("🔍 Verifying core imports...")
    core_modules = ['main', 'db', 'graph']
    for mod_name in core_modules:
        if os.path.exists(f"{mod_name}.py"):
            try:
                # Use sub-process to avoid polluting this process
                subprocess.run([sys.executable, "-c", f"import {mod_name}"], check=True, capture_output=True)
                print(f"  ✅ import {mod_name} OK")
            except subprocess.CalledProcessError as e:
                print(f"  ❌ Failed to import {mod_name}:")
                print(e.stderr.decode())
                return False
    return True

def check_fastapi():
    print("🔍 Verifying FastAPI application...")
    try:
        # Use sub-process to avoid polluting this process
        subprocess.run([sys.executable, "-c", "from main import app"], check=True, capture_output=True)
        print("  ✅ FastAPI 'app' found in main.py")
        return True
    except Exception as e:
        print("  ❌ FastAPI 'app' MISSING or broken in main.py")
        return False

def main():
    print("--- OpenColab Vitals Check ---")
    if not check_syntax():
        sys.exit(1)
    if not check_imports():
        sys.exit(1)
    if not check_fastapi():
        sys.exit(1)
    print("--- 🚀 System Vitals: HEALTHY ---")
    sys.exit(0)

if __name__ == "__main__":
    main()
