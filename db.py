import sqlite3
import json
import os
from threading import Lock
from typing import Optional

import logging

QUEUE_DB_PATH = "queue.db"
_lock = Lock()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("db")

def init_db():
    with _lock:
        with sqlite3.connect(QUEUE_DB_PATH, timeout=15.0) as conn:
            # Enable WAL mode for concurrency
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS pending_tasks (
                    task_id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    assigned_worker_id TEXT,
                    attempts INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS threads (
                    thread_id TEXT PRIMARY KEY,
                    prompt TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    priority INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Migrations
            try:
                cursor.execute("ALTER TABLE threads ADD COLUMN status TEXT NOT NULL DEFAULT 'active'")
            except sqlite3.OperationalError: pass
            try:
                cursor.execute("ALTER TABLE threads ADD COLUMN priority INTEGER NOT NULL DEFAULT 0")
            except sqlite3.OperationalError: pass

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS workers (
                    worker_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL DEFAULT 'idle',
                    model TEXT,
                    metadata TEXT,
                    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            try:
                cursor.execute("ALTER TABLE workers ADD COLUMN model TEXT")
            except sqlite3.OperationalError: pass
            try:
                cursor.execute("ALTER TABLE workers ADD COLUMN metadata TEXT")
            except sqlite3.OperationalError: pass
            try:
                cursor.execute("ALTER TABLE pending_tasks ADD COLUMN attempts INTEGER DEFAULT 0")
            except sqlite3.OperationalError: pass

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS task_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    message TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS agent_memory USING fts5(
                    topic,
                    content
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS agent_insights (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    topic TEXT NOT NULL,
                    insight TEXT NOT NULL,
                    source_thread_id TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS task_traces (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    prompt TEXT,
                    reasoning TEXT,
                    result TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS proposed_improvements (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    description TEXT UNIQUE NOT NULL,
                    votes INTEGER DEFAULT 1,
                    patch_data TEXT,
                    test_output TEXT,
                    status TEXT DEFAULT 'pending',
                    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS system_prompts (
                    name TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    version INTEGER DEFAULT 1,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()

def update_improvement_status(item_id: int, status: str, test_output: str = None):
    """Updates the verification status and test logs for an improvement."""
    with _lock:
        with sqlite3.connect(QUEUE_DB_PATH, timeout=15.0) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE proposed_improvements SET status = ?, test_output = ?, last_seen = CURRENT_TIMESTAMP WHERE id = ?",
                (status, test_output, item_id)
            )
            conn.commit()

def delete_improvement(item_id: int):
    """Removes an improvement from the ledger."""
    with _lock:
        with sqlite3.connect(QUEUE_DB_PATH, timeout=15.0) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM proposed_improvements WHERE id = ?", (item_id,))
            conn.commit()

def clear_all_improvements():
    """Wipes the entire improvements table."""
    with _lock:
        with sqlite3.connect(QUEUE_DB_PATH, timeout=15.0) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM proposed_improvements")
            conn.commit()

def get_improvement(item_id: int) -> Optional[dict]:
    """Retrieves a single improvement by ID."""
    with _lock:
        with sqlite3.connect(QUEUE_DB_PATH, timeout=15.0) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, description, patch_data, status FROM proposed_improvements WHERE id = ?", (item_id,))
            row = cursor.fetchone()
            if row:
                return {"id": row[0], "description": row[1], "patch_data": row[2], "status": row[3]}
            return None

def apply_improvement(item_id: int):
    """Marks an improvement as applied."""
    with _lock:
        with sqlite3.connect(QUEUE_DB_PATH, timeout=15.0) as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE proposed_improvements SET status = 'applied', last_seen = CURRENT_TIMESTAMP WHERE id = ?", (item_id,))
            conn.commit()

def register_thread(thread_id: str, prompt: str):
    """Saves a new thread to the history."""
    with _lock:
        with sqlite3.connect(QUEUE_DB_PATH, timeout=15.0) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR IGNORE INTO threads (thread_id, prompt) VALUES (?, ?)",
                (thread_id, prompt)
            )
            conn.commit()

def get_task_statuses(thread_id: str) -> dict:
    """Returns a dictionary mapping task_id -> {status, worker_id} for a thread."""
    with _lock:
        with sqlite3.connect(QUEUE_DB_PATH, timeout=15.0) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT task_id, status, assigned_worker_id, attempts FROM pending_tasks WHERE thread_id = ?",
                (thread_id,)
            )
            return {r[0]: {"status": r[1], "worker_id": r[2], "attempts": r[3]} for r in cursor.fetchall()}

def get_task_thread(task_id: str) -> Optional[str]:
    """Returns the thread_id for a given task_id (works for parent or replica IDs)."""
    with _lock:
        with sqlite3.connect(QUEUE_DB_PATH, timeout=15.0) as conn:
            cursor = conn.cursor()
            # Try direct match
            cursor.execute("SELECT thread_id FROM pending_tasks WHERE task_id = ?", (task_id,))
            row = cursor.fetchone()
            if row: return row[0]
            
            # Try matching as parent of replicas
            cursor.execute("SELECT thread_id FROM pending_tasks WHERE task_id LIKE ? LIMIT 1", (f"{task_id}_rep%",))
            row = cursor.fetchone()
            return row[0] if row else None

def get_replicas(parent_task_id: str) -> list[str]:
    """Returns all replica task IDs for a given parent task ID."""
    with _lock:
        with sqlite3.connect(QUEUE_DB_PATH, timeout=15.0) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT task_id FROM pending_tasks WHERE task_id LIKE ?", (f"{parent_task_id}_rep%",))
            return [r[0] for r in cursor.fetchall()]

def get_all_logs(since: str = None) -> list[dict]:
    with _lock:
        with sqlite3.connect(QUEUE_DB_PATH, timeout=15.0) as conn:
            cursor = conn.cursor()
            if since:
                cursor.execute(
                    "SELECT task_id, thread_id, message, created_at FROM task_logs WHERE created_at > ? ORDER BY created_at ASC",
                    (since,)
                )
            else:
                cursor.execute(
                    "SELECT task_id, thread_id, message, created_at FROM task_logs ORDER BY created_at DESC LIMIT 50"
                )
            rows = cursor.fetchall()
            logs = [{"task_id": r[0], "thread_id": r[1], "message": r[2], "created_at": r[3]} for r in rows]
            return logs if since else list(reversed(logs))

def delete_thread_data(thread_id: str):
    """Permanently deletes all data associated with a thread across all tables."""
    with _lock:
        with sqlite3.connect(QUEUE_DB_PATH, timeout=15.0) as conn:
            cursor = conn.cursor()
            # Delete from all relevant tables
            cursor.execute("DELETE FROM threads WHERE thread_id = ?", (thread_id,))
            cursor.execute("DELETE FROM pending_tasks WHERE thread_id = ?", (thread_id,))
            cursor.execute("DELETE FROM task_logs WHERE thread_id = ?", (thread_id,))
            cursor.execute("DELETE FROM task_traces WHERE thread_id = ?", (thread_id,))
            conn.commit()

def heartbeat(worker_id: str, status: str = 'idle', model: str = None, meta: dict = None):
    """Registers or updates a worker's heartbeat."""
    meta_json = json.dumps(meta) if meta else None
    with _lock:
        with sqlite3.connect(QUEUE_DB_PATH, timeout=15.0) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO workers (worker_id, status, model, metadata, last_seen) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP) "
                "ON CONFLICT(worker_id) DO UPDATE SET "
                "status = excluded.status, model = excluded.model, metadata = excluded.metadata, last_seen = CURRENT_TIMESTAMP",
                (worker_id, status, model, meta_json)
            )
            conn.commit()

def get_telemetry() -> dict:
    """Returns the number of active workers in the last 15 seconds."""
    with _lock:
        with sqlite3.connect(QUEUE_DB_PATH, timeout=15.0) as conn:
            cursor = conn.cursor()
            # Clean up dead workers (not seen in 15 seconds)
            cursor.execute("DELETE FROM workers WHERE (CAST(strftime('%s', 'now') AS INTEGER) - CAST(strftime('%s', last_seen) AS INTEGER)) > 15")
            
            cursor.execute("SELECT worker_id, status, model, metadata, last_seen FROM workers")
            workers = cursor.fetchall()
            
            cursor.execute("SELECT status, count(*) FROM pending_tasks GROUP BY status")
            queue_stats = dict(cursor.fetchall())
            
            return {
                "active_workers": [
                    {
                        "id": w[0], 
                        "status": w[1], 
                        "model": w[2], 
                        "metadata": json.loads(w[3]) if w[3] else {},
                        "last_seen": w[4]
                    } for w in workers
                ],
                "queue": queue_stats
            }

def get_recent_threads(limit: int = 10) -> list[dict]:
    """Returns the most recent threads."""
    with _lock:
        with sqlite3.connect(QUEUE_DB_PATH, timeout=15.0) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT thread_id, prompt, created_at FROM threads ORDER BY created_at DESC LIMIT ?", (limit,))
            rows = cursor.fetchall()
            return [{"thread_id": r[0], "prompt": r[1], "created_at": r[2]} for r in rows]

def push_task(task_id: str, thread_id: str, payload: dict):
    with _lock:
        with sqlite3.connect(QUEUE_DB_PATH, timeout=15.0) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO pending_tasks (task_id, thread_id, payload, status, updated_at) VALUES (?, ?, ?, 'pending', CURRENT_TIMESTAMP)",
                (task_id, thread_id, json.dumps(payload))
            )
            conn.commit()

def poll_task(worker_id: str) -> Optional[dict]:
    """
    Atomically fetches and marks a single pending task as 'processing'.
    Returns a dictionary with task information, or None if the queue is empty.
    """
    with _lock:
        with sqlite3.connect(QUEUE_DB_PATH, timeout=15.0) as conn:
            cursor = conn.cursor()
            
            # Reset tasks that timed out (600 seconds / 10 mins)
            # Local LLM ReAct agents can take a long time to run 7 iterations!
            cursor.execute(
                "UPDATE pending_tasks SET status = 'pending', assigned_worker_id = NULL, updated_at = CURRENT_TIMESTAMP "
                "WHERE status = 'processing' AND (CAST(strftime('%s', 'now') AS INTEGER) - CAST(strftime('%s', updated_at) AS INTEGER)) > 600"
            )
            
            # Atomically find and claim a pending task
            # We ONLY want tasks from threads that are 'active'.
            # We order by thread priority (highest first) then task creation time (oldest first).
            cursor.execute("""
                UPDATE pending_tasks 
                SET status = 'processing', assigned_worker_id = ?, updated_at = CURRENT_TIMESTAMP 
                WHERE rowid = (
                    SELECT pt.rowid 
                    FROM pending_tasks pt
                    JOIN threads t ON pt.thread_id = t.thread_id
                    WHERE pt.status = 'pending' AND t.status = 'active'
                    ORDER BY t.priority DESC, pt.created_at ASC 
                    LIMIT 1
                )
                RETURNING task_id, thread_id, payload
            """, (worker_id,))
            
            row = cursor.fetchone()
            conn.commit()
            
            if not row:
                return None
                
            task_id, thread_id, payload_str = row
            
            return {
                "task_id": task_id,
                "thread_id": thread_id,
                "payload": json.loads(payload_str)
            }

def fail_task(task_id: str, max_attempts: int = 3) -> bool:
    """
    Increments attempts. If attempts < max_attempts, resets to 'pending'.
    Returns True if reset to pending, False if max attempts reached.
    """
    with _lock:
        with sqlite3.connect(QUEUE_DB_PATH, timeout=15.0) as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE pending_tasks SET attempts = attempts + 1 WHERE task_id = ?", (task_id,))
            cursor.execute("SELECT attempts FROM pending_tasks WHERE task_id = ?", (task_id,))
            row = cursor.fetchone()
            if row and row[0] < max_attempts:
                cursor.execute("UPDATE pending_tasks SET status = 'pending', assigned_worker_id = NULL, updated_at = CURRENT_TIMESTAMP WHERE task_id = ?", (task_id,))
                conn.commit()
                return True
            conn.commit()
            return False

def complete_task(task_id: str):
    """
    Marks a task as completed in the queue database instead of deleting it, 
    so we preserve the assigned_worker_id for historical telemetry.
    """
    with _lock:
        with sqlite3.connect(QUEUE_DB_PATH, timeout=15.0) as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE pending_tasks SET status = 'completed', updated_at = CURRENT_TIMESTAMP WHERE task_id = ?", (task_id,))
            conn.commit()

def restart_task(task_id: str):
    """Resets a single task to pending status, clearing its previous worker and result."""
    with _lock:
        with sqlite3.connect(QUEUE_DB_PATH, timeout=15.0) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE pending_tasks SET status = 'pending', assigned_worker_id = NULL, attempts = 0, updated_at = CURRENT_TIMESTAMP WHERE task_id = ?",
                (task_id,)
            )
            # Also clear the trace result if it exists to avoid confusion
            cursor.execute("UPDATE task_traces SET result = NULL WHERE task_id = ?", (task_id,))
            conn.commit()

def push_log(task_id: str, thread_id: str, message: str):
    with _lock:
        with sqlite3.connect(QUEUE_DB_PATH, timeout=15.0) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO task_logs (task_id, thread_id, message) VALUES (?, ?, ?)",
                (task_id, thread_id, message)
            )
            conn.commit()

