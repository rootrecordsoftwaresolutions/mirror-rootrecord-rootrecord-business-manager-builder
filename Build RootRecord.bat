@echo off
REM Double-click = build + Azure sign. To build only without signing: Build RootRecord.bat nosign
call "%~dp0build\build_rootrecord.bat" %*
