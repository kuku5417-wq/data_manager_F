@echo off
title data_manager
REM data_manager - quick launcher (venv must already exist; run setup_env.bat first)
set "PYEXE=%LOCALAPPDATA%\venvs\data_manager\Scripts\python.exe"
if not exist "%PYEXE%" (
    echo [ERROR] local venv not found: %PYEXE%
    echo Run setup_env.bat first (creates venv + installs deps).
    pause
    exit /b 1
)
echo Starting data_manager ... http://localhost:8510
"%PYEXE%" -m streamlit run "%~dp0app.py" --server.port 8510 --browser.gatherUsageStats false
