@echo off
setlocal
title RootRecord — build MSIX for Store
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0build\build_msix.ps1"
if errorlevel 1 (
  echo.
  echo MSIX build failed.
  pause
  exit /b 1
)
echo.
echo MSIX output: build\output\RootRecord-BusinessManager_*.msix
pause
exit /b 0
