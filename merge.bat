@echo off
REM WhatsMerge — double-click this to rebuild the archive.
REM Reads every export in .\exports and writes .\output\archive.html

setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo Python was not found on your PATH.
  echo Install Python 3.10 or newer from https://python.org and try again.
  echo.
  pause
  exit /b 1
)

python -m app --open %*
set EXITCODE=%ERRORLEVEL%

echo.
if %EXITCODE% neq 0 (
  echo Something went wrong ^(exit code %EXITCODE%^). The messages above say what.
)
pause
exit /b %EXITCODE%
