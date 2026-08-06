@echo off
REM ============================================================
REM  Claude Code Retry Proxy - Windows Auto-Start
REM  Place this file in: shell:startup
REM  (Win+R -> shell:startup -> Enter)
REM ============================================================

REM --- Configure your upstream gateway here ---
set UPSTREAM=https://agentrouter.org
set PORT=8787

REM --- Path to the proxy script ---
set PROXY_SCRIPT=%USERPROFILE%\.claude\retry-proxy.py

REM --- Start minimized in background ---
start /min pythonw "%PROXY_SCRIPT%" --upstream %UPSTREAM% --port %PORT%
