// Mission Control Logic
const API_BASE = 'http://localhost:8000';
let activeThreadId = null;
let pollInterval = null;
let logPollInterval = null;
let nodesMap = {};
let currentGraphState = null;
let lastLogTimestamp = null;

// DOM Elements
const form = document.getElementById('newJobForm');
const promptInput = document.getElementById('promptInput');
const imageInput = document.getElementById('imageInput');
const btnText = document.querySelector('.btn-text');
const spinner = document.querySelector('.spinner');
const threadList = document.getElementById('threadList');
const currentThreadIdDisplay = document.getElementById('currentThreadId');
const currentStatusText = document.getElementById('currentStatusText');
const statusDot = document.getElementById('statusDot');
const nodesContainer = document.getElementById('nodesContainer');
const svgCanvas = document.getElementById('svgCanvas');
const emptyState = document.getElementById('emptyState');
const nodeTemplate = document.getElementById('nodeTemplate');
const activeWorkersCount = document.getElementById('activeWorkersCount');
const taskDrawer = document.getElementById('taskDrawer');
const closeDrawerBtn = document.getElementById('closeDrawerBtn');
const pipelineProgress = document.getElementById('pipelineProgress');
const finalAnswerPanel = document.getElementById('finalAnswerPanel');
const finalAnswerBody = document.getElementById('finalAnswerBody');
const copyResultBtn = document.getElementById('copyResultBtn');

// Modals
const improvementsModal = document.getElementById('improvementsModal');
const btnViewImprovements = document.getElementById('btnViewImprovements');
const closeImprovementsBtn = document.getElementById('closeImprovementsBtn');
const improvementsList = document.getElementById('improvementsList');
const workersModal = document.getElementById('workersModal');
const btnViewWorkers = document.getElementById('btnViewWorkers');
const closeWorkersBtn = document.getElementById('closeWorkersBtn');
const workersList = document.getElementById('workersList');

// Sidebar & Controls
const sidebar = document.getElementById('sidebar');
const sidebarResizer = document.getElementById('sidebarResizer');
const threadControls = document.getElementById('threadControls');
const btnApproveDag = document.getElementById('btnApproveDag');
const btnTogglePause = document.getElementById('btnTogglePause');
const btnRestart = document.getElementById('btnRestart');
const prioritySelect = document.getElementById('prioritySelect');

// Drawer fields
const drawerTaskId = document.getElementById('drawerTaskId');
const drawerStatusBadge = document.getElementById('drawerStatusBadge');
const drawerWorkerId = document.getElementById('drawerWorkerId');
const drawerAttempts = document.getElementById('drawerAttempts');
const drawerDescription = document.getElementById('drawerDescription');
const drawerResult = document.getElementById('drawerResult');

// Log Panel
const logPanel = document.getElementById('logPanel');
const logHeader = document.getElementById('logHeader');
const logBody = document.getElementById('logBody');
const toggleLogPanelBtn = document.getElementById('toggleLogPanelBtn');
const clearLogsBtn = document.getElementById('clearLogsBtn');

