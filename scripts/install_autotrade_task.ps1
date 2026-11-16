# Copyright (c) 2026 Maor Moshe Shalom. All rights reserved. See LICENSE.
# Creates the Windows task "DTH paper trader" (run via install_autotrade_task.bat) = the FREE DEMO:
#  - starts scripts\autotrade.py every Monday-Friday at 15:15 Israel time; it waits for 09:30 New York, trades your
#    live plan on a paper 50k case minute by minute, and stops after 16:00 New York
#  - runs HIDDEN (pythonw, no window that can be closed by mistake) and writes reports\autotrade.log
#  - RESTARTS ITSELF: the trigger repeats every 10 minutes until 23:15; if the demo isn't running (stopped, crashed,
#    PC was asleep or off) it starts again and catches up from the saved paper case. Button "3 Stop the demo now"
#    writes a flag so it does NOT restart that day.
#  - WAKES the PC from sleep to start, and keeps it awake while the session runs
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$py = (Get-Command python -ErrorAction Stop).Source
$pyw = Join-Path (Split-Path -Parent $py) "pythonw.exe"
if (-not (Test-Path $pyw)) { $pyw = $py }               # no pythonw (unusual install): use python
$script = Join-Path $root "scripts\autotrade.py"
$log = Join-Path $root "reports\autotrade.log"
$action = New-ScheduledTaskAction -Execute $pyw -Argument "`"$script`" --log `"$log`"" -WorkingDirectory $root
$days = @("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")
$t1 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At "15:15"
$rep = New-ScheduledTaskTrigger -Once -At "15:15" -RepetitionInterval (New-TimeSpan -Minutes 10) -RepetitionDuration (New-TimeSpan -Hours 8)
$t1.Repetition = $rep.Repetition
$set = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
       -ExecutionTimeLimit (New-TimeSpan -Hours 10) -MultipleInstances IgnoreNew
$prin = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName "DTH paper trader" -Action $action -Trigger $t1 -Settings $set `
       -Principal $prin -Force | Out-Null
Write-Host "OK: task 'DTH paper trader' created (Mon-Fri 15:15, hidden, restarts itself every 10 min, log: reports\autotrade.log)." -ForegroundColor Green

$out = powercfg /q SCHEME_CURRENT SUB_SLEEP RTCWAKE 2>$null | Out-String
if ($out -match "Current AC Power Setting Index:\s+0x00000000") {
    Write-Host "Wake timers are OFF in the power plan - turning them ON..." -ForegroundColor Yellow
    powercfg /SETACVALUEINDEX SCHEME_CURRENT SUB_SLEEP RTCWAKE 1
    powercfg /SETDCVALUEINDEX SCHEME_CURRENT SUB_SLEEP RTCWAKE 1
    powercfg /S SCHEME_CURRENT
}
Write-Host "Wake timers: allowed." -ForegroundColor Green
