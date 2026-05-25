@echo off
REM Commit + push the OpenWA migration changes
REM Double-click this file, or run from a terminal in the project root.

cd /d "%~dp0"

echo === Current status ===
git status
echo.

echo === Staging changed files ===
git add docker/docker-compose.yml ^
        backend/.env.example ^
        backend/app ^
        backend/migrations ^
        scripts/openwa_setup.py ^
        commit-and-push.bat

echo.
echo === Creating commit ===
git commit ^
  -m "Build backend + migrate WhatsApp layer to OpenWA" ^
  -m "Replaces the Meta Cloud API with OpenWA (whatsapp-web.js, QR-linked device) so the agent can read personal chats and groups the user belongs to, and removes the 24h token expiry." ^
  -m "Adds the full Python backend that was missing: FastAPI app, async SQLAlchemy models + init.sql, Redis streams + delayed-job scheduler, LLM agent engine with tool calling (GitHub Models / Google AI), Google Calendar service with Meet link support, agent_worker and scheduler_worker, OAuth callback, and QR setup endpoints." ^
  -m "New behaviors: proactive messaging (per-event reminders 15m/1h/1d, morning briefing, evening summary, conflict nudges); group + DM reply routing via OpenWA chat IDs; permission flow where the agent DMs the owner with a short-code approval request before risky actions (group reply, delete event, optionally create event)." ^
  -m "Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"

if errorlevel 1 (
    echo.
    echo Commit failed. See errors above.
    pause
    exit /b 1
)

echo.
echo === Pushing to origin ===
git push origin main
if errorlevel 1 (
    echo.
    echo Push failed. If this is the first push, try: git push -u origin main
    pause
    exit /b 1
)

echo.
echo === Final status ===
git status
echo.
echo Done.
pause
