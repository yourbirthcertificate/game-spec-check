@echo off
rem Starts Game Spec Check and opens it in your browser.
rem Close this window to stop the app.
cd /d "%~dp0"
title Game Spec Check

where py >nul 2>nul
if %errorlevel%==0 (
  py -3 run.py %*
  goto :done
)
where python >nul 2>nul
if %errorlevel%==0 (
  python run.py %*
  goto :done
)
echo Python 3 was not found on this PC.
echo Install it from https://www.python.org/downloads/ and run this file again.

:done
if not %errorlevel%==0 pause
