# Wrapper called by the scheduled task.
# Lives in its own file (rather than inline in setup_scheduler.ps1) so we
# don't fight the quoting hell of -Command "...nested quotes...".

[CmdletBinding()]
param(
    [int]$Limit = 50
)

$ErrorActionPreference = "Continue"   # don't bail on first error — capture everything
$BackendDir = $PSScriptRoot
$Venv       = Join-Path $BackendDir ".venv\Scripts\python.exe"
$LogFile    = Join-Path $BackendDir "extract_scheduled.log"

Set-Location $BackendDir

# Timestamp helper
function Stamp { Get-Date -Format 'yyyy-MM-dd HH:mm:ss' }

# Append a START marker
"[$(Stamp)] === extract --limit $Limit START ===" | Out-File -FilePath $LogFile -Append -Encoding utf8

if (-not (Test-Path $Venv)) {
    "[$(Stamp)] FATAL: venv not found at $Venv" | Out-File -FilePath $LogFile -Append -Encoding utf8
    "[$(Stamp)] === END (exit 2) ===" | Out-File -FilePath $LogFile -Append -Encoding utf8
    exit 2
}

# Run the extract command; pipe every stream into the log.
# *>&1 collects stdout+stderr+info+verbose+warning+debug; we then append.
$output = & $Venv -m tasks.run extract --limit $Limit *>&1
$exit   = $LASTEXITCODE

# Each element of $output is a string or an ErrorRecord — coerce to string
$output | ForEach-Object { $_.ToString() } | Out-File -FilePath $LogFile -Append -Encoding utf8

"[$(Stamp)] === END (exit $exit) ===" | Out-File -FilePath $LogFile -Append -Encoding utf8
exit $exit
