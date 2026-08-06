@echo off
REM ============================================================
REM  ClaudeShield - Double-click to start proxy + tray icon
REM  Like Discord: one click, everything starts.
REM ============================================================

REM Kill any old proxy on port 8787
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8787.*LISTENING"') do (
    taskkill /PID %%a /F >nul 2>&1
)

REM Start proxy + tray together via PowerShell
start "" powershell -ExecutionPolicy Bypass -WindowStyle Hidden -File "%USERPROFILE%\.claude\shield-tray.ps1"
