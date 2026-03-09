@echo off
:: batch file to launch Home13 Flask server and open the default browser
cd /d "%~dp0"

:: install dependencies if they are missing
py -c "import flask, reportlab, openpyxl" >nul 2>&1
if errorlevel 1 (
	echo Installing Python dependencies...
	py -m pip install -r requirements.txt
	if errorlevel 1 (
		echo Failed to install dependencies. Please check your Python/pip setup.
		pause
		exit /b 1
	)
)

:: pick a free local port (5000-5010) to avoid conflicts
for /f %%P in ('powershell -NoProfile -Command "$port=5000..5010 | Where-Object { -not (Get-NetTCPConnection -LocalPort $_ -State Listen -ErrorAction SilentlyContinue) } | Select-Object -First 1; if ($port) { $port } else { 5000 }"') do set "PORT=%%P"

:: run in debug mode for local development
set "FLASK_DEBUG=1"

:: start server in a new window so the launcher console is free
start "Home13 server" cmd /k "set PORT=%PORT%&&set FLASK_DEBUG=%FLASK_DEBUG%&&py app.py"

:: give the server a moment to start then open browser
timeout /t 2 /nobreak >nul
start "" "http://127.0.0.1:%PORT%/"