def get_task_logs(task_id: str, thread_id: str = None) -> list[dict]:
    with _lock:
        with sqlite3.connect(QUEUE_DB_PATH, timeout=15.0) as conn:
            cursor = conn.cursor()
            if thread_id:
                cursor.execute(
                    "SELECT message, created_at FROM task_logs WHERE task_id = ? AND thread_id = ? ORDER BY id ASC",
                    (task_id, thread_id)
                )
            else:
                cursor.execute(
                    "SELECT message, created_at FROM task_logs WHERE task_id = ? ORDER BY id ASC",
                    (task_id,)
                )
            rows = cursor.fetchall()
            return [{"message": r[0], "created_at": r[1]} for r in rows]

def set_thread_status(thread_id: str, status: str):
    """Sets the status of a thread (e.g., 'active' or 'paused')."""
    with _lock:
        with sqlite3.connect(QUEUE_DB_PATH, timeout=15.0) as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE threads SET status = ? WHERE thread_id = ?", (status, thread_id))
            conn.commit()

def set_thread_priority(thread_id: str, priority: int):
    """Sets the execution priority of a thread."""
    with _lock:
        with sqlite3.connect(QUEUE_DB_PATH, timeout=15.0) as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE threads SET priority = ? WHERE thread_id = ?", (priority, thread_id))
            conn.commit()

