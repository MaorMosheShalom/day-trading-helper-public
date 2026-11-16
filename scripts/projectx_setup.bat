@echo off
REM Copyright (c) 2026 Maor Moshe Shalom. All rights reserved. See LICENSE.
REM Topstep DEMO setup: saves your TopstepX user name + API key on THIS PC (settings_local.txt, not in git),
REM checks the connection and lists your accounts. Double-click to run.
cd /d "%~dp0.."
python scripts\projectx_setup.py
pause
