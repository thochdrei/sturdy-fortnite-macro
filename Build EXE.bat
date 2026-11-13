@echo off
title Pirate Shop Macro - Build EXE
cd /d "%~dp0"

rem ---- find Python and resolve its full path ----
set "PYEXE="
for /f "delims=" %%i in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do set "PYEXE=%%i"
if not defined PYEXE (
    for /f "delims=" %%i in ('python -c "import sys;print(sys.executable)" 2^>nul') do set "PYEXE=%%i"
)
if not defined PYEXE (
    echo [ERROR] Python was not found.
    echo Install it from python.org and tick "Add Python to PATH", then run this again.
    goto :fail
)
echo Using: %PYEXE%
"%PYEXE%" --version

if not exist "pirate_shop_macro.pyw" (
    echo [ERROR] pirate_shop_macro.pyw is not in this folder:
    echo %cd%
    goto :fail
)
if not exist "logo.png" (
    echo [ERROR] logo.png is missing. Keep it in this folder.
    goto :fail
)
if not exist "icon.ico" (
    echo [ERROR] icon.ico is missing. Keep it in this folder.
    goto :fail
)

echo.
echo === Step 1/3: installing packages (one at a time) ===
for %%P in (pyautogui pillow keyboard opencv-python pyinstaller) do (
    echo.
    echo --- installing %%P ---
    "%PYEXE%" -m pip install --no-warn-script-location %%P
    if errorlevel 1 (
        echo [ERROR] Failed to install %%P. Read the messages above.
        goto :fail
    )
)

echo.
echo === Step 2/3: building exe (takes a few minutes) ===
"%PYEXE%" -m PyInstaller --noconfirm --clean --noconsole --onefile --uac-admin --icon "icon.ico" --add-data "logo.png;." --name "Pirate Shop Macro" pirate_shop_macro.pyw
if errorlevel 1 (
    echo [ERROR] PyInstaller failed. Read the messages above.
    goto :fail
)

echo.
echo === Step 3/3: copying exe ===
if not exist "dist\Pirate Shop Macro.exe" (
    echo [ERROR] Build finished but dist\Pirate Shop Macro.exe was not created.
    echo Your antivirus may have removed it. Check its quarantine/protection history.
    goto :fail
)
copy /y "dist\Pirate Shop Macro.exe" "Pirate Shop Macro.exe" >nul
if not exist "Pirate Shop Macro.exe" (
    echo [ERROR] Could not copy the exe. Look in the "dist" folder for it.
    goto :fail
)

echo.
echo ================================================
echo  SUCCESS: "Pirate Shop Macro.exe" is in:
echo  %cd%
echo  Keep the "images" folder next to it.
echo ================================================
pause
exit /b 0

:fail
echo.
echo ================================================
echo  BUILD FAILED - see the error above.
echo ================================================
pause
exit /b 1