// ===================== INIT =====================
async function init() {
    await fetchThreads();
    pollTelemetry();
    setInterval(pollTelemetry, 3000);

    closeDrawerBtn.addEventListener('click', () => {
        taskDrawer.classList.remove('open');
        if (logPollInterval) { clearInterval(logPollInterval); logPollInterval = null; }
    });

    btnViewImprovements.addEventListener('click', () => { improvementsModal.classList.add('active'); fetchImprovements(); });
    closeImprovementsBtn.addEventListener('click', () => { improvementsModal.classList.remove('active'); });
    document.getElementById('btnClearImprovements').addEventListener('click', clearAllImprovements);
    btnViewWorkers.addEventListener('click', () => { workersModal.classList.add('active'); fetchWorkerDetails(); });
    closeWorkersBtn.addEventListener('click', () => { workersModal.classList.remove('active'); });

    [improvementsModal, workersModal].forEach(modal => {
        modal.addEventListener('click', (e) => { if (e.target === modal) modal.classList.remove('active'); });
    });

    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const prompt = promptInput.value.trim();
        if (!prompt) return;
        btnText.textContent = "Launching...";
        try {
            const formData = new FormData();
            formData.append('prompt', prompt);
            if (imageInput.files.length > 0) {
                for (let i = 0; i < imageInput.files.length; i++) {
                    const file = imageInput.files[i];
                    formData.append('files', file);
                    formData.append('paths', file.webkitRelativePath || file.name);
                }
            }
            const res = await fetch(`${API_BASE}/submit`, { method: 'POST', body: formData });
            const data = await res.json();
            promptInput.value = '';
            imageInput.value = '';
            await fetchThreads();
            selectThread(data.thread_id);
        } catch (err) {
            console.error("Failed to launch job:", err);
            alert("Error launching job. Is the backend running?");
        } finally {
            btnText.textContent = "🚀 Launch Job";
        }
    });

    // Thread Controls
    btnTogglePause.addEventListener('click', async () => {
        if (!activeThreadId) return;
        const isPaused = currentStatusText.textContent.toLowerCase() === 'paused';
        const endpoint = isPaused ? 'resume' : 'pause';
        try {
            await fetch(`${API_BASE}/api/threads/${activeThreadId}/${endpoint}`, { method: 'POST' });
            pollThreadStatus();
        } catch (e) { console.error("Failed to toggle pause", e); }
    });

    btnRestart.addEventListener('click', async () => {
        if (!activeThreadId) return;
        try {
            const res = await fetch(`${API_BASE}/api/threads/${activeThreadId}/restart`, { method: 'POST' });
            const data = await res.json();
            await fetchThreads();
            selectThread(data.thread_id);
        } catch (e) { console.error("Failed to restart", e); }
    });

    btnApproveDag.addEventListener('click', async () => {
        if (!activeThreadId) return;
        btnApproveDag.textContent = 'Approving...';
        try {
            await fetch(`${API_BASE}/api/threads/${activeThreadId}/approve_dag`, { method: 'POST' });
            btnApproveDag.style.display = 'none';
            btnApproveDag.textContent = '✅ Approve & Dispatch';
            pollThreadStatus();
        } catch (e) {
            console.error("Failed to approve DAG", e);
            btnApproveDag.textContent = '✅ Approve & Dispatch';
        }
    });

    prioritySelect.addEventListener('change', async (e) => {
        if (!activeThreadId) return;
        try {
            await fetch(`${API_BASE}/api/threads/${activeThreadId}/priority`, {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ priority: parseInt(e.target.value) })
            });
        } catch (e) { console.error("Failed to set priority", e); }
    });

    copyResultBtn.addEventListener('click', () => {
        const text = finalAnswerBody.innerText;
        navigator.clipboard.writeText(text).then(() => {
            copyResultBtn.textContent = '✅ Copied!';
            setTimeout(() => { copyResultBtn.textContent = '📋 Copy'; }, 2000);
        });
    });

    // Sidebar Resizing
    let isResizing = false;
    sidebarResizer.addEventListener('mousedown', (e) => {
        isResizing = true; sidebarResizer.classList.add('active');
        document.body.style.cursor = 'col-resize'; document.body.style.userSelect = 'none';
        e.preventDefault();
    });
    document.addEventListener('mousemove', (e) => {
        if (!isResizing) return;
        let w = e.clientX; if (w < 250) w = 250; if (w > 800) w = 800;
        sidebar.style.width = w + 'px';
    });
    document.addEventListener('mouseup', () => {
        if (isResizing) { isResizing = false; sidebarResizer.classList.remove('active'); document.body.style.cursor = ''; document.body.style.userSelect = ''; }
    });

    // Log panel
    logHeader.onclick = (e) => {
        if (e.target.closest('.log-control-btn')) return;
        logPanel.classList.toggle('minimized');
        toggleLogPanelBtn.textContent = logPanel.classList.contains('minimized') ? '⌃' : '⌄';
    };
    toggleLogPanelBtn.onclick = () => {
        logPanel.classList.toggle('minimized');
        toggleLogPanelBtn.textContent = logPanel.classList.contains('minimized') ? '⌃' : '⌄';
    };
    clearLogsBtn.onclick = () => { logBody.innerHTML = ''; addLogEntry('System', '[CLEARED]', 'Log buffer cleared.'); };
}

