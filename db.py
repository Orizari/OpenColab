import sqlite3
import json
import os
from threading import Lock
from typing import Optional

QUEUE_DB_PATH = "queue.db"
_lock = Lock()

def init_db():
    with _lock:
        with sqlite3.connect(QUEUE_DB_PATH, timeout=15.0) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS pending_tasks (
                    task_id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    assigned_worker_id TEXT,
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
            
            # Migration to add status and priority if they don't exist
            try:
                cursor.execute("ALTER TABLE threads ADD COLUMN status TEXT NOT NULL DEFAULT 'active'")
            except sqlite3.OperationalError:
                pass # Column exists
                
            try:
                cursor.execute("ALTER TABLE threads ADD COLUMN priority INTEGER NOT NULL DEFAULT 0")
            except sqlite3.OperationalError:
                pass # Column exists
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS workers (
                    worker_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL DEFAULT 'idle',
                    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
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

def get_thread_assignments(thread_id: str) -> dict:
    """Returns a dictionary mapping task_id -> assigned_worker_id for a thread."""
    with _lock:
        with sqlite3.connect(QUEUE_DB_PATH, timeout=15.0) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT task_id, assigned_worker_id FROM pending_tasks WHERE thread_id = ? AND assigned_worker_id IS NOT NULL",
                (thread_id,)
            )
            return {r[0]: r[1] for r in cursor.fetchall()}

def heartbeat(worker_id: str, status: str = 'idle'):
    """Registers or updates a worker's heartbeat."""
    with _lock:
        with sqlite3.connect(QUEUE_DB_PATH, timeout=15.0) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO workers (worker_id, status, last_seen) VALUES (?, ?, CURRENT_TIMESTAMP) "
                "ON CONFLICT(worker_id) DO UPDATE SET status = excluded.status, last_seen = CURRENT_TIMESTAMP",
                (worker_id, status)
            )
            conn.commit()

def get_telemetry() -> dict:
    """Returns the number of active workers in the last 15 seconds."""
    with _lock:
        with sqlite3.connect(QUEUE_DB_PATH, timeout=15.0) as conn:
            cursor = conn.cursor()
            # Clean up dead workers (not seen in 15 seconds)
            cursor.execute("DELETE FROM workers WHERE (CAST(strftime('%s', 'now') AS INTEGER) - CAST(strftime('%s', last_seen) AS INTEGER)) > 15")
            
            cursor.execute("SELECT worker_id, status FROM workers")
            workers = cursor.fetchall()
            
            cursor.execute("SELECT status, count(*) FROM pending_tasks GROUP BY status")
            queue_stats = dict(cursor.fetchall())
            
            return {
                "active_workers": [{"id": w[0], "status": w[1]} for w in workers],
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
                WHERE task_id = (
                    SELECT pt.task_id 
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

def push_log(task_id: str, thread_id: str, message: str):
    with _lock:
        with sqlite3.connect(QUEUE_DB_PATH, timeout=15.0) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO task_logs (task_id, thread_id, message) VALUES (?, ?, ?)",
                (task_id, thread_id, message)
            )
            conn.commit()

def get_task_logs(task_id: str) -> list[dict]:
    with _lock:
        with sqlite3.connect(QUEUE_DB_PATH, timeout=15.0) as conn:
            cursor = conn.cursor()
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
