@echo off
setlocal EnableExtensions
title RootRecord — signed build + MSIX for Store
cd /d "%~dp0"
echo [1/2] PyInstaller + Inno with Azure signing (packages signed binaries into MSIX) ...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0build\build_windows.ps1" -Sign -StopRunningApp
if errorlevel 1 (
  echo.
  echo Signed Windows build failed.
  pause
  exit /b 1
)
echo [2/2] Packing MSIX ...
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
