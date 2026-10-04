@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [FIL] Python environment not found. Create .venv and install requirements.txt first.
  pause
  exit /b 1
)
start "" "http://127.0.0.1:8000"
".venv\Scripts\python.exe" -m uvicorn apps.api.app.main:app --host 127.0.0.1 --port 8000
