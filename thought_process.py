import os
import time
import re
from langchain_openai import ChatOpenAI

# ANSI Escape codes for different colors per step
ANSI_COLORS = {
    1: "\033[94m", # Blue
    2: "\033[96m", # Cyan
    3: "\033[92m", # Green
    4: "\033[93m", # Yellow
    5: "\033[95m", # Magenta
    6: "\033[91m", # Red
    7: "\033[97m", # White
    "RESET": "\033[0m"
}

def clean_text(text):
    """Removes simple markdown formatting like **bold** and *italic*."""
    text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)
    text = re.sub(r'\*(.*?)\*', r'\1', text)
    return text

def print_step(step_num, text, prompt=None):
    """Prints the prompt (dimmed) and then the step result with its designated color."""
    if prompt:
        # Dark grey (\033[90m) for ~50% contrast
        print(f"\033[90m[Prompt for Step {step_num}]\n{prompt}{ANSI_COLORS['RESET']}\n")
        
    color = ANSI_COLORS.get(step_num, ANSI_COLORS["RESET"])
    cleaned_text = clean_text(text)
    print(f"{color}[Step {step_num}]\n{cleaned_text}{ANSI_COLORS['RESET']}\n")

# Setup LLM based on mock_worker.py configuration
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://10.0.0.126:8001/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "Qwen3.6-35B-A3B-UD-IQ3_S.gguf")

try:
    llm = ChatOpenAI(
        base_url=LLM_BASE_URL,
        api_key="sk-no-key-required",
        model=LLM_MODEL,
        temperature=0.3,
        request_timeout=120,
    )
except Exception as e:
    print(f"Error initializing LLM: {e}")
    exit(1)

def ask_llm(system_prompt, user_prompt):
    """Helper function to interact with the LLM."""
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]
    try:
        res = llm.invoke(messages)
        return res.content.strip()
    except Exception as e:
        return f"Error communicating with LLM: {e}"

MEMORY_FILE = "memory.txt"

def read_memory():
    """Reads the long-term memory text file."""
    if not os.path.exists(MEMORY_FILE):
        return ""
    with open(MEMORY_FILE, "r") as f:
        return f.read()

def write_memory(entry):
    """Appends a new experience to the long-term memory text file."""
    with open(MEMORY_FILE, "a") as f:
        f.write(entry + "\n")

