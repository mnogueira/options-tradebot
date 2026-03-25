param(
    [string]$Python = "python",
    [string]$ConfigPath = "config/defined_risk_short_vol.toml",
    [string]$Mode = "paper-broker",
    [string]$Venues = "mt5,ib",
    [switch]$RunOnce,
    [string]$LogPath = "runtime/defined_risk_short_vol/session.log"
)

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$logTarget = Join-Path $repoRoot $LogPath
$logDirectory = Split-Path -Parent $logTarget
if (-not (Test-Path $logDirectory)) {
    New-Item -ItemType Directory -Path $logDirectory | Out-Null
}

$stderrTarget = "$logTarget.err"
$arguments = @(
    "-m", "options_tradebot.cli.main",
    "run-short-vol",
    "--config", $ConfigPath,
    "--mode", $Mode,
    "--venues", $Venues
)

if ($RunOnce) {
    $arguments += @("--run-once")
}

$process = Start-Process `
    -FilePath $Python `
    -ArgumentList $arguments `
    -WorkingDirectory $repoRoot `
    -RedirectStandardOutput $logTarget `
    -RedirectStandardError $stderrTarget `
    -PassThru

Write-Output ("Started defined-risk short-vol runtime with PID {0}" -f $process.Id)
Write-Output ("Stdout: {0}" -f $logTarget)
Write-Output ("Stderr: {0}" -f $stderrTarget)
