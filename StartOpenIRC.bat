@echo off
setlocal EnableExtensions DisableDelayedExpansion
title OpenIRC
pushd "%~dp0"
if errorlevel 1 goto directory_error

if exist ".venv\Scripts\python.exe" goto check_environment
if exist ".venv" goto environment_error

echo Creating the OpenIRC Python environment...
py -3 -c "import sys; sys.exit(sys.version_info < (3, 12))" >nul 2>&1
if not errorlevel 1 goto create_with_launcher
python -c "import sys; sys.exit(sys.version_info < (3, 12))" >nul 2>&1
if not errorlevel 1 goto create_with_python
echo Python 3.12 or newer is required.
echo Install Python from https://www.python.org/downloads/windows/ and run this file again.
goto failure

:create_with_launcher
py -3 -m venv .venv
if errorlevel 1 goto environment_error
goto check_environment

:create_with_python
python -m venv .venv
if errorlevel 1 goto environment_error

:check_environment
".venv\Scripts\python.exe" -c "import sys; sys.exit(sys.version_info < (3, 12))" >nul 2>&1
if errorlevel 1 goto environment_error
".venv\Scripts\python.exe" -c "import OpenIRC, argon2; from PyQt6 import QtWidgets; from importlib.metadata import version; version('OpenIRC')" >nul 2>&1
if not errorlevel 1 goto launch

echo Installing OpenIRC and its desktop dependencies...
echo The first installation needs internet access.
".venv\Scripts\python.exe" -m pip --version >nul 2>&1
if not errorlevel 1 goto install
".venv\Scripts\python.exe" -m ensurepip
if errorlevel 1 goto install_error

:install
".venv\Scripts\python.exe" -m pip install -e ".[gui]"
if errorlevel 1 goto install_error
".venv\Scripts\python.exe" -c "import OpenIRC, argon2; from PyQt6 import QtWidgets" >nul 2>&1
if errorlevel 1 goto install_error

:launch
echo Starting the OpenIRC console and server...
echo On first launch, complete the setup wizard to start listening.
".venv\Scripts\python.exe" -m OpenIRC --start-server %*
set "OPENIRC_EXIT_CODE=%ERRORLEVEL%"
if "%OPENIRC_EXIT_CODE%"=="0" goto done
echo OpenIRC exited with error code %OPENIRC_EXIT_CODE%.
pause
goto done

:environment_error
echo The .venv environment could not be created or does not contain a working Python 3.12+.
echo Close OpenIRC, rename the .venv folder, and run this file again to create a fresh environment.
goto failure

:install_error
echo Dependency installation failed. Check the error above and your internet connection, then try again.

:failure
set "OPENIRC_EXIT_CODE=1"
pause

:done
popd
exit /b %OPENIRC_EXIT_CODE%

:directory_error
echo Could not open the OpenIRC directory.
pause
exit /b 1
