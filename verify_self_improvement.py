import db
import os
import json

def test_db_changes():
    print("Testing DB Changes...")
    db.init_db()
    
    # Test adding an insight
    db.save_insight("Test Topic", "This is a test insight for self-improvement.", "thread_123")
    insights = db.get_recent_insights()
    
    assert len(insights) > 0
    assert insights[0]['topic'] == "Test Topic"
    print("✓ DB Insights storage verified.")

def test_worker_tools():
    print("\nTesting Worker meta-tools code (Simulation)...")
    from mock_worker import list_files_tool, grep_search_tool
    
    # Test list_files
    files = list_files_tool(".")
    assert "main.py" in files
    assert "graph.py" in files
    print("✓ list_files_tool verified.")
    
    # Test grep_search
    matches = grep_search_tool(".|planner_node")
    assert "graph.py" in matches
    print("✓ grep_search_tool verified.")

if __name__ == "__main__":
    try:
        test_db_changes()
        test_worker_tools()
        print("\nALL VERIFICATIONS PASSED!")
    except Exception as e:
        print(f"\nVERIFICATION FAILED: {e}")
        exit(1)
