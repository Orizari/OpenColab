// Mission Control Logic

const API_BASE = 'http://localhost:8000';
let activeThreadId = null;
let pollInterval = null;
let logPollInterval = null;
let nodesMap = {}; // id -> DOM element
let currentGraphState = null;

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

// Resizer Elements
const sidebar = document.getElementById('sidebar');
const sidebarResizer = document.getElementById('sidebarResizer');

// Thread Controls
const threadControls = document.getElementById('threadControls');
const btnApproveDag = document.getElementById('btnApproveDag');
const btnTogglePause = document.getElementById('btnTogglePause');
const btnRestart = document.getElementById('btnRestart');
const prioritySelect = document.getElementById('prioritySelect');

// Drawer fields
const drawerTaskId = document.getElementById('drawerTaskId');
const drawerStatusBadge = document.getElementById('drawerStatusBadge');
const drawerWorkerId = document.getElementById('drawerWorkerId');
const drawerDescription = document.getElementById('drawerDescription');
const drawerResult = document.getElementById('drawerResult');

// App Initialization
async function init() {
    await fetchThreads();
    pollTelemetry();
    setInterval(pollTelemetry, 3000); // Check for workers every 3s

    closeDrawerBtn.addEventListener('click', () => {
        taskDrawer.classList.remove('open');
        if (logPollInterval) {
            clearInterval(logPollInterval);
            logPollInterval = null;
        }
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
                    // Append parallel paths array. webkitRelativePath contains the full path from the selected folder root.
                    // Fallback to file.name if it's a direct file selection.
                    formData.append('paths', file.webkitRelativePath || file.name);
                }
            }

            const res = await fetch(`${API_BASE}/submit`, {
                method: 'POST',
                body: formData
            });
            const data = await res.json();
            promptInput.value = '';
            imageInput.value = '';

            // Instantly jump to new thread
            await fetchThreads();
            selectThread(data.thread_id);

        } catch (err) {
            console.error("Failed to launch job:", err);
            alert("Error launching job. Is the backend running?");
        } finally {
            btnText.textContent = "Launch Job";
        }
    });

    // Thread Control Listeners
    btnTogglePause.addEventListener('click', async () => {
        if (!activeThreadId) return;
        const isPaused = currentStatusText.textContent.toLowerCase() === 'paused';
        const endpoint = isPaused ? 'resume' : 'pause';

        try {
            await fetch(`${API_BASE}/api/threads/${activeThreadId}/${endpoint}`, { method: 'POST' });
            // Immediate optimistic UI update (polling will correct it if wrong)
            updateHeaderStatus(isPaused ? 'resuming' : 'paused', null);
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
        const priority = parseInt(e.target.value);
        try {
            await fetch(`${API_BASE}/api/threads/${activeThreadId}/priority`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ priority })
            });
        } catch (e) { console.error("Failed to set priority", e); }
    });

    // Sidebar Resizing Logic
    let isResizing = false;

    sidebarResizer.addEventListener('mousedown', (e) => {
        isResizing = true;
        sidebarResizer.classList.add('active');
        document.body.style.cursor = 'col-resize';
        document.body.style.userSelect = 'none'; // Prevent text highlighting break
        e.preventDefault();
    });

    document.addEventListener('mousemove', (e) => {
        if (!isResizing) return;

        let newWidth = e.clientX;
        // Clamp the width to sane values
        if (newWidth < 250) newWidth = 250;
        if (newWidth > 800) newWidth = 800;

        sidebar.style.width = newWidth + 'px';
    });

    document.addEventListener('mouseup', () => {
        if (isResizing) {
            isResizing = false;
            sidebarResizer.classList.remove('active');
            document.body.style.cursor = '';
            document.body.style.userSelect = '';
        }
    });
}

// Fetch Thread History
async function fetchThreads() {
    try {
        const res = await fetch(`${API_BASE}/api/threads`);
        const threads = await res.json();

        threadList.innerHTML = '';
        threads.forEach(t => {
            const li = document.createElement('li');
            li.className = `thread-item ${t.thread_id === activeThreadId ? 'active' : ''}`;
            li.onclick = () => selectThread(t.thread_id);

            const date = new Date(t.created_at).toLocaleTimeString();
            li.innerHTML = `
                <div class="thread-item-prompt">${t.prompt}</div>
                <div class="thread-item-time">${date}</div>
            `;
            threadList.appendChild(li);
        });
    } catch (e) {
        console.error("Failed fetching threads", e);
    }
}

// Select a Thread to monitor
function selectThread(threadId) {
    if (activeThreadId === threadId) return;

    activeThreadId = threadId;
    currentThreadIdDisplay.textContent = threadId;
    emptyState.style.display = 'none';
    threadControls.style.display = 'flex';

    // Reset priority to normal initially for new view
    prioritySelect.value = "0";

    // Cleanup drawer and log polling
    taskDrawer.classList.remove('open');
    if (logPollInterval) {
        clearInterval(logPollInterval);
        logPollInterval = null;
    }

    // Check if we need to clear current nodes
    const existingNodes = nodesContainer.querySelectorAll('.dag-node');
    existingNodes.forEach(n => n.remove());
    svgCanvas.innerHTML = '';

    nodesMap = {};
    currentGraphState = null;

    // Highlight sidebar
    fetchThreads();

    // Start Polling
    if (pollInterval) clearInterval(pollInterval);
    pollThreadStatus();
    pollInterval = setInterval(pollThreadStatus, 1000);
}