def run_thought_process():
    """Main execution flow for the Thought Process."""
    while True:
        # --- [ 1. Stimulus / Input ] ---
        # What is the best way to make good money for a software engineer that is good at understanding things? Needs to be a good side hustle. 
        user_input = input(f"{ANSI_COLORS[1]}[Step 1] Stimulus / Input (Enter trigger, or 'quit' to exit) > {ANSI_COLORS['RESET']}")
        if user_input.lower() in ['quit', 'exit']:
            print("Exiting Thought Process...")
            break
            
        stimulus = user_input
        step = 2
        
        while step <= 7:
            memory = read_memory()
            time.sleep(0.5) # Slight pause for readability
            
            if step == 2:
                # --- [ 2. Perception & Attention Filter ] ---
                system_prompt = "You are the brain's Attention Filter (Step 2)."
                user_prompt = f"Stimulus: '{stimulus}'.\nDecision: Is this important, novel, or relevant to survival/goals?\nAnswer STRICTLY with 'YES' or 'NO' on the first line, then provide a brief reason on the next line."
                
                response = ask_llm(system_prompt, user_prompt)
                print_step(2, response, prompt=user_prompt)
                
                if response.upper().startswith("YES"):
                    step = 3
                else:
                    print_step(2, "Ignored, filtered out as background noise. (End of flow)")
                    break # Back to Step 1
                    
            elif step == 3:
                # --- [ 3. Initial Assessment (System 1 - Fast Thinking) ] ---
                system_prompt = "You are System 1 (Fast Thinking) - Step 3."
                user_prompt = f"Stimulus: '{stimulus}'.\nMemory: '{memory}'.\nDecision: Is this an emergency, a familiar habit, or a highly emotional trigger?\nAnswer STRICTLY with 'YES' (to bypass deep thought and go to action) or 'NO' (to engage higher reasoning). Then provide a brief reason."
                
                response = ask_llm(system_prompt, user_prompt)
                print_step(3, response, prompt=user_prompt)
                
                if response.upper().startswith("YES"):
                    step = 6 # Bypass to Action
                else:
                    step = 4 # Proceed to Deep Processing
                    
            elif step == 4:
                # --- [ 4. Deep Processing (System 2 - Slow Thinking) ] ---
                system_prompt = "You are System 2 (Slow Thinking) - Step 4."
                user_prompt = f"""
Stimulus: '{stimulus}'
Memory limits context: '{memory[-1000:] if memory else 'None'}'

Perform Deep Processing. Analyze facts, do mental simulation, weigh emotions.
You must decide if you hit a loop or if you reach a conclusion.
First line of your response MUST BE exactly ONE of the following tags:
[SIMULATION_LOOP] (If mental trial-and-error fails, loop back in Step 4)
[EMOTION_LOOP] (If logic and emotion conflict, loop back to Step 3)
[DISTRACTION_LOOP] (If you lose focus, loop back to Step 1)
[NO_LOOP] (If you processed it successfully, proceed to Step 5)

Then provide your deep analysis/reasoning on the following lines.
"""
                response = ask_llm(system_prompt, user_prompt)
                print_step(4, response, prompt=user_prompt)
                
                first_line = response.split('\n')[0].strip().upper()
                if "SIMULATION_LOOP" in first_line:
                    print_step(4, "-> Simulation Loop Triggered! Retrying Deep Processing...")
                    step = 4
                elif "EMOTION_LOOP" in first_line:
                    print_step(4, "-> Emotion vs. Logic Loop! Reconciling with System 1...")
                    step = 3
                elif "DISTRACTION_LOOP" in first_line:
                    print_step(4, "-> Distraction Loop! Attention interrupted. Going back to start...")
                    break # Back to Step 1
                else:
                    step = 5
                    
            elif step == 5:
                # --- [ 5. Decision / Conclusion ] ---
                system_prompt = "You are the Decision Making Faculty - Step 5."
                user_prompt = f"""
Stimulus: '{stimulus}'.
Based on your deep processing, you must arrive at a choice or conclusion.
However, you might experience the "Second-Guessing Loop" (Doubt).
First line of your response MUST BE exactly ONE of the following tags:
[FINAL_DECISION] (You are confident, proceed to Step 6)
[DOUBT_LOOP] (You second-guess yourself, loop back to Step 4)

Then provide your conclusion or reason for doubt.
"""
                response = ask_llm(system_prompt, user_prompt)
                print_step(5, response, prompt=user_prompt)
                
                first_line = response.split('\n')[0].strip().upper()
                if "DOUBT_LOOP" in first_line:
                    print_step(5, "-> Second-Guessing Loop! Doubt triggered. Re-evaluating...")
                    step = 4
                else:
                    step = 6
                    
            elif step == 6:
                # --- [ 6. Action / Output ] ---
                system_prompt = "You are the Motor Cortex / Output Executor - Step 6."
                user_prompt = f"Based on the flow for stimulus '{stimulus}', what is the final action or output? Describe the execution in a short paragraph."
                
                response = ask_llm(system_prompt, user_prompt)
                print_step(6, response, prompt=user_prompt)
                action = response
                step = 7
                
            elif step == 7:
                # --- [ 7. Feedback Loop ] ---
                system_prompt = "You are the Feedback and Learning System - Step 7."
                user_prompt = f"Action taken: '{action}' for Stimulus: '{stimulus}'. Observe the result. Evaluate it. Generate a concise, single-sentence summary to be stored in long-term memory for future reference."
                
                response = ask_llm(system_prompt, user_prompt)
                print_step(7, response, prompt=user_prompt)
                
                memory_entry = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Stimulus: {stimulus} | Action: {action} | Lesson: {response}"
                write_memory(memory_entry)
                print_step(7, "-> Feedback Loop: Data stored in long-term memory. Ready for next stimulus.")
                break # Flow complete, back to Step 1

if __name__ == "__main__":
    # Create memory file if it doesn't exist
    if not os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE, "w") as f:
            f.write("Initial memory bank initialized.\n")
    
    print("=========================================")
    print("  Initializing Thought Process Flow...   ")
    print("=========================================\n")
    run_thought_process()