// ===================== THREADS =====================
async function fetchThreads() {
    try {
        const res = await fetch(`${API_BASE}/api/threads`);
        const threads = await res.json();
        threadList.innerHTML = '';
        threads.forEach(t => {
            const li = document.createElement('li');
            const isSystem = t.thread_id.startsWith('SYSTEM_EVO_') || t.prompt.startsWith('APPLY IMPROVEMENT:');
            li.className = `thread-item ${t.thread_id === activeThreadId ? 'active' : ''} ${isSystem ? 'thread-item-system' : ''}`;
            const date = new Date(t.created_at).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});
            li.innerHTML = `
                <div class="thread-item-main" onclick="selectThread('${t.thread_id}')">
                    <div class="thread-item-prompt">${t.prompt}</div>
                    <div class="thread-item-time">${date}</div>
                </div>
                <button class="thread-delete-btn" title="Delete job">
                    <span style="pointer-events: none;">🗑️</span>
                </button>
            `;
            li.querySelector('.thread-delete-btn').onclick = (e) => { e.stopPropagation(); deleteThread(t.thread_id); };
            threadList.appendChild(li);
        });
    } catch (e) { console.error("Failed to fetch threads:", e); }
}

async function deleteThread(threadId) {
    if (!confirm("Permanently delete this job and all its data?")) return;
    try {
        const res = await fetch(`${API_BASE}/api/threads/${threadId}`, { method: 'DELETE' });
        if (res.ok) {
            if (activeThreadId === threadId) {
                activeThreadId = null;
                nodesContainer.querySelectorAll('.dag-node').forEach(n => n.remove());
                svgCanvas.innerHTML = '';
                nodesMap = {};
                currentThreadIdDisplay.textContent = 'Select a job to begin';
                updateHeaderStatus('idle', null);
                emptyState.style.display = 'flex';
                finalAnswerPanel.style.display = 'none';
                taskDrawer.classList.remove('open');
                pipelineProgress.style.display = 'none';
                threadControls.style.display = 'none';
            }
            fetchThreads();
        } else { alert("Failed to delete thread."); }
    } catch (e) { console.error("Error deleting thread:", e); alert("Error communicating with server."); }
}

function selectThread(threadId) {
    if (activeThreadId === threadId) return;
    activeThreadId = threadId;
    currentThreadIdDisplay.textContent = threadId.substring(0, 8) + '...';
    emptyState.style.display = 'none';
    finalAnswerPanel.style.display = 'none';
    threadControls.style.display = 'flex';
    pipelineProgress.style.display = 'flex';
    prioritySelect.value = "0";
    taskDrawer.classList.remove('open');
    if (logPollInterval) { clearInterval(logPollInterval); logPollInterval = null; }
    nodesContainer.querySelectorAll('.dag-node').forEach(n => n.remove());
    svgCanvas.innerHTML = '';
    nodesMap = {};
    currentGraphState = null;
    fetchThreads();
    if (pollInterval) clearInterval(pollInterval);
    pollThreadStatus();
    pollInterval = setInterval(pollThreadStatus, 1500);
}

// ===================== POLLING =====================
async function pollThreadStatus() {
    if (!activeThreadId) return;
    try {
        const res = await fetch(`${API_BASE}/api/status/${activeThreadId}`);
        if (!res.ok) return;
        const state = await res.json();
        currentGraphState = state;
        updateHeaderStatus(state.status, state.error);
        updatePipeline(state.status, state.task_list);
        if (state.priority !== undefined && prioritySelect.value !== state.priority.toString()) {
            prioritySelect.value = state.priority.toString();
        }
        renderGraph(state.task_list, state.completed_results);
        
        // Show final answer when finished
        if (state.status === 'finished' || state.status === 'completed') {
            showFinalAnswer(state.task_list, state.completed_results);
        }
    } catch (e) { console.error("Polling error", e); }
}

async function pollTelemetry() {
    try {
        const res = await fetch(`${API_BASE}/api/telemetry`);
        if (!res.ok) return;
        const state = await res.json();
        const count = state.active_workers ? state.active_workers.length : 0;
        activeWorkersCount.textContent = count.toString();
        if (workersModal.classList.contains('active')) renderWorkerDetails(state.active_workers);
    } catch (e) { console.error("Telemetry error", e); }
}

