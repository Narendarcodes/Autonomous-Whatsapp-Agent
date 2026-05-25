"""
Real-time Logs Viewer
WebSocket endpoint for streaming logs from all Docker containers
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
import asyncio
from datetime import datetime
from typing import List, Optional, Dict, Any
import json
import subprocess
import re

from app.core.logging import logger
from app.core.config import settings

router = APIRouter()

# Container names to monitor
CONTAINERS = [
    "whatsapp_calendar_backend",
    "whatsapp_calendar_agent_worker", 
    "whatsapp_calendar_scheduler_worker",
    "whatsapp_calendar_db",
    "whatsapp_calendar_redis"
]

# Container display names and colors
CONTAINER_INFO = {
    "whatsapp_calendar_backend": {"name": "backend", "color": "#4ec9b0"},
    "whatsapp_calendar_agent_worker": {"name": "agent", "color": "#dcdcaa"},
    "whatsapp_calendar_scheduler_worker": {"name": "scheduler", "color": "#c586c0"},
    "whatsapp_calendar_db": {"name": "postgres", "color": "#569cd6"},
    "whatsapp_calendar_redis": {"name": "redis", "color": "#ce9178"}
}


class ConnectionManager:
    """Manage WebSocket connections for log streaming"""
    
    def __init__(self):
        self.active_connections: List[WebSocket] = []
    
    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"📺 New log viewer connected (total: {len(self.active_connections)})")
    
    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        logger.info(f"📺 Log viewer disconnected (remaining: {len(self.active_connections)})")
    
    async def broadcast(self, message: dict):
        """Broadcast message to all connected clients"""
        disconnected = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                disconnected.append(connection)
        
        # Remove disconnected clients
        for conn in disconnected:
            if conn in self.active_connections:
                self.active_connections.remove(conn)


manager = ConnectionManager()


def strip_ansi_codes(text: str) -> str:
    """Remove ANSI color/escape codes from text"""
    # Comprehensive pattern to match all ANSI escape sequences
    ansi_pattern = re.compile(r'''
        \x1b\[[0-9;]*[mGKHF]|  # Standard color/cursor codes
        \x1b\[[0-9;]*[ABCDJsu]|  # Cursor movement
        \x1b\][^\x07]*\x07|  # OSC sequences
        \x1b[PX^_].*?\x1b\\|  # String sequences
        \x1b\[[\?]?[0-9;]*[hl]|  # Mode setting
        \[0m|\[32m|\[33m|\[31m|\[34m|\[35m|\[36m|\[37m|  # Bare color codes without escape
        \[0M|\[32M|\[33M|\[31M|\[34M|\[35M|\[36M|\[37M  # Uppercase variants
    ''', re.VERBOSE)
    return ansi_pattern.sub('', text)


def parse_log_line(line: str, container: str) -> Optional[Dict[str, Any]]:
    """Parse a log line and extract level, timestamp, and message"""
    if not line.strip():
        return None
    
    # Strip ANSI codes first
    line = strip_ansi_codes(line)
    
    if not line.strip():
        return None
    
    container_name = CONTAINER_INFO.get(container, {}).get("name", container)
    
    # Common log patterns
    # Pattern 1: 2025-12-01 09:35:33 | INFO     | module:func:line - message
    pattern1 = r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\s*\|\s*(INFO|ERROR|WARNING|DEBUG|WARN)\s*\|.*?[-:]\s*(.+)'
    # Pattern 2: PostgreSQL style: 2025-12-01 09:22:28.902 UTC [363] LOG:  message
    pattern_postgres = r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}[.\d]*\s*\w*)\s*\[\d+\]\s*(LOG|ERROR|WARNING|FATAL|STATEMENT):\s*(.+)'
    # Pattern 3: Redis style: 1:M 01 Dec 2025 09:15:00.826 * message
    pattern_redis = r'\d+:[CMSX]\s+(\d+\s+\w+\s+\d{4}\s+\d{2}:\d{2}:\d{2}[.\d]*)\s*[*#]\s*(.+)'
    # Pattern 4: SQLAlchemy style: 2025-12-01 10:01:57,543 INFO sqlalchemy...
    pattern_sqlalchemy = r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}[,\d]*)\s*(INFO|ERROR|WARNING|DEBUG)\s+(.+)'
    # Pattern 5: Uvicorn style: INFO:     127.0.0.1:51006 - "GET /health HTTP/1.1" 200 OK
    pattern_uvicorn = r'^(INFO|ERROR|WARNING|DEBUG):\s*(.+)'
    
    level = "INFO"
    message = line.strip()
    timestamp = datetime.utcnow().isoformat()
    
    match1 = re.match(pattern1, line, re.IGNORECASE)
    match_pg = re.match(pattern_postgres, line, re.IGNORECASE)
    match_redis = re.match(pattern_redis, line, re.IGNORECASE)
    match_sql = re.match(pattern_sqlalchemy, line, re.IGNORECASE)
    match_uv = re.match(pattern_uvicorn, line, re.IGNORECASE)
    
    if match1:
        timestamp = match1.group(1)
        level = match1.group(2).upper()
        message = match1.group(3)
    elif match_pg:
        timestamp = match_pg.group(1)
        level = match_pg.group(2).upper()
        if level == "STATEMENT":
            level = "DEBUG"
        elif level == "FATAL":
            level = "ERROR"
        elif level == "LOG":
            level = "INFO"
        message = match_pg.group(3)
    elif match_redis:
        timestamp = match_redis.group(1)
        message = match_redis.group(2)
    elif match_sql:
        timestamp = match_sql.group(1)
        level = match_sql.group(2).upper()
        message = match_sql.group(3)
    elif match_uv:
        level = match_uv.group(1).upper()
        message = match_uv.group(2)
    else:
        # Check for error indicators in message
        lower_line = line.lower()
        if 'error' in lower_line or 'exception' in lower_line or 'failed' in lower_line:
            level = "ERROR"
        elif 'warning' in lower_line or 'warn' in lower_line:
            level = "WARNING"
        elif 'debug' in lower_line:
            level = "DEBUG"
    
    # Normalize level
    if level == "WARN":
        level = "WARNING"
    
    return {
        "timestamp": timestamp,
        "level": level,
        "message": message,
        "container": container_name
    }


async def get_container_logs(container: str, lines: int = 50) -> List[Dict[str, Any]]:
    """Get recent logs from a container"""
    try:
        result = subprocess.run(
            ["docker", "logs", container, "--tail", str(lines)],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        logs = []
        output = result.stdout + result.stderr
        
        for line in output.split('\n'):
            parsed = parse_log_line(line, container)
            if parsed:
                logs.append(parsed)
        
        return logs
    except Exception as e:
        logger.error(f"Failed to get logs from {container}: {e}")
        return []


async def stream_container_logs(websocket: WebSocket, container: str):
    """Stream logs from a container using docker logs -f"""
    try:
        process = await asyncio.create_subprocess_exec(
            "docker", "logs", "-f", "--tail", "0", container,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT
        )
        
        while True:
            line = await process.stdout.readline()
            if not line:
                break
            
            decoded = line.decode('utf-8', errors='ignore').strip()
            parsed = parse_log_line(decoded, container)
            
            if parsed:
                try:
                    await websocket.send_json(parsed)
                except:
                    process.terminate()
                    break
                    
    except Exception as e:
        logger.error(f"Error streaming logs from {container}: {e}")


@router.get("/logs/viewer", response_class=HTMLResponse)
async def logs_viewer():
    """
    Real-time log viewer UI - Shows logs from ALL containers
    Access at: http://localhost:8000/logs/viewer
    """
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>WhatsApp Agent - Live Logs (All Containers)</title>
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            
            body {
                font-family: 'Courier New', monospace;
                background: #1e1e1e;
                color: #d4d4d4;
                height: 100vh;
                overflow: hidden;
            }
            
            .header {
                background: #252526;
                padding: 15px 20px;
                border-bottom: 2px solid #007acc;
                display: flex;
                justify-content: space-between;
                align-items: center;
            }
            
            .header h1 {
                color: #007acc;
                font-size: 18px;
                display: flex;
                align-items: center;
                gap: 10px;
            }
            
            .status {
                display: flex;
                align-items: center;
                gap: 10px;
                font-size: 14px;
            }
            
            .status-dot {
                width: 12px;
                height: 12px;
                border-radius: 50%;
                background: #4ec9b0;
                animation: pulse 2s infinite;
            }
            
            .status-dot.disconnected { background: #dc3545; }
            
            @keyframes pulse {
                0%, 100% { opacity: 1; }
                50% { opacity: 0.5; }
            }
            
            .controls {
                padding: 10px 20px;
                background: #2d2d30;
                border-bottom: 1px solid #3e3e42;
                display: flex;
                gap: 10px;
                align-items: center;
                flex-wrap: wrap;
            }
            
            .btn {
                padding: 8px 16px;
                border: none;
                border-radius: 4px;
                cursor: pointer;
                font-family: inherit;
                font-size: 13px;
                transition: all 0.2s;
            }
            
            .btn-clear { background: #dc3545; color: white; }
            .btn-clear:hover { background: #c82333; }
            .btn-pause { background: #ffc107; color: black; }
            .btn-pause:hover { background: #e0a800; }
            .btn-refresh { background: #17a2b8; color: white; }
            .btn-refresh:hover { background: #138496; }
            
            .filter-input {
                padding: 8px 12px;
                border: 1px solid #3e3e42;
                border-radius: 4px;
                background: #1e1e1e;
                color: #d4d4d4;
                font-family: inherit;
                font-size: 13px;
                width: 200px;
            }
            
            .container-filters {
                display: flex;
                gap: 8px;
                margin-left: 10px;
                padding-left: 10px;
                border-left: 1px solid #3e3e42;
            }
            
            .container-toggle {
                padding: 6px 12px;
                border: 2px solid;
                border-radius: 4px;
                cursor: pointer;
                font-size: 12px;
                font-weight: bold;
                transition: all 0.2s;
                background: transparent;
            }
            
            .container-toggle.active { color: white !important; }
            .container-toggle:not(.active) { opacity: 0.4; }
            
            .logs-container {
                height: calc(100vh - 140px);
                overflow-y: auto;
                padding: 10px 20px;
            }
            
            .log-entry {
                padding: 6px 12px;
                margin-bottom: 2px;
                border-left: 3px solid transparent;
                transition: background 0.2s;
                font-size: 12px;
                line-height: 1.5;
                display: flex;
                align-items: flex-start;
                gap: 10px;
            }
            
            .log-entry:hover { background: #2d2d30; }
            .log-entry.new { animation: highlight 1s; }
            
            @keyframes highlight {
                0% { background: #264f78; }
                100% { background: transparent; }
            }
            
            .log-info { border-left-color: #4ec9b0; }
            .log-warning { border-left-color: #ffc107; background: rgba(255, 193, 7, 0.1); }
            .log-error { border-left-color: #dc3545; background: rgba(220, 53, 69, 0.1); }
            .log-debug { border-left-color: #858585; }
            
            .log-timestamp {
                color: #858585;
                min-width: 85px;
                flex-shrink: 0;
            }
            
            .log-container {
                display: inline-block;
                padding: 2px 8px;
                border-radius: 3px;
                font-size: 10px;
                font-weight: bold;
                min-width: 70px;
                text-align: center;
                flex-shrink: 0;
            }
            
            .log-level {
                display: inline-block;
                padding: 2px 6px;
                border-radius: 3px;
                font-size: 10px;
                font-weight: bold;
                min-width: 55px;
                text-align: center;
                flex-shrink: 0;
            }
            
            .level-info { background: #4ec9b0; color: black; }
            .level-warning { background: #ffc107; color: black; }
            .level-error { background: #dc3545; color: white; }
            .level-debug { background: #858585; color: white; }
            
            .log-message {
                color: #d4d4d4;
                word-break: break-word;
                flex: 1;
            }
            
            .empty-state {
                text-align: center;
                padding: 60px 20px;
                color: #858585;
            }
            
            .empty-state h2 { font-size: 48px; margin-bottom: 20px; }
            
            .stats {
                margin-left: auto;
                color: #858585;
                font-size: 12px;
                display: flex;
                gap: 15px;
            }
            
            ::-webkit-scrollbar { width: 10px; }
            ::-webkit-scrollbar-track { background: #1e1e1e; }
            ::-webkit-scrollbar-thumb { background: #424242; border-radius: 5px; }
            ::-webkit-scrollbar-thumb:hover { background: #4e4e4e; }
        </style>
    </head>
    <body>
        <div class="header">
            <h1>📊 WhatsApp AI Agent - Live Logs (All Containers)</h1>
            <div class="status">
                <div class="status-dot" id="statusDot"></div>
                <span id="status">Connecting...</span>
            </div>
        </div>
        
        <div class="controls">
            <button class="btn btn-clear" onclick="clearLogs()">🗑️ Clear</button>
            <button class="btn btn-pause" id="pauseBtn" onclick="togglePause()">⏸️ Pause</button>
            <button class="btn btn-refresh" onclick="loadRecentLogs()">🔄 Load Recent</button>
            <input type="text" class="filter-input" id="filterInput" placeholder="Filter logs..." oninput="filterLogs()">
            
            <div class="container-filters">
                <button class="container-toggle active" id="toggle-backend" 
                    style="border-color: #4ec9b0; color: #4ec9b0; background: #4ec9b0;"
                    onclick="toggleContainer('backend')">Backend</button>
                <button class="container-toggle active" id="toggle-agent"
                    style="border-color: #dcdcaa; color: #dcdcaa; background: #dcdcaa;"
                    onclick="toggleContainer('agent')">Agent</button>
                <button class="container-toggle active" id="toggle-scheduler"
                    style="border-color: #c586c0; color: #c586c0; background: #c586c0;"
                    onclick="toggleContainer('scheduler')">Scheduler</button>
                <button class="container-toggle active" id="toggle-postgres"
                    style="border-color: #569cd6; color: #569cd6; background: #569cd6;"
                    onclick="toggleContainer('postgres')">Postgres</button>
                <button class="container-toggle active" id="toggle-redis"
                    style="border-color: #ce9178; color: #ce9178; background: #ce9178;"
                    onclick="toggleContainer('redis')">Redis</button>
            </div>
            
            <div class="stats">
                <span>Logs: <span id="logCount">0</span></span>
            </div>
        </div>
        
        <div class="logs-container" id="logsContainer">
            <div class="empty-state">
                <h2>📡</h2>
                <p>Connecting to log streams...</p>
                <p style="margin-top: 10px; font-size: 12px;">Logs from all containers will appear here</p>
            </div>
        </div>
        
        <script>
            const logsContainer = document.getElementById('logsContainer');
            const logCount = document.getElementById('logCount');
            const statusText = document.getElementById('status');
            const statusDot = document.getElementById('statusDot');
            const filterInput = document.getElementById('filterInput');
            const pauseBtn = document.getElementById('pauseBtn');
            
            let ws;
            let logs = [];
            let isPaused = false;
            let autoScroll = true;
            
            const containerVisible = {
                backend: true,
                agent: true,
                scheduler: true,
                postgres: true,
                redis: true
            };
            
            const containerColors = {
                backend: '#4ec9b0',
                agent: '#dcdcaa',
                scheduler: '#c586c0',
                postgres: '#569cd6',
                redis: '#ce9178'
            };
            
            function connect() {
                const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
                const wsUrl = `${protocol}//${window.location.host}/ws/logs/all`;
                
                ws = new WebSocket(wsUrl);
                
                ws.onopen = () => {
                    statusText.textContent = 'Connected - Streaming all containers';
                    statusDot.classList.remove('disconnected');
                };
                
                ws.onmessage = (event) => {
                    if (!isPaused) {
                        const data = JSON.parse(event.data);
                        if (Array.isArray(data)) {
                            data.forEach(log => addLog(log));
                        } else {
                            addLog(data);
                        }
                    }
                };
                
                ws.onerror = () => {
                    statusText.textContent = 'Error';
                    statusDot.classList.add('disconnected');
                };
                
                ws.onclose = () => {
                    statusText.textContent = 'Disconnected - Reconnecting...';
                    statusDot.classList.add('disconnected');
                    setTimeout(connect, 2000);
                };
            }
            
            function addLog(data) {
                logs.push(data);
                
                const container = data.container || 'backend';
                const entry = document.createElement('div');
                entry.className = `log-entry log-${(data.level || 'info').toLowerCase()} new`;
                entry.dataset.container = container;
                
                if (!containerVisible[container]) {
                    entry.style.display = 'none';
                }
                
                const timestamp = data.timestamp ? 
                    new Date(data.timestamp).toLocaleTimeString('en-US', {
                        hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit'
                    }) : new Date().toLocaleTimeString('en-US', {
                        hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit'
                    });
                
                const containerColor = containerColors[container] || '#858585';
                const containerName = container.charAt(0).toUpperCase() + container.slice(1);
                
                entry.innerHTML = `
                    <span class="log-timestamp">${timestamp}</span>
                    <span class="log-container" style="background: ${containerColor}; color: black;">${containerName}</span>
                    <span class="log-level level-${(data.level || 'info').toLowerCase()}">${(data.level || 'INFO').toUpperCase()}</span>
                    <span class="log-message">${escapeHtml(data.message || '')}</span>
                `;
                
                const emptyState = logsContainer.querySelector('.empty-state');
                if (emptyState) emptyState.remove();
                
                logsContainer.appendChild(entry);
                logCount.textContent = logs.length;
                
                if (autoScroll) {
                    logsContainer.scrollTop = logsContainer.scrollHeight;
                }
                
                if (logs.length > 2000) {
                    logs.shift();
                    if (logsContainer.firstChild) {
                        logsContainer.removeChild(logsContainer.firstChild);
                    }
                }
            }
            
            function clearLogs() {
                logs = [];
                logsContainer.innerHTML = '<div class="empty-state"><h2>📡</h2><p>Logs cleared</p></div>';
                logCount.textContent = '0';
            }
            
            function togglePause() {
                isPaused = !isPaused;
                pauseBtn.textContent = isPaused ? '▶️ Resume' : '⏸️ Pause';
                pauseBtn.style.background = isPaused ? '#28a745' : '#ffc107';
            }
            
            function toggleContainer(container) {
                containerVisible[container] = !containerVisible[container];
                const btn = document.getElementById(`toggle-${container}`);
                
                if (containerVisible[container]) {
                    btn.classList.add('active');
                    btn.style.background = containerColors[container];
                } else {
                    btn.classList.remove('active');
                    btn.style.background = 'transparent';
                }
                
                document.querySelectorAll('.log-entry').forEach(entry => {
                    if (entry.dataset.container === container) {
                        entry.style.display = containerVisible[container] ? 'flex' : 'none';
                    }
                });
            }
            
            function filterLogs() {
                const filter = filterInput.value.toLowerCase();
                document.querySelectorAll('.log-entry').forEach(entry => {
                    const text = entry.textContent.toLowerCase();
                    const container = entry.dataset.container;
                    const matchesFilter = filter === '' || text.includes(filter);
                    const containerIsVisible = containerVisible[container];
                    entry.style.display = (matchesFilter && containerIsVisible) ? 'flex' : 'none';
                });
            }
            
            function loadRecentLogs() {
                fetch('/logs/recent')
                    .then(r => r.json())
                    .then(data => {
                        if (data.logs) {
                            clearLogs();
                            data.logs.forEach(log => addLog(log));
                        }
                    })
                    .catch(err => console.error('Failed to load recent logs:', err));
            }
            
            function escapeHtml(text) {
                const div = document.createElement('div');
                div.textContent = text;
                return div.innerHTML;
            }
            
            logsContainer.addEventListener('scroll', () => {
                const isAtBottom = logsContainer.scrollHeight - logsContainer.scrollTop <= logsContainer.clientHeight + 50;
                autoScroll = isAtBottom;
            });
            
            connect();
            setTimeout(loadRecentLogs, 1000);
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


@router.get("/logs/recent")
async def get_recent_logs():
    """Get recent logs from all containers"""
    all_logs = []
    
    for container in CONTAINERS:
        logs = await get_container_logs(container, lines=30)
        all_logs.extend(logs)
    
    # Sort by timestamp
    all_logs.sort(key=lambda x: x.get('timestamp', ''), reverse=False)
    
    return {"logs": all_logs[-200:]}  # Return last 200 logs


@router.websocket("/ws/logs")
async def websocket_logs(websocket: WebSocket):
    """WebSocket endpoint for streaming backend logs only (legacy)"""
    await manager.connect(websocket)
    
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


@router.websocket("/ws/logs/all")
async def websocket_logs_all(websocket: WebSocket):
    """WebSocket endpoint for streaming logs from ALL containers"""
    await websocket.accept()
    logger.info("📺 New multi-container log viewer connected")
    
    tasks = []
    try:
        # Start streaming from all containers
        for container in CONTAINERS:
            task = asyncio.create_task(stream_container_logs(websocket, container))
            tasks.append(task)
        
        # Also broadcast internal logs
        manager.active_connections.append(websocket)
        
        # Wait for all tasks or disconnection
        await asyncio.gather(*tasks, return_exceptions=True)
        
    except WebSocketDisconnect:
        logger.info("📺 Multi-container log viewer disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        # Cancel all streaming tasks
        for task in tasks:
            task.cancel()
        
        if websocket in manager.active_connections:
            manager.active_connections.remove(websocket)


async def broadcast_log(level: str, message: str, container: str = "backend"):
    """
    Broadcast log message to all connected viewers
    
    Args:
        level: Log level (INFO, WARNING, ERROR, DEBUG)
        message: Log message
        container: Container name (default: backend)
    """
    if manager.active_connections:
        await manager.broadcast({
            "timestamp": datetime.utcnow().isoformat(),
            "level": level,
            "message": message,
            "container": container
        })
