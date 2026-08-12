@echo off
REM Startup script for omniWA Autonomous WhatsApp AI Assistant & Personal OS
REM Starts all required services using Docker Compose

echo ======================================
echo omniWA WhatsApp AI Assistant ^& Personal OS
echo ======================================
echo.

echo [1/4] Checking Docker...
docker --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Docker is not installed or not running
    echo Please install Docker Desktop and ensure it's running
    pause
    exit /b 1
)
echo OK: Docker found

echo.
echo [2/4] Stopping any existing containers...
cd /d "%~dp0..\docker"
docker-compose down

echo.
echo [3/4] Building and starting services...
echo   - PostgreSQL (database)
echo   - Redis (message queue + cache)
echo   - FastAPI (webhook API)
echo   - Agent Worker (message processing)
echo   - Scheduler Worker (proactive notifications)
echo.
docker-compose up -d --build

if errorlevel 1 (
    echo ERROR: Failed to start services
    pause
    exit /b 1
)

echo.
echo [4/4] Waiting for services to be healthy...
timeout /t 10 /nobreak >nul

echo.
echo ======================================
echo Services Started Successfully!
echo ======================================
echo.
echo FastAPI Backend:       http://localhost:8000
echo API Documentation:     http://localhost:8000/docs
echo Health Check:          http://localhost:8000/health/detailed
echo Real-time Logs:        http://localhost:8000/logs/viewer
echo.
echo Redis:                 localhost:6379
echo PostgreSQL:            Internal only (postgres:5432)
echo.
echo ======================================
echo Background Workers
echo ======================================
echo Agent Worker:          Processing WhatsApp messages
echo Scheduler Worker:      Handling reminders and notifications
echo.
echo View logs:
echo   docker-compose logs -f backend
echo   docker-compose logs -f agent_worker
echo   docker-compose logs -f scheduler_worker
echo.
echo Stop services:
echo   docker-compose down
echo.
pause