// ===================== PIPELINE PROGRESS =====================
function updatePipeline(status, taskList) {
    const steps = pipelineProgress.querySelectorAll('.pipeline-step');
    const statusOrder = ['planning', 'pending_approval', 'dispatching', 'dispatched', 'processing', 'sleeping', 'aggregating', 'finished', 'completed'];
    
    const stageMap = {
        'planning': 'planning', 'pending_approval': 'planning',
        'dispatching': 'dispatching', 'dispatched': 'dispatching',
        'processing': 'processing', 'sleeping': 'processing',
        'aggregating': 'aggregating', 'reflecting': 'aggregating',
        'evolving': 'evolving',
        'finished': 'finished', 'completed': 'finished'
    };
    const currentStage = stageMap[status] || 'planning';
    const stageOrder = ['planning', 'dispatching', 'processing', 'aggregating', 'evolving', 'finished'];
    const currentIdx = stageOrder.indexOf(currentStage);

    steps.forEach(step => {
        const stepName = step.dataset.step;
        const stepIdx = stageOrder.indexOf(stepName);
        step.classList.remove('active', 'completed');
        if (stepIdx < currentIdx) step.classList.add('completed');
        else if (stepIdx === currentIdx) step.classList.add('active');
    });
}

// ===================== FINAL ANSWER =====================
function showFinalAnswer(taskList, completedResults) {
    if (!taskList || taskList.length === 0) return;
    
    // Collect completed user task results (not system tasks)
    let finalText = '';
    const userTasks = taskList.filter(t => !t.id.startsWith('SYSTEM_'));
    
    if (userTasks.length === 1) {
        finalText = findTaskResult(userTasks[0].id, completedResults) || '';
    } else {
        userTasks.forEach(t => {
            const result = findTaskResult(t.id, completedResults);
            if (result) {
                finalText += `## ${t.id}: ${t.description}\n\n${result}\n\n---\n\n`;
            }
        });
    }
    
    if (finalText) {
        finalAnswerPanel.style.display = 'block';
        finalAnswerBody.innerHTML = marked.parse(finalText);
    }
}

// ===================== STATUS =====================
function updateHeaderStatus(status, error) {
    let text = status.charAt(0).toUpperCase() + status.slice(1);
    let colorVar = `--status-${status}`;
    
    if (error) { text = "Error"; colorVar = "--status-error"; statusDot.classList.remove('pulsing'); }
    else if (status === 'finished' || status === 'completed' || status === 'paused') { statusDot.classList.remove('pulsing'); }
    else { statusDot.classList.add('pulsing'); }

    if (status === 'pending_approval') {
        text = "Needs Approval";
        btnApproveDag.style.display = 'inline-flex';
        statusDot.style.background = '#00f2fe';
    } else { btnApproveDag.style.display = 'none'; }

    if (status === 'paused') {
        statusDot.style.background = '#ffaa00';
        btnTogglePause.textContent = "▶️ Resume";
        btnTogglePause.classList.add('paused-state');
    } else {
        btnTogglePause.textContent = "⏸️ Pause";
        btnTogglePause.classList.remove('paused-state');
    }

    if (status === 'finished' || status === 'completed') {
        text = "✅ Completed";
        statusDot.style.background = 'var(--status-completed)';
    } else if (status === 'dispatching' || status === 'dispatched') {
        text = "📡 Waiting for Workers...";
        statusDot.style.background = 'var(--status-dispatching)';
    } else if (status !== 'paused' && status !== 'pending_approval') {
        statusDot.style.background = `var(${colorVar})`;
    }
    currentStatusText.textContent = text;
}

// ===================== GRAPH RENDERING =====================
function calculateLayout(tasks) {
    const depths = {};
    function getDepth(taskId, visited = new Set()) {
        if (depths[taskId] !== undefined) return depths[taskId];
        if (visited.has(taskId)) return 0;
        const task = tasks.find(t => t.id === taskId);
        if (!task || !task.dependencies || task.dependencies.length === 0) { depths[taskId] = 0; return 0; }
        visited.add(taskId);
        let max = -1;
        task.dependencies.forEach(depId => { const d = getDepth(depId, new Set(visited)); if (d > max) max = d; });
        depths[taskId] = max + 1;
        return max + 1;
    }
    tasks.forEach(t => getDepth(t.id));
    const nodesByDepth = {};
    for (const [id, d] of Object.entries(depths)) { if (!nodesByDepth[d]) nodesByDepth[d] = []; nodesByDepth[d].push(id); }
    const layout = {};
    Object.keys(nodesByDepth).forEach(depthStr => {
        const d = parseInt(depthStr);
        const taskIds = nodesByDepth[d];
        taskIds.forEach((id, index) => {
            layout[id] = { x: 50 + (d * 350), y: 100 + (index * 180) };
        });
    });
    return layout;
}

