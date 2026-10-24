<#
.SYNOPSIS
  Start the media-streamer worker automatically when you sign in to Windows.

.DESCRIPTION
  Registers a Scheduled Task "media-worker" that runs the worker either natively
  (media-worker.exe installed with `uv tool install`) or inside a WSL distribution.
  For WSL the task keeps wsl.exe running, which also keeps the WSL VM alive.

  The token is stored in the task's arguments, visible to administrators of this PC.

.EXAMPLE
  .\install-task.ps1 -Server http://192.168.1.10:8000 -Token msw_... -Mode wsl -Distro Ubuntu
.EXAMPLE
  .\install-task.ps1 -Server http://192.168.1.10:8000 -Token msw_... -Mode native `
      -WorkerArgs "--detector ultralytics --model yolov8s-worldv2.pt"
.EXAMPLE
  .\install-task.ps1 -Uninstall
#>
param(
    [string]$Server,
    [string]$Token = "",
    [ValidateSet("wsl", "native")] [string]$Mode = "wsl",
    [string]$Distro = "Ubuntu",
    [string]$WorkerArgs = "--detector speciesnet",
    [switch]$Uninstall
)
$ErrorActionPreference = "Stop"
$TaskName = "media-worker"

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed scheduled task $TaskName."
    return
}
if (-not $Server) { throw "-Server is required, e.g. -Server http://192.168.1.10:8000" }

$tokenArg = if ($Token) { "--token $Token" } else { "" }
$workerCmd = "--server $Server $tokenArg $WorkerArgs".Trim()

if ($Mode -eq "wsl") {
    # Login shell so ~/.local/bin (uv tool install) is on PATH.
    $exe = "wsl.exe"
    $arguments = "-d $Distro -- bash -lc `"media-worker $workerCmd`""
} else {
    $exe = Join-Path $env:USERPROFILE ".local\bin\media-worker.exe"
    if (-not (Test-Path $exe)) {
        $found = Get-Command media-worker.exe -ErrorAction SilentlyContinue
        if (-not $found) { throw "media-worker.exe not found; install it with uv tool install first." }
        $exe = $found.Source
    }
    $arguments = $workerCmd
}

$action = New-ScheduledTaskAction -Execute $exe -Argument $arguments
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Description "media-streamer detection worker ($Mode)" -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Write-Host "Registered and started scheduled task '$TaskName' ($Mode)."
Write-Host "Check it in the server's Worker queue tab; stop with: Stop-ScheduledTask $TaskName"
