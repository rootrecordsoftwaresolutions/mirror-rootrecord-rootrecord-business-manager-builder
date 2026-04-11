@echo off
setlocal EnableExtensions
title RootRecord — build exe and installer

REM Builds dist\RootRecord\RootRecord.exe (PyInstaller) and build\output\RootRecordSetup-*.exe (Inno Setup).
REM Requires: Python on PATH with PyInstaller installed, Inno Setup 6 (ISCC.exe).

cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_windows.ps1"
set ERR=%ERRORLEVEL%

echo.
if %ERR% neq 0 (
  echo Build failed with exit code %ERR%.
  pause
  exit /b %ERR%
)

echo Build finished. Portable exe: ..\dist\RootRecord\RootRecord.exe
echo Installer: output\RootRecordSetup-*.exe
echo.
pause
exit /b 0
