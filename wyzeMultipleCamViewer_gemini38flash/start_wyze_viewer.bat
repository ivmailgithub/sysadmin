@echo off
setlocal EnableDelayedExpansion
title Wyze Multi-Cam Viewer
cd /d "%~dp0"

echo ================================================================
echo       WYZE MULTI-CAM VIEWER • BIRDSEYE NOC (WINDOWS)
echo ================================================================
echo.

set "PY_EXE="
if exist "%~dp0Scripts\python.exe" (
    set "PY_EXE=%~dp0Scripts\python.exe"
) else if exist "%~dp0.venv\Scripts\python.exe" (
    set "PY_EXE=%~dp0.venv\Scripts\python.exe"
) else if exist "C:\Python\Python313\python.exe" (
    set "PY_EXE=C:\Python\Python313\python.exe"
) else if exist "C:\Python\Python312\python.exe" (
    set "PY_EXE=C:\Python\Python312\python.exe"
) else if exist "..\.venv\Scripts\python.exe" (
    set "PY_EXE=..\.venv\Scripts\python.exe"
) else (
    for /f "tokens=*" %%i in ('where python 2^>nul') do (
        set "PY_EXE=%%i"
        goto :found_py
    )
)

:found_py
if "%PY_EXE%"=="" (
    echo [ERROR] Python interpreter not found!
    echo Please install Python 3.12/3.13 or check PATH.
    pause
    exit /b 1
)

echo Starting Wyze Server using: %PY_EXE%
echo Web Interface: http://localhost:5005
echo.

"%PY_EXE%" wyze_server.py %*

if errorlevel 1 (
    echo.
    echo [ERROR] wyze_server.py exited with an error code.
    pause
)
