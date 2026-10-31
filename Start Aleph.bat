@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 goto :missing_python

py -3 -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>nul
if errorlevel 1 goto :old_python

if not exist ".venv\Scripts\python.exe" (
    echo First launch: creating a private Python environment.
    py -3 -m venv .venv
    if errorlevel 1 goto :setup_failed
)

if not exist ".venv\Scripts\alephgeo.exe" (
    echo Installing Aleph. This may take a few minutes.
    ".venv\Scripts\python.exe" -m pip install .
    if errorlevel 1 goto :setup_failed
)

echo Starting the Aleph dashboard. Keep this window open while using it.
".venv\Scripts\alephgeo.exe" dashboard -o captures
if errorlevel 1 goto :start_failed
exit /b 0

:missing_python
echo Python 3.11 or newer is required to run Aleph.
echo Install Python from https://www.python.org/downloads/windows/ and select the Python Launcher option.
goto :failed

:old_python
echo Python 3.11 or newer is required. Install a newer Python version from https://www.python.org/downloads/windows/.
goto :failed

:setup_failed
echo Aleph setup failed. Check your internet connection and try again.
goto :failed

:start_failed
echo Aleph could not start. Read the error above and try again.

:failed
echo.
pause
exit /b 1
