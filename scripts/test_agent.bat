@echo off
REM CLI Interface to Test Agent Flow
REM Run this from project root directory

echo.
echo ========================================
echo   WhatsApp AI Agent Flow Tester
echo ========================================
echo.

REM Check if Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not in PATH
    echo Please install Python 3.8 or higher
    pause
    exit /b 1
)

REM Check if colorama is installed
python -c "import colorama" >nul 2>&1
if errorlevel 1 (
    echo Installing required package: colorama
    pip install colorama
    echo.
)

REM Check if httpx is installed
python -c "import httpx" >nul 2>&1
if errorlevel 1 (
    echo Installing required package: httpx
    pip install httpx
    echo.
)

REM Run the test script
python scripts\test_agent_flow.py

pause
