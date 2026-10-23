@echo off
rem Portfolio tracker launcher (read-only). Double-click, or run from a terminal.
rem Extra options are passed through, e.g.:  run_tracker.cmd --mock   /   run_tracker.cmd --once
chcp 65001 >nul
set PYTHONUTF8=1
"%USERPROFILE%\.venvs\broker\Scripts\python.exe" "%~dp0portfolio_tracker.py" %*
if "%~1"=="" pause