function renderGraph(tasks, completedResults) {
    if (!tasks || tasks.length === 0) return;
    if (Object.keys(nodesMap).length === 0) {
        const layout = calculateLayout(tasks);
        tasks.forEach(task => {
            const clone = nodeTemplate.content.cloneNode(true);
            const nodeEl = clone.querySelector('.dag-node');
            nodeEl.dataset.id = task.id;
            nodeEl.querySelector('.node-id').textContent = task.id;
            nodeEl.querySelector('.node-body').textContent = task.description;
            if (layout[task.id]) { nodeEl.style.left = `${layout[task.id].x}px`; nodeEl.style.top = `${layout[task.id].y}px`; }
            nodesContainer.appendChild(nodeEl);
            nodesMap[task.id] = nodeEl;
            
            // Per-task restart
            const restartBtn = nodeEl.querySelector('.node-restart-btn');
            if (restartBtn) {
                restartBtn.onclick = (e) => {
                    e.stopPropagation();
                    restartTask(task.id);
                };
            }
        });
        tasks.forEach(task => {
            if (task.dependencies) {
                task.dependencies.forEach(depId => {
                    if (layout[depId] && layout[task.id]) drawSvgLine(depId, task.id, layout);
                });
            }
        });
    }
    tasks.forEach(task => {
        const el = nodesMap[task.id];
        if (!el) return;
        let vs = task.status || 'pending';
        const directResult = completedResults[task.id];
        if (directResult) vs = 'completed';
        el.className = `dag-node node-${vs}`;
        el.querySelector('.node-badge').textContent = vs;
        const workerEl = el.querySelector('.node-worker');
        if (workerEl) workerEl.textContent = task.assigned_worker_id ? `⚙️ ${task.assigned_worker_id}` : '';
        
        const attemptsEl = el.querySelector('.node-attempts');
        if (attemptsEl) {
            attemptsEl.textContent = task.attempts > 0 ? `🔄 ${task.attempts + 1}` : '';
            attemptsEl.style.display = task.attempts > 0 ? 'inline' : 'none';
        }
        
        if (vs === 'processing' || vs === 'dispatched') {
            if (task.dependencies) task.dependencies.forEach(depId => { const line = document.getElementById(`line-${depId}-${task.id}`); if (line) line.classList.add('active'); });
        } else if (vs === 'completed') {
            if (task.dependencies) task.dependencies.forEach(depId => { const line = document.getElementById(`line-${depId}-${task.id}`); if (line) line.classList.remove('active'); });
        }
        // Resolve the best available result for this task
        const resolvedResult = findTaskResult(task.id, completedResults);
        if (taskDrawer.classList.contains('open') && drawerTaskId.textContent === task.id) updateDrawer(task, vs, resolvedResult);
        el.onclick = () => { updateDrawer(task, vs, resolvedResult); taskDrawer.classList.add('open'); };
    });
}

// Find the best available result for a task, checking parent ID, replicas, and aggregator
function findTaskResult(taskId, completedResults) {
    // Direct result (parent task ID)
    if (completedResults[taskId]) return completedResults[taskId];
    // Aggregator result
    const aggKey = `SYSTEM_AGGREGATOR_${taskId}`;
    if (completedResults[aggKey]) return completedResults[aggKey];
    // Fallback to first replica
    const rep1Key = `${taskId}_rep1`;
    if (completedResults[rep1Key]) return completedResults[rep1Key];
    return null;
}

