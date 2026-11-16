# Copyright (c) 2026 Maor Moshe Shalom. All rights reserved. See LICENSE.
# The demo's control buttons (small .bat files in the folder above the project call this): status / start / stop / off / on / trades /
# orders / past. Usage: powershell -File demo_control.ps1 <action>
param([string]$Action = "status")
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot                 # ...\Day-Trading-Helper
$home_ = Split-Path -Parent $root                         # the folder above the project (where the buttons are)
$TASK = "DTH paper trader"
$PICS = "DTH daily trade pictures"
$log = Join-Path $root "reports\autotrade.log"
$plan = "example"
$pf = Join-Path $root "reports\live_plan.txt"
if (Test-Path $pf) { $plan = (Get-Content $pf -Raw).Trim() }        # written by scripts\autotrade.py
$state = Join-Path $root "reports\paper_${plan}_state.json"
$orders = Join-Path $root "reports\paper_${plan}_orders.csv"

function Running-Demo {
    Get-CimInstance Win32_Process -Filter "Name like 'python%'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match "autotrade\.py" -and $_.CommandLine -notmatch "--status|--replay-day" }
}

function Say($text, $color = "White") { Write-Host $text -ForegroundColor $color }

function NY-Now { [System.TimeZoneInfo]::ConvertTimeBySystemTimeZoneId([DateTime]::UtcNow, "Eastern Standard Time") }
function Stop-Flag { Join-Path $root ("reports\demo_stopped_{0:yyyy-MM-dd}.flag" -f (NY-Now)) }

function Show-Status {
    Say "=================== THE DEMO: plan $plan on a paper 50k account ===================" Cyan
    $t = Get-ScheduledTask -TaskName $TASK -ErrorAction SilentlyContinue
    if (-not $t) {
        Say "Automatic demo:  NOT INSTALLED - run scripts\install_autotrade_task.bat" Red
    } else {
        $i = Get-ScheduledTaskInfo -TaskName $TASK
        if ($t.State -eq "Disabled") { Say "Automatic demo:  OFF (turn it on with '5 Demo ON')" Yellow }
        else { Say ("Automatic demo:  ON  - next start {0:ddd dd/MM HH:mm} (it waits for 16:30 Israel, trades until 23:00)" -f $i.NextRunTime) Green }
        if ($i.LastRunTime -and $i.LastRunTime.Year -gt 2000) { Say ("Last start:      {0:ddd dd/MM HH:mm}" -f $i.LastRunTime) }
    }
    $p = Running-Demo
    $ny = NY-Now
    $session = $ny.DayOfWeek -ne "Saturday" -and $ny.DayOfWeek -ne "Sunday" -and $ny.TimeOfDay -ge [TimeSpan]"09:31" -and $ny.TimeOfDay -le [TimeSpan]"16:00"
    if ($p) { Say ("Running now:     YES (since {0:HH:mm})" -f $p[0].CreationDate) Green }
    elseif (Test-Path (Stop-Flag)) { Say "Running now:     no - YOU STOPPED IT for today (button 3). It starts again next weekday, or press 2." Yellow }
    elseif ($session) { Say "Running now:     NO, but the market is open - it restarts by itself within 10 minutes (or press 2)." Yellow }
    else { Say "Running now:     no (it runs Mon-Fri 15:15 -> 23:00 Israel, and restarts by itself if it stops)" }
    if (Test-Path $state) {
        $age = [int]((Get-Date) - (Get-Item $state).LastWriteTime).TotalMinutes
        $jj = Get-Content $state -Raw | ConvertFrom-Json
        $lm = [int]$jj.last_minute
        Say ("Last update:     {0} {1:D2}:{2:D2} New York  ({3} minutes ago)" -f $jj.last_day, [int][math]::Floor($lm / 60), ($lm % 60), $age)
        if ($p -and $session -and $age -gt 5) { Say "                 STUCK? It runs but hasn't updated for $age minutes (no data?). Press 3 then 2." Red }
    }
    Say ""
    Say "------------------------------------ the paper case ------------------------------------" Cyan
    if (Test-Path $state) {
        $j = Get-Content $state -Raw | ConvertFrom-Json
        $open = if ($j.open -and $j.open.Count -gt 0) { ($j.open | ForEach-Object { "$($_.tag) $(if ($_.side -gt 0) {'LONG'} else {'SHORT'}) $($_.micros) MNQ at $($_.entry)" }) -join "; " } else { "none" }
        if ($open -ne "none" -and [string]$j.last_day -ne ("{0:yyyy-MM-dd}" -f $ny)) { $open += "   (from $($j.last_day) - it is finished at the next start)" }
        Say ("Case {0} ({1})   balance `${2:N0}   loses at `${3:N0}" -f $j.case, $j.phase, [double]$j.balance, [double]$j.limit)
        Say ("Open trade now:  {0}" -f $open)
        Say ("Paid to you so far `${0:N0} - case fees `${1:N0} = `${2:N0}      last day traded: {3}" -f [double]$j.paid_to_you, [double]$j.fees_paid, [double]$j.net, $j.last_day)
    } else { Say "No paper case yet - it starts on the first trading day." }
    Say ""
    Say "------------------------------------ last orders ------------------------------------" Cyan
    if (Test-Path $orders) { Get-Content $orders -Tail 8 | ForEach-Object { Say $_ } } else { Say "No orders yet." }
    Say ""
    Say "------------------------------------ last lines of the log ------------------------------------" Cyan
    if (Test-Path $log) { Get-Content $log -Tail 12 | ForEach-Object { Say $_ DarkGray } } else { Say "No log yet." }
}

