@echo off
REM ============================================================
REM  ClaudeShield - Double-click to start proxy + tray icon
REM  Like Discord: one click, everything starts.
REM
REM  To install: copy this file + shield-tray.ps1 + retry-proxy.py
REM  to %USERPROFILE%\.claude\
REM
REM  For auto-start on boot: copy this .bat to
REM  %APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\
REM ============================================================

REM Kill any old proxy on port 8787
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8787.*LISTENING"') do (
    taskkill /PID %%a /F >nul 2>&1
)

REM Start proxy + tray together via PowerShell (hidden window)
start "" powershell -ExecutionPolicy Bypass -WindowStyle Hidden -File "%USERPROFILE%\.claude\shield-tray.ps1"
