@echo off
title Auto-Crypto Launcher
echo.
echo ========================================
echo   Auto-Crypto Launcher
echo ========================================
echo.
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Launch-Auto-Crypto.ps1" %*
set EXITCODE=%ERRORLEVEL%
if not "%EXITCODE%"=="0" (
  echo.
  echo Auto-Crypto launcher exited with code %EXITCODE%.
  pause
)
exit /b %EXITCODE%
