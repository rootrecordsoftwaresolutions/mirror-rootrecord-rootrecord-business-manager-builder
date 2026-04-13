@echo off
REM cmd.exe:        sign_release_azure.cmd -StopRunningApp
REM PowerShell:      .\sign_release_azure.cmd -StopRunningApp
REM (No spinner characters before the line; paste one line only.)
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0sign_release_azure.ps1" %*
