@echo off
REM Default: Azure signing + stop running app (same as build\build_rootrecord.bat).
REM To skip signing:  build_windows.cmd nosign   (optional extra args after nosign are passed through)
setlocal EnableExtensions
cd /d "%~dp0"
if /i "%~1"=="nosign" (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_windows.ps1" %2 %3 %4 %5 %6 %7 %8 %9
  exit /b %ERRORLEVEL%
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_windows.ps1" -Sign -StopRunningApp %*
exit /b %ERRORLEVEL%
