# Copyright (c) 2026 Maor Moshe Shalom. All rights reserved. See LICENSE.
# Creates the Windows task "DTH daily trade pictures" (run via install_daily_pictures_task.bat).
#  - runs scripts\daily_pictures.py every Monday-Saturday at 23:30 (after the US close, Israel time)
#  - WAKES the PC from sleep to run it
#  - if the PC was off / the time was missed, it runs as soon as possible afterwards, and also at every logon
#  - the user must stay LOGGED IN (locking the screen or sleep is fine) - Google Drive only works inside the session
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$py = (Get-Command python -ErrorAction Stop).Source
$script = Join-Path $root "scripts\daily_pictures.py"
$action = New-ScheduledTaskAction -Execute $py -Argument "`"$script`"" -WorkingDirectory $root
$days = @("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")
$t1 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At "23:30"
$t2 = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$t2.Delay = "PT10M"
$set = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
       -ExecutionTimeLimit (New-TimeSpan -Hours 3) -MultipleInstances IgnoreNew
$prin = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName "DTH daily trade pictures" -Action $action -Trigger @($t1, $t2) -Settings $set `
       -Principal $prin -Force | Out-Null
Write-Host "OK: task 'DTH daily trade pictures' created (Mon-Sat 23:30 + 10 min after logon, wakes the PC from sleep)." -ForegroundColor Green

# wake timers must be allowed in the power plan, otherwise Windows ignores -WakeToRun
$out = powercfg /q SCHEME_CURRENT SUB_SLEEP RTCWAKE 2>$null | Out-String
if ($out -match "Current AC Power Setting Index:\s+0x00000000") {
    Write-Host "Wake timers are OFF in the power plan - turning them ON (plugged in and on battery)..." -ForegroundColor Yellow
    powercfg /SETACVALUEINDEX SCHEME_CURRENT SUB_SLEEP RTCWAKE 1
    powercfg /SETDCVALUEINDEX SCHEME_CURRENT SUB_SLEEP RTCWAKE 1
    powercfg /S SCHEME_CURRENT
}
Write-Host "Wake timers: allowed." -ForegroundColor Green
Write-Host "Test it now: right-click the task in Task Scheduler > Run, or: python scripts\daily_pictures.py"
