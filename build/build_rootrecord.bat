@echo off
setlocal EnableExtensions
title RootRecord - build exe and installer

REM Builds dist\RootRecordBusinessManager\RootRecordBusinessManager.exe (PyInstaller) and build\output\RootRecordSetup-*.exe (Inno Setup).
REM Requires: Python on PATH with PyInstaller installed, Inno Setup 6 (ISCC.exe).
REM By default also Azure-signs (exe before Inno, then installer). Needs build\artifact_signing_metadata.json (or env).
REM To skip signing:   build_rootrecord.bat nosign

cd /d "%~dp0"
set "OPTS=-Sign -StopRunningApp"
if /i "%~1"=="nosign" set "OPTS="

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_windows.ps1" %OPTS%
set ERR=%ERRORLEVEL%

echo.
if %ERR% neq 0 (
  echo Build failed with exit code %ERR%.
  pause
  exit /b %ERR%
)

echo Build finished. Portable exe: ..\dist\RootRecordBusinessManager\RootRecordBusinessManager.exe
echo Installer: output\RootRecordSetup-*.exe
echo.
pause
exit /b 0
