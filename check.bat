@echo off
rem BI Lens ortam kontrolu: backend neden acilmiyor?
cd /d "%~dp0"
if not exist "backend\.venv\Scripts\python.exe" (
    echo backend\.venv yok: once start.bat calistirin ^(Python 3.10+ gerekli^).
    pause
    exit /b 1
)
set "BI_PORT=8000"
if exist "backend\.port" set /p BI_PORT=<"backend\.port"
backend\.venv\Scripts\python -X utf8 backend\scripts\doctor.py --port %BI_PORT%
pause