function updateDrawer(task, status, result) {
    const isNewTask = drawerTaskId.textContent !== task.id;
    const shouldPollLogs = ['processing', 'queued', 'dispatched'].includes(status);
    drawerTaskId.textContent = task.id;
    drawerStatusBadge.textContent = status;
    drawerStatusBadge.className = `node-badge ${status}`;
    drawerStatusBadge.style.color = `var(--status-${status})`;
    drawerDescription.textContent = task.description;
    drawerWorkerId.textContent = task.assigned_worker_id || "None";
    
    if (task.attempts > 0) {
        drawerAttempts.style.display = 'inline-block';
        drawerAttempts.textContent = `Attempt ${task.attempts + 1}`;
    } else {
        drawerAttempts.style.display = 'none';
    }
    if (logPollInterval && (isNewTask || !shouldPollLogs)) { clearInterval(logPollInterval); logPollInterval = null; }
    if (result) {
        drawerResult.classList.add('markdown-output');
        drawerResult.innerHTML = marked.parse(typeof result === 'string' ? result : JSON.stringify(result, null, 2));
    } else if (status === 'completed') {
        drawerResult.classList.remove('markdown-output');
        drawerResult.textContent = "Task completed. Result is being finalized...";
    } else {
        drawerResult.classList.remove('markdown-output');
        if (isNewTask) drawerResult.textContent = "Waiting for worker to begin...\n";
        if (shouldPollLogs && !logPollInterval) startLogPolling(task.id);
    }
}

function drawSvgLine(fromId, toId, layout) {
    const fromX = layout[fromId].x + 260, fromY = layout[fromId].y + 60;
    const toX = layout[toId].x, toY = layout[toId].y + 60;
    const cp1x = fromX + (toX - fromX) / 2;
    const path = `M ${fromX} ${fromY} C ${cp1x} ${fromY}, ${cp1x} ${toY}, ${toX} ${toY}`;
    const pathEl = document.createElementNS("http://www.w3.org/2000/svg", "path");
    pathEl.setAttribute("d", path); pathEl.setAttribute("id", `line-${fromId}-${toId}`); pathEl.setAttribute("class", "dag-line");
    svgCanvas.appendChild(pathEl);
}

async function restartTask(taskId) {
    if (!confirm(`Restart task ${taskId}?`)) return;
    const el = nodesMap[taskId];
    if (el) el.classList.add('node-restarting');
    
    try {
        const res = await fetch(`${API_BASE}/api/tasks/${taskId}/restart`, { method: 'POST' });
        if (res.ok) {
            addLogEntry('System', 'RESTART', `Task ${taskId} reset to pending.`);
            pollThreadStatus();
        } else {
            alert("Failed to restart task.");
            if (el) el.classList.remove('node-restarting');
        }
    } catch (e) {
        console.error("Error restarting task:", e);
        if (el) el.classList.remove('node-restarting');
    }
}

