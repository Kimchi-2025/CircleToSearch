@echo off
setlocal
cd /d "%~dp0"
if /I "%~1"=="--check" (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-mobile-test.ps1" -Check
) else (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-mobile-test.ps1" %*
)
if errorlevel 1 (
  echo.
  echo Mobile test setup failed. Read the reason above.
  pause
  exit /b 1
)
