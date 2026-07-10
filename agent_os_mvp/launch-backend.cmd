@echo off
setlocal

cd /d "%~dp0backend"
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8010 1> "%~dp0logs\backend-8010.out.log" 2> "%~dp0logs\backend-8010.err.log"
