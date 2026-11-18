@echo off
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\uvicorn.exe" (
    ".venv\Scripts\uvicorn.exe" backend.app.main:app --host 0.0.0.0 --port 8000
    goto :eof
)

uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