def get_thread_info(thread_id: str) -> Optional[dict]:
    """Fetches details for a specific thread."""
    with _lock:
        with sqlite3.connect(QUEUE_DB_PATH, timeout=15.0) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT prompt, status, priority FROM threads WHERE thread_id = ?", (thread_id,))
            row = cursor.fetchone()
            if row:
                return {"prompt": row[0], "status": row[1], "priority": row[2]}
            return None

def save_memory(topic: str, content: str):
    """Saves a memory to the FTS5 agent_memory table."""
    with _lock:
        with sqlite3.connect(QUEUE_DB_PATH, timeout=15.0) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO agent_memory (topic, content) VALUES (?, ?)",
                (topic, content)
            )
            conn.commit()

def search_memory(query: str) -> list[dict]:
    """Searches the agent memory using SQLite FTS5."""
    with _lock:
        with sqlite3.connect(QUEUE_DB_PATH, timeout=15.0) as conn:
            cursor = conn.cursor()
            
            # Simple FTS syntax: match any word
            words = [w for w in query.replace('"', '').split() if w.isalnum()]
            if not words:
                return []
                
            safe_query = " OR ".join(words)
            try:
                cursor.execute(
                    "SELECT topic, content FROM agent_memory WHERE agent_memory MATCH ? ORDER BY rank LIMIT 3",
                    (safe_query,)
                )
                rows = cursor.fetchall()
                return [{"topic": r[0], "content": r[1]} for r in rows]
            except sqlite3.OperationalError:
                return []

