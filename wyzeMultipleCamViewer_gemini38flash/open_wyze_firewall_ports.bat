@echo off
setlocal EnableDelayedExpansion
title Open Wyze Multi-Cam Viewer Firewall Ports
cd /d "%~dp0"

echo ================================================================
echo       WYZE MULTI-CAM VIEWER • FIREWALL CONFIGURATOR
echo ================================================================
echo.

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [!] Requesting Administrator privileges to manage Windows Firewall...
    powershell -NoProfile -Command "Start-Process cmd.exe -ArgumentList '/c `\"%~f0`\"' -Verb RunAs"
    exit /b
)

echo [*] Running firewall configuration script...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0open_wyze_firewall_ports.ps1"

echo.
echo ================================================================
echo Press any key to close this window...
pause >nul
