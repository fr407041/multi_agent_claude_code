@echo off
setlocal

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-dashboard.ps1"
