# Installs a Windows Task Scheduler task that runs the extract pipeline
# hourly while you are logged on.
#
# Usage:
#   cd D:\claude_code\news-credibility\backend
#   .\setup_scheduler.ps1                    # install with defaults
#   .\setup_scheduler.ps1 -Limit 100         # process 100 articles per run
#   .\setup_scheduler.ps1 -IntervalMinutes 30 # fire every 30 min
#   .\setup_scheduler.ps1 -Uninstall          # remove the task
#
# Why "only while logged on"?
#   The extract step shells out to the `claude` CLI, which uses your
#   Claude Pro/Max OAuth session. That session lives in your user
#   profile and only exists while you're logged in. Running as SYSTEM
#   or a different user would fail with no auth token.

[CmdletBinding()]
param(
    [int]   $Limit           = 50,
    [int]   $IntervalMinutes = 60,
    [string]$TaskName        = "NewsCredibility-Extract",
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"

# --- Paths (self-detected) -------------------------------------------
$BackendDir = $PSScriptRoot                                  # this script lives in backend/
$Venv       = Join-Path $BackendDir ".venv\Scripts\python.exe"
$LogFile    = Join-Path $BackendDir "extract_scheduled.log"

# --- Uninstall path ---------------------------------------------------
if ($Uninstall) {
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existing) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Removed scheduled task: $TaskName" -ForegroundColor Yellow
    } else {
        Write-Host "No task named $TaskName found." -ForegroundColor Gray
    }
    return
}

# --- Sanity checks ---------------------------------------------------
if (-not (Test-Path $Venv)) {
    throw "venv not found at $Venv`n" +
          "Run first: cd $BackendDir; python -m venv .venv; .\.venv\Scripts\Activate.ps1; pip install -r requirements.txt"
}
$ClaudeCmd = Get-Command claude -ErrorAction SilentlyContinue
if (-not $ClaudeCmd) {
    Write-Warning "claude CLI not found on PATH. The task will install but every run will fail."
    Write-Warning "Install Claude Code first, then `claude auth` to log in."
}

# --- Action: powershell wrapping our pipeline command ----------------
# We launch powershell.exe with the entire command on a single -Command
# line. *>> appends every output stream (stdout/stderr/info/verbose) to
# the log so partial runs are visible.
$RunCmd = @"
Set-Location '$BackendDir';
'[' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + '] === extract --limit $Limit START ===' >> '$LogFile';
& '$Venv' -m tasks.run extract --limit $Limit *>> '$LogFile';
'[' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + "] === END (exit `$LASTEXITCODE) ===" >> '$LogFile'
"@

$Action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -Command `"$RunCmd`""

# --- Trigger: every N minutes, indefinite ---------------------------
# Start 2 minutes from now so you can verify the install before it fires.
$Trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2)
$Trigger.Repetition = (New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
    -RepetitionDuration (New-TimeSpan -Days 3650)).Repetition

# --- Principal: current user, only when logged on -------------------
$Principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited

# --- Settings -------------------------------------------------------
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)

# --- Idempotent: drop existing task with the same name --------------
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Replaced existing task: $TaskName" -ForegroundColor Gray
}

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Principal $Principal `
    -Settings $Settings `
    -Description "news-credibility: hourly extract pipeline via Claude Code OAuth" | Out-Null

# --- Summary --------------------------------------------------------
Write-Host ""
Write-Host "+--------------------------------------------------------+" -ForegroundColor Green
Write-Host "| Scheduled task installed: $TaskName" -ForegroundColor Green
Write-Host "+--------------------------------------------------------+" -ForegroundColor Green
Write-Host "  Fires every $IntervalMinutes min while you are logged on"
Write-Host "  Processes up to $Limit articles per run"
Write-Host "  First run:  $((Get-Date).AddMinutes(2).ToString('yyyy-MM-dd HH:mm:ss'))"
Write-Host "  Log file:   $LogFile"
Write-Host ""
Write-Host "Useful commands:" -ForegroundColor Cyan
Write-Host "  Manually trigger now      :  Start-ScheduledTask -TaskName $TaskName"
Write-Host "  Check last run            :  Get-ScheduledTaskInfo -TaskName $TaskName"
Write-Host "  Disable temporarily       :  Disable-ScheduledTask -TaskName $TaskName"
Write-Host "  Re-enable                 :  Enable-ScheduledTask  -TaskName $TaskName"
Write-Host "  Watch log live            :  Get-Content '$LogFile' -Tail 20 -Wait"
Write-Host "  Remove the task           :  .\setup_scheduler.ps1 -Uninstall"
Write-Host ""
Write-Host "Heads-up:" -ForegroundColor Yellow
Write-Host "  - The task and any interactive Claude Code you run share the same"
Write-Host "    subscription quota; heavy concurrent use may rate-limit both."
Write-Host "  - If the scheduled run hits 15 consecutive errors it will early-stop"
Write-Host "    and try again on the next cycle (no quota burned in tight loops)."