def save_insight(topic: str, insight: str, thread_id: str = None):
    """Saves a learned insight to the agent_insights table."""
    with _lock:
        with sqlite3.connect(QUEUE_DB_PATH, timeout=15.0) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO agent_insights (topic, insight, source_thread_id) VALUES (?, ?, ?)",
                (topic, insight, thread_id)
            )
            conn.commit()

def get_recent_insights(limit: int = 5) -> list[dict]:
    """Retrieves the most recent learned insights."""
    with _lock:
        with sqlite3.connect(QUEUE_DB_PATH, timeout=15.0) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT topic, insight, created_at FROM agent_insights ORDER BY created_at DESC LIMIT ?",
                (limit,)
            )
            rows = cursor.fetchall()
            return [{"topic": r[0], "insight": r[1], "created_at": r[2]} for r in rows]

def save_trace(task_id: str, thread_id: str, prompt: str, reasoning: str, result: str):
    """Saves a full execution trace."""
    with _lock:
        with sqlite3.connect(QUEUE_DB_PATH, timeout=15.0) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO task_traces (task_id, thread_id, prompt, reasoning, result) VALUES (?, ?, ?, ?, ?)",
                (task_id, thread_id, prompt, reasoning, result)
            )
            conn.commit()

def save_improvement(description: str, patch_data: str = None):
    """Saves or updates a proposed improvement, incrementing votes if it exists."""
    with _lock:
        with sqlite3.connect(QUEUE_DB_PATH, timeout=15.0) as conn:
            cursor = conn.cursor()
            # Try to increment votes if description exists
            cursor.execute("""
                INSERT INTO proposed_improvements (description, patch_data, votes, last_seen) 
                VALUES (?, ?, 1, CURRENT_TIMESTAMP)
                ON CONFLICT(description) DO UPDATE SET 
                votes = votes + 1,
                last_seen = CURRENT_TIMESTAMP
            """, (description.strip(), patch_data))
            conn.commit()

