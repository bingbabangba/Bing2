@echo off
:: Re-launch elevated (the game runs as admin, so input needs admin too)
net session >nul 2>&1 || (powershell -Command "Start-Process -Verb RunAs -FilePath '%~f0' -ArgumentList '%*'" & exit /b)
cd /d "%~dp0"
python -m hsr_reroll %*
pause
