@echo off
REM PowerShell: .\publish_product_release.cmd
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0publish_product_release.ps1" %*
