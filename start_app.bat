@echo off
setlocal
cd /d "%~dp0"
set PORT=5001
set FLASK_DEBUG=1
py app.py
