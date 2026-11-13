@echo off
title Pirate Shop Macro - Install
cd /d "%~dp0"

set "PYEXE="
for /f "delims=" %%i in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do set "PYEXE=%%i"
if not defined PYEXE (
    for /f "delims=" %%i in ('python -c "import sys;print(sys.executable)" 2^>nul') do set "PYEXE=%%i"
)
if not defined PYEXE (
    echo [ERROR] Python was not found.
    echo Install it from python.org and tick "Add Python to PATH", then run this again.
    pause
    exit /b 1
)

echo Using: %PYEXE%
"%PYEXE%" --version
echo.
echo Installing packages for Pirate Shop Macro...
"%PYEXE%" -m pip install --no-warn-script-location -r requirements.txt
if errorlevel 1 (
    echo.
    echo [ERROR] Install failed. Read the messages above.
    echo If it mentions a package that can't be built, install Python 3.12 or 3.13.
    pause
    exit /b 1
)

echo.
echo Done! Now double-click pirate_shop_macro.pyw to open the app.
pause
