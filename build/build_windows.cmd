@echo off
REM PowerShell: .\build_windows.cmd   or   .\build_windows.cmd -Sign [-StopRunningApp]
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_windows.ps1" %*
