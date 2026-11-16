@echo off
REM Copyright (c) 2026 Maor Moshe Shalom. All rights reserved. See LICENSE.
REM Double-click once: creates the task "DTH daily trade pictures"
REM (every Mon-Sat 23:30 Israel + after logon; wakes the PC from sleep; catches up missed days).
REM To remove it later:  schtasks /Delete /TN "DTH daily trade pictures" /F
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_daily_pictures_task.ps1"
pause
