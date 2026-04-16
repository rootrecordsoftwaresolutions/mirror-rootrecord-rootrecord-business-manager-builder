@echo off
setlocal EnableExtensions
REM Publishes developer (private builder) + customer (public download) for APP_VERSION in app_version.py.
REM Default: signed Windows build. Use -NoSign or a first argument of "nosign" for local unsigned only.
REM Other args are forwarded to publish_dual_releases.ps1, e.g.:
REM   Publish-Dual-Releases.bat -SkipBuild -SkipBuilderIfTagExists
REM Full help:  powershell -NoProfile -Command "Get-Help .\build\publish_dual_releases.ps1 -Full"

cd /d "%~dp0"
if /i "%~1"=="nosign" (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0publish_dual_releases.ps1" -NoSign %2 %3 %4 %5 %6 %7 %8 %9
  exit /b %ERRORLEVEL%
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0publish_dual_releases.ps1" %*
exit /b %ERRORLEVEL%