def get_top_improvements(limit: int = 10) -> list[dict]:
    """Returns the most voted improvements."""
    with _lock:
        with sqlite3.connect(QUEUE_DB_PATH, timeout=15.0) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, description, votes, patch_data, test_output, status, last_seen FROM proposed_improvements ORDER BY votes DESC, last_seen DESC LIMIT ?",
                (limit,)
            )
            rows = cursor.fetchall()
            return [{
                "id": r[0], 
                "description": r[1], 
                "votes": r[2], 
                "patch_data": r[3],
                "test_output": r[4],
                "status": r[5],
                "last_seen": r[6]
            } for r in rows]

def get_system_prompt(name: str, default_content: str) -> str:
    """Retrieves a system prompt from the database, or initializes it if missing."""
    with _lock:
        with sqlite3.connect(QUEUE_DB_PATH, timeout=15.0) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT content FROM system_prompts WHERE name = ?", (name,))
            row = cursor.fetchone()
            if row:
                return row[0]
            
            # Seed the prompt if it doesn't exist
            cursor.execute("INSERT OR IGNORE INTO system_prompts (name, content) VALUES (?, ?)", (name, default_content))
            conn.commit()
            return default_content

def update_system_prompt(name: str, content: str):
    """Updates a system prompt and increments its version."""
    with _lock:
        with sqlite3.connect(QUEUE_DB_PATH, timeout=15.0) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE system_prompts SET content = ?, version = version + 1, updated_at = CURRENT_TIMESTAMP WHERE name = ?",
                (content, name)
            )
            conn.commit()

def get_evolution_context(limit: int = 20) -> str:
    """Aggregates recent traces and proposed improvements for system-wide analysis."""
    with _lock:
        with sqlite3.connect(QUEUE_DB_PATH, timeout=15.0) as conn:
            cursor = conn.cursor()
            
            # Get recent traces
            cursor.execute("SELECT task_id, prompt, reasoning, result FROM task_traces ORDER BY created_at DESC LIMIT ?", (limit,))
            traces = cursor.fetchall()
            
            # Get proposed improvements
            cursor.execute("SELECT description, votes, status FROM proposed_improvements WHERE status = 'pending' ORDER BY votes DESC LIMIT 10")
            imps = cursor.fetchall()
            
            ctx = "## RECENT EXECUTION TRACES\n"
            for t in traces:
                ctx += f"Task: {t[0]}\nPrompt: {t[1][:200]}...\nResult Snippet: {t[3][:200]}...\n---\n"
            
            ctx += "\n## PENDING IMPROVEMENT SUGGESTIONS\n"
            for i in imps:
                ctx += f"- [{i[2].upper()}] ({i[1]} votes) {i[0]}\n"
                
            return ctx

def get_replica_stats(parent_id: str, thread_id: str) -> dict:
    """Returns the counts of replicas in different stages for a parent task."""
    with _lock:
        with sqlite3.connect(QUEUE_DB_PATH, timeout=15.0) as conn:
            cursor = conn.cursor()
            pattern = f"{parent_id}_rep%"
            cursor.execute("""
                SELECT status, COUNT(*) 
                FROM queue 
                WHERE thread_id = ? AND task_id LIKE ? 
                GROUP BY status
            """, (thread_id, pattern))
            rows = cursor.fetchall()
            stats = {"pending": 0, "dispatched": 0, "completed": 0, "failed": 0}
            for status, count in rows:
                if status in stats:
                    stats[status] = count
            return stats