switch ($Action) {
    "status" { Show-Status }
    "isrunning" { if (Running-Demo) { exit 1 } else { exit 0 } }
    "start" {
        Remove-Item (Stop-Flag) -ErrorAction SilentlyContinue
        if (Running-Demo) { Say "The demo is already running." Green; break }
        $t = Get-ScheduledTask -TaskName $TASK -ErrorAction SilentlyContinue
        if (-not $t) { Say "The demo is not installed - run scripts\install_autotrade_task.bat." Red; break }
        if ($t.State -eq "Disabled") { Enable-ScheduledTask -TaskName $TASK | Out-Null }
        Start-ScheduledTask -TaskName $TASK
        Start-Sleep 5
        if (Running-Demo) { Say "Started. Before 16:30 Israel it waits for the US open; after 23:00 it stops by itself." Green }
        else { Say "It started and already finished (weekend, or the day is already done). Look at '1 Is the demo running'." Yellow }
    }
    "stop" {
        New-Item -ItemType File -Force (Stop-Flag) | Out-Null       # so the 10-minute restart doesn't start it again today
        $p = Running-Demo
        Stop-ScheduledTask -TaskName $TASK -ErrorAction SilentlyContinue
        $p | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        if ($p) { Say "Stopped for today. Nothing is lost (it saves every minute)." Green }
        else { Say "It wasn't running - and it won't start again today." }
        Say "It starts by itself next weekday. Start it again today: '2 Start the demo now'. No more automatic days: '4 Demo OFF'."
    }
    "off" {
        $p = Running-Demo
        Stop-ScheduledTask -TaskName $TASK -ErrorAction SilentlyContinue
        $p | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        Disable-ScheduledTask -TaskName $TASK -ErrorAction SilentlyContinue | Out-Null
        Disable-ScheduledTask -TaskName $PICS -ErrorAction SilentlyContinue | Out-Null
        Say "Demo OFF: it won't start by itself any more (the paper case is kept). Turn it back on with '5 Demo ON'." Yellow
    }
    "on" {
        Remove-Item (Stop-Flag) -ErrorAction SilentlyContinue
        Enable-ScheduledTask -TaskName $TASK -ErrorAction SilentlyContinue | Out-Null
        Enable-ScheduledTask -TaskName $PICS -ErrorAction SilentlyContinue | Out-Null
        if (Get-ScheduledTask -TaskName $TASK -ErrorAction SilentlyContinue) { Say "Demo ON: it starts by itself every Monday-Friday at 15:15." Green }
        else { Say "The demo is not installed - run scripts\install_autotrade_task.bat." Red }
    }
    "trades" {
        $live = Join-Path $root "reports\trade_book\LIVE\index.html"
        if (Test-Path $live) { Start-Process $live }
        else { Say "No demo trade pictures yet - a picture appears the moment the demo takes its first trade." Yellow
               Say "Meanwhile: '9 Past trades 2019-2026' shows every past trade." }
    }
    "orders" {
        if (Test-Path $orders) { Start-Process $orders } else { Say "No orders yet - the first demo day hasn't traded." Yellow }
    }
    "past" {
        $idx = Join-Path $home_ "Past trades 2019-2026\index.html"
        if (Test-Path $idx) { Start-Process $idx } else { Say "Not built yet - run: python scripts\trade_book.py" Yellow }
    }
}
