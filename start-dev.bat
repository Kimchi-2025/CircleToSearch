
@echo off
setlocal
cd /d "%~dp0"
if /I "%~1"=="--stop" (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-dev.ps1" -Stop
) else if /I "%~1"=="--check" (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-dev.ps1" -Check
) else if /I "%~1"=="--no-browser" (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-dev.ps1" -NoBrowser
) else (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-dev.ps1"
)
if errorlevel 1 (
  echo.
  echo CircleToSearch startup failed. Read the reason above.
  pause
  exit /b 1
)
