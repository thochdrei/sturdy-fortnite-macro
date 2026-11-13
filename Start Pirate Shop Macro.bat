@echo off
cd /d "%~dp0"
where pyw >nul 2>&1 && (start "" pyw -3 "pirate_shop_macro.pyw" & exit /b)
where pythonw >nul 2>&1 && (start "" pythonw "pirate_shop_macro.pyw" & exit /b)
echo Python was not found. Install it from python.org (tick "Add Python to PATH"),
echo then run "Install Requirements.bat".
pause
