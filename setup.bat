@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
pushd "%~dp0"
if errorlevel 1 exit /b 1
if exist ".venv\Scripts\python.exe" goto existing
for %%V in (3.13 3.12 3.11 3.10) do (
  py -%%V -c "import sys; sys.exit(not ((3,10) <= sys.version_info[:2] < (3,14)))" >nul 2>nul
  if not errorlevel 1 (
    set "ledger_python_version=%%V"
    goto python_launcher
  )
)
python -c "import sys; sys.exit(not ((3,10) <= sys.version_info[:2] < (3,14)))" >nul 2>nul
if errorlevel 1 goto missing_python
python -X utf8 setup_windows.py %*
goto result
:python_launcher
py -%ledger_python_version% -X utf8 setup_windows.py %*
goto result
:existing
".venv\Scripts\python.exe" -X utf8 setup_windows.py %*
goto result
:missing_python
type "docs\SETUP_WINDOWS.txt"
set "ledger_exit=2"
goto finish
:result
set "ledger_exit=%errorlevel%"
:finish
popd
if "%~1"=="--check" goto done
pause
:done
exit /b %ledger_exit%