// Poll the API for graph state
async function pollThreadStatus() {
    if (!activeThreadId) return;

    try {
        const res = await fetch(`${API_BASE}/api/status/${activeThreadId}`);
        if (!res.ok) return;
        const state = await res.json();

        // Update header status
        updateHeaderStatus(state.status, state.error);

        // Synchronize priority dropdown
        if (state.priority !== undefined && prioritySelect.value !== state.priority.toString()) {
            prioritySelect.value = state.priority.toString();
        }

        // Render DAG
        renderGraph(state.task_list, state.completed_results);

    } catch (e) {
        console.error("Polling error", e);
    }
}

async function pollTelemetry() {
    try {
        const res = await fetch(`${API_BASE}/api/telemetry`);
        if (!res.ok) return;
        const state = await res.json();

        const count = state.active_workers ? state.active_workers.length : 0;
        activeWorkersCount.textContent = count.toString();

    } catch (e) {
        console.error("Telemetry error", e);
    }
}

function updateHeaderStatus(status, error) {
    let text = status.charAt(0).toUpperCase() + status.slice(1);
    let colorVar = `--status-${status}`;

    if (error) {
        text = "Error";
        colorVar = "--status-error";
        statusDot.classList.remove('pulsing');
    } else if (status === 'completed' || status === 'paused') {
        statusDot.classList.remove('pulsing');
    } else {
        statusDot.classList.add('pulsing');
    }

    if (status === 'pending_approval') {
        text = "Needs Approval";
        btnApproveDag.style.display = 'inline-flex'; // show the approve button
        statusDot.style.background = '#00f2fe'; // custom cyan for waiting
    } else {
        btnApproveDag.style.display = 'none';
    }

    // Special coloring for Paused state if it doesn't exist in root map
    if (status === 'paused') {
        statusDot.style.background = '#ffaa00'; // Amber
        btnTogglePause.textContent = "▶️ Resume";
        btnTogglePause.classList.add('paused-state');
    } else {
        btnTogglePause.textContent = "⏸️ Pause";
        btnTogglePause.classList.remove('paused-state');
    }

    if (status !== 'paused' && status !== 'pending_approval') {
        statusDot.style.background = `var(${colorVar})`;
    }

    currentStatusText.textContent = text;
}

// Calculate DAG layout (hierarchical tree)
function calculateLayout(tasks) {
    // 1. Calculate depths
    const depths = {};

    function getDepth(taskId, visited = new Set()) {
        if (depths[taskId] !== undefined) return depths[taskId];
        if (visited.has(taskId)) {
            console.warn("Circular dependency detected by UI for task:", taskId);
            return 0; // Prevent infinite loop crash
        }

        const task = tasks.find(t => t.id === taskId);
        if (!task || !task.dependencies || task.dependencies.length === 0) {
            depths[taskId] = 0;
            return 0;
        }

        visited.add(taskId);

        let maxDepDepth = -1;
        task.dependencies.forEach(depId => {
            const d = getDepth(depId, new Set(visited));
            if (d > maxDepDepth) maxDepDepth = d;
        });

        const myDepth = maxDepDepth + 1;
        depths[taskId] = myDepth;
        return myDepth;
    }

    tasks.forEach(t => getDepth(t.id));

    // 2. Assign X/Y coordinates
    const nodesByDepth = {};
    for (const [id, d] of Object.entries(depths)) {
        if (!nodesByDepth[d]) nodesByDepth[d] = [];
        nodesByDepth[d].push(id);
    }

    const layout = {};
    const X_SPACING = 350;
    const Y_SPACING = 180;
    const START_X = 50;
    const START_Y = 100;

    Object.keys(nodesByDepth).forEach(depthStr => {
        const d = parseInt(depthStr);
        const taskIds = nodesByDepth[d];

        taskIds.forEach((id, index) => {
            layout[id] = {
                x: START_X + (d * X_SPACING),
                y: START_Y + (index * Y_SPACING)
            };
        });
    });

    return layout;
}

