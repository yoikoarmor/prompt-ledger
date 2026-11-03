@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
pushd "%~dp0"
if errorlevel 1 exit /b 1
if not exist ".venv\Scripts\python.exe" goto missing_venv
".venv\Scripts\python.exe" -X utf8 launch.py %*
set "ledger_exit=%errorlevel%"
goto finish
:missing_venv
type "docs\START_WINDOWS.txt"
set "ledger_exit=2"
:finish
popd
if "%~1"=="--check" goto done
if not "%ledger_exit%"=="0" pause
:done
exit /b %ledger_exit%