// ===================== LOG PANEL =====================
function addLogEntry(tag, status, message) {
    const entry = document.createElement('div');
    entry.className = 'log-entry';
    const now = new Date();
    const timeStr = now.toLocaleTimeString([], { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
    entry.innerHTML = `<span class="log-time">${timeStr}</span><span class="log-tag">[${tag}]</span><span class="log-msg">${message}</span>`;
    logBody.appendChild(entry);
    logBody.scrollTop = logBody.scrollHeight;
    if (logBody.children.length > 200) logBody.removeChild(logBody.firstChild);
}

async function pollGlobalLogs() {
    try {
        const url = `${API_BASE}/api/logs/all${lastLogTimestamp ? `?since=${lastLogTimestamp}` : ''}`;
        const res = await fetch(url);
        if (!res.ok) return;
        const data = await res.json();
        if (data.logs && data.logs.length > 0) {
            data.logs.forEach(log => {
                let tag = log.task_id ? log.task_id.substring(0, 12) : 'System';
                if (log.message.trim()) addLogEntry(tag, '', log.message.trim());
                lastLogTimestamp = log.created_at;
            });
        }
    } catch (e) { console.error("Global log poll failed:", e); }
}
setInterval(pollGlobalLogs, 2000);

function startLogPolling(taskId) { fetchAndDisplayLogs(taskId); }

async function fetchAndDisplayLogs(taskId) {
    if (!taskId) return;
    try {
        const res = await fetch(`${API_BASE}/api/logs/${taskId}${activeThreadId ? `?thread_id=${activeThreadId}` : ''}`);
        if (!res.ok) return;
        const data = await res.json();
        if (data.logs && data.logs.length > 0) {
            let text = ""; data.logs.forEach(l => { text += l.message; });
            drawerResult.textContent = text;
            const container = document.getElementById('terminalContainer');
            if (container) container.scrollTop = container.scrollHeight;
        }
    } catch (e) { console.error("Failed fetching live logs:", e); }
}

// ===================== MODALS =====================
async function fetchImprovements() {
    improvementsList.innerHTML = '<div class="loading-state">Loading...</div>';
    try {
        const res = await fetch(`${API_BASE}/api/improvements/all`);
        const imps = await res.json();
        if (!imps || imps.length === 0) { improvementsList.innerHTML = '<div class="loading-state">No improvements yet. Keep running jobs!</div>'; return; }
        improvementsList.innerHTML = '';
        imps.forEach(imp => {
            const div = document.createElement('div');
            div.className = `improvement-card ${imp.status || 'pending'}`;
            div.innerHTML = `
                <div class="improvement-header">
                    <div style="display:flex; align-items:center; gap:12px;">
                        <div class="vote-badge">${imp.votes} Votes</div>
                        <div class="status-badge status-${imp.status || 'pending'}">${(imp.status || 'pending').toUpperCase()}</div>
                    </div>
                    <div style="display:flex; gap:10px; align-items:center;">
                        ${imp.status !== 'applied' ? `<button class="icon-btn" onclick="applyImprovement(${imp.id})" title="Apply this improvement" style="color:var(--status-completed); border-color:rgba(10,200,100,0.3); padding: 6px 14px;">✅ Apply</button>` : ''}
                        <button class="thread-delete-btn" onclick="deleteImprovement(${imp.id})" title="Dismiss improvement" style="padding: 6px;">🗑️</button>
                    </div>
                </div>
                <div class="improvement-desc markdown-output" style="margin-top: 10px;">${marked.parse(imp.description || '')}</div>
                ${imp.patch_data ? `<div class="patch-container" style="margin-top: 15px;"><div class="patch-header">Proposed Patch</div><pre class="patch-content">${imp.patch_data}</pre></div>` : ''}
            `;
            improvementsList.appendChild(div);
        });
    } catch (e) { improvementsList.innerHTML = '<div class="loading-state" style="color:var(--status-error);">Error loading.</div>'; }
}

async function applyImprovement(id) {
    if (!confirm("This will trigger a worker to autonomously apply this improvement to the system. Proceed?")) return;
    try {
        const res = await fetch(`${API_BASE}/api/improvements/${id}/apply`, { method: 'POST' });
        if (res.ok) {
            const data = await res.json();
            addLogEntry('System', 'APPLY', `Improvement application started in thread ${data.thread_id}`);
            improvementsModal.classList.remove('active');
            // Navigate to the new thread to watch progress
            loadThread(data.thread_id);
        } else {
            alert("Failed to start application.");
        }
    } catch (e) { console.error("Failed to apply improvement", e); }
}

async function deleteImprovement(id) {
    if (!confirm("Remove this improvement from the ledger?")) return;
    try {
        const res = await fetch(`${API_BASE}/api/improvements/${id}`, { method: 'DELETE' });
        if (res.ok) fetchImprovements();
    } catch (e) { console.error("Failed to delete improvement", e); }
}

async function clearAllImprovements() {
    if (!confirm("Are you sure you want to clear ALL proposed improvements?")) return;
    try {
        const res = await fetch(`${API_BASE}/api/improvements`, { method: 'DELETE' });
        if (res.ok) fetchImprovements();
    } catch (e) { console.error("Failed to clear improvements", e); }
}

async function fetchWorkerDetails() {
    workersList.innerHTML = '<div class="loading-state">Scanning...</div>';
    try {
        const res = await fetch(`${API_BASE}/api/telemetry`);
        const data = await res.json();
        renderWorkerDetails(data.active_workers);
    } catch (e) { workersList.innerHTML = '<div class="loading-state" style="color:var(--status-error);">Error.</div>'; }
}

function renderWorkerDetails(workers) {
    if (!workers || workers.length === 0) { workersList.innerHTML = '<div class="loading-state">No workers detected. Start a worker to begin.</div>'; return; }
    workersList.innerHTML = '';
    workers.forEach(w => {
        const div = document.createElement('div');
        div.className = 'worker-card';
        div.innerHTML = `
            <div class="worker-id-container">
                <div class="worker-id">Worker # ${w.id}</div>
                <div class="worker-model">${w.model || "Unknown"}</div>
            </div>
            <div style="text-align: right;">
                <div class="worker-status-badge">ACTIVE</div>
            </div>
        `;
        workersList.appendChild(div);
    });
}

// Start
init();