function renderGraph(tasks, completedResults) {
    if (!tasks || tasks.length === 0) return;

    // First time render layout
    if (Object.keys(nodesMap).length === 0) {
        const layout = calculateLayout(tasks);

        // Create HTML nodes
        tasks.forEach(task => {
            const clone = nodeTemplate.content.cloneNode(true);
            const nodeEl = clone.querySelector('.dag-node');

            nodeEl.dataset.id = task.id;
            nodeEl.querySelector('.node-id').textContent = task.id;
            nodeEl.querySelector('.node-body').textContent = task.description;

            // Apply bounds
            if (layout[task.id]) {
                nodeEl.style.left = `${layout[task.id].x}px`;
                nodeEl.style.top = `${layout[task.id].y}px`;
            }

            nodesContainer.appendChild(nodeEl);
            nodesMap[task.id] = nodeEl;
        });

        // Draw SVG Lines
        tasks.forEach(task => {
            if (task.dependencies) {
                task.dependencies.forEach(depId => {
                    if (layout[depId] && layout[task.id]) {
                        drawSvgLine(depId, task.id, layout);
                    }
                });
            }
        });
    }

    // Unconditionally update statuses to sync with state machine
    tasks.forEach(task => {
        const el = nodesMap[task.id];
        if (!el) return;

        let visualStatus = task.status || 'pending';

        // Overrides: The local state graph might only say 'pending' or 'dispatched'.
        // If it exists in completedResults, it's definitively complete.
        if (completedResults[task.id]) {
            visualStatus = 'completed';
        }

        el.className = `dag-node node-${visualStatus}`;
        el.querySelector('.node-badge').textContent = visualStatus;

        const workerEl = el.querySelector('.node-worker');
        if (workerEl) {
            workerEl.textContent = task.assigned_worker_id ? `⚙️ ${task.assigned_worker_id}` : '';
        }

        // Highlight active paths
        if (visualStatus === 'processing' || visualStatus === 'dispatched') {
            if (task.dependencies) {
                task.dependencies.forEach(depId => {
                    const line = document.getElementById(`line-${depId}-${task.id}`);
                    if (line) line.classList.add('active');
                });
            }
        } else if (visualStatus === 'completed') {
            if (task.dependencies) {
                task.dependencies.forEach(depId => {
                    const line = document.getElementById(`line-${depId}-${task.id}`);
                    if (line) line.classList.remove('active');
                });
            }
        }

        // Update Drawer if it's currently open for this node
        if (taskDrawer.classList.contains('open') && drawerTaskId.textContent === task.id) {
            updateDrawer(task, visualStatus, completedResults[task.id]);
        }

        // Add click handler
        el.onclick = () => {
            updateDrawer(task, visualStatus, completedResults[task.id]);
            taskDrawer.classList.add('open');
        };
    });
}
function updateDrawer(task, status, result) {
    const isNewTask = drawerTaskId.textContent !== task.id;
    const isNowProcessing = status === 'processing';

    drawerTaskId.textContent = task.id;
    drawerStatusBadge.textContent = status;
    drawerStatusBadge.className = `node-badge ${status}`;
    drawerStatusBadge.style.color = `var(--status-${status})`;

    drawerDescription.textContent = task.description;

    if (task.assigned_worker_id) {
        drawerWorkerId.textContent = task.assigned_worker_id;
    } else {
        drawerWorkerId.textContent = "None";
    }

    // Clear previous log polling ONLY if switching tasks or no longer processing
    if (logPollInterval && (isNewTask || !isNowProcessing)) {
        clearInterval(logPollInterval);
        logPollInterval = null;
    }

    if (result) {
        // Render as Markdown
        drawerResult.classList.add('markdown-output');
        drawerResult.innerHTML = marked.parse(typeof result === 'string' ? result : JSON.stringify(result, null, 2));
    } else if (isNowProcessing) {
        drawerResult.classList.remove('markdown-output');
        if (isNewTask || drawerResult.textContent === "Awaiting execution...") {
            drawerResult.textContent = "Fetching live execution logs...\n";
        }
        // Only start polling if we aren't already
        if (!logPollInterval) {
            startLogPolling(task.id);
        }
        drawerResult.classList.remove('markdown-output');
        drawerResult.textContent = "Awaiting execution...";
    }
}

function startLogPolling(taskId) {
    fetchAndDisplayLogs(taskId);
    logPollInterval = setInterval(() => fetchAndDisplayLogs(taskId), 1000);
}

async function fetchAndDisplayLogs(taskId) {
    try {
        const res = await fetch(`${API_BASE}/api/logs/${taskId}`);
        if (!res.ok) return;
        const data = await res.json();

        if (data.logs && data.logs.length > 0) {
            let text = "";
            data.logs.forEach(l => {
                text += l.message; // Backend preserves line breaks and stdout spacing
            });
            drawerResult.textContent = text;

            // Auto scroll terminal container to bottom
            const container = document.getElementById('terminalContainer');
            if (container) {
                container.scrollTop = container.scrollHeight;
            }
        }
    } catch (e) {
        console.error("Failed fetching live logs:", e);
    }
}

function drawSvgLine(fromId, toId, layout) {
    const fromX = layout[fromId].x + 260; // right edge of node
    const fromY = layout[fromId].y + 60;  // middle of node
    const toX = layout[toId].x;           // left edge of node
    const toY = layout[toId].y + 60;      // middle of node

    // Bezier curve
    const cp1x = fromX + (toX - fromX) / 2;
    const path = `M ${fromX} ${fromY} C ${cp1x} ${fromY}, ${cp1x} ${toY}, ${toX} ${toY}`;

    const pathEl = document.createElementNS("http://www.w3.org/2000/svg", "path");
    pathEl.setAttribute("d", path);
    pathEl.setAttribute("id", `line-${fromId}-${toId}`);
    pathEl.setAttribute("class", "dag-line");

    svgCanvas.appendChild(pathEl);
}

// Start
init();
