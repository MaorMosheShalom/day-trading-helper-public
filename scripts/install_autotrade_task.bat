@echo off
REM Copyright (c) 2026 Maor Moshe Shalom. All rights reserved. See LICENSE.
REM THE FREE DEMO. Double-click once: creates the task "DTH paper trader" that trades your live plan
REM on a paper 50k case every Monday-Friday (starts 15:15 Israel, waits for 09:30 New York,
REM stops after 16:00 New York). It wakes the PC from sleep and keeps it awake during the session.
REM To remove it later:  schtasks /Delete /TN "DTH paper trader" /F
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_autotrade_task.ps1"
pause
