@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" ( echo O hub ainda nao foi instalado. & pause & exit /b 1 )
".venv\Scripts\python.exe" tools\parar.py
timeout /t 3 >nul
