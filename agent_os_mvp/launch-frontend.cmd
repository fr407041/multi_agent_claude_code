@echo off
setlocal

cd /d "%~dp0frontend"
set VITE_API_BASE_URL=http://127.0.0.1:8010
call "C:\Program Files\nodejs\npm.cmd" run dev -- --host 127.0.0.1 --port 5174 1> "%~dp0logs\frontend-5174.out.log" 2> "%~dp0logs\frontend-5174.err.log"
