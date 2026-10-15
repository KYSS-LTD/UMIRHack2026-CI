@echo off
rem Start the site and the Telegram bot by double click (same as start.ps1; uses the PowerShell that ships with Windows).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
pause
