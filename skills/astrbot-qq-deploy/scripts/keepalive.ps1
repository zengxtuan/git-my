# AstrBot + NapCat keep-alive daemon
#
# Usage (started silently at logon via a .vbs in the Startup folder):
#   powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File keepalive.ps1 `
#       -AstrBotRoot "D:\AstrBot" -NapCatShell "D:\NapCat\NapCat.XXXX.Shell"
#
# Loops forever, checks every -IntervalSec seconds.
# Idempotent and safe to run multiple times.

param(
    [Parameter(Mandatory = $true)][string]$AstrBotRoot,
    [Parameter(Mandatory = $true)][string]$NapCatShell,
    [int]$IntervalSec = 300
)

$ErrorActionPreference = 'SilentlyContinue'

$LogDir = Join-Path $AstrBotRoot 'logs'
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }
$LogFile = Join-Path $LogDir 'autostart.log'

function Write-Log($msg) {
    try {
        $line = "{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
        Add-Content -Path $LogFile -Value $line -Encoding UTF8
    } catch { }
}

function Ensure-AstrBot {
    if (Get-Process astrbot -ErrorAction SilentlyContinue) { return $true }
    try {
        Start-Process -FilePath (Join-Path $AstrBotRoot '.venv\Scripts\astrbot.exe') `
            -ArgumentList 'run' -WorkingDirectory $AstrBotRoot `
            -RedirectStandardOutput (Join-Path $AstrBotRoot 'run.out.log') `
            -RedirectStandardError (Join-Path $AstrBotRoot 'run.err.log') `
            -WindowStyle Hidden
        Write-Log 'AstrBot STARTED'
        Start-Sleep -Seconds 10
        return $true
    } catch {
        Write-Log ("AstrBot start failed: " + $_.Exception.Message)
        return $false
    }
}

function Ensure-NapCat {
    if (Get-Process QQ -ErrorAction SilentlyContinue) { return $true }
    try {
        Start-Process -FilePath 'cmd.exe' `
            -ArgumentList '/c', "cd /d `"$NapCatShell`" && napcat.bat" `
            -WorkingDirectory $NapCatShell -WindowStyle Hidden
        Write-Log 'NapCat STARTED'
        Start-Sleep -Seconds 8
        return $true
    } catch {
        Write-Log ("NapCat start failed: " + $_.Exception.Message)
        return $false
    }
}

Write-Log '=== keeper started ==='
$lastAb = $null
$lastQq = $null

while ($true) {
    $abOk = Ensure-AstrBot
    $qqOk = Ensure-NapCat

    if ($abOk -ne $lastAb) { Write-Log ("AstrBot state -> " + $abOk); $lastAb = $abOk }
    if ($qqOk -ne $lastQq) { Write-Log ("NapCat state -> " + $qqOk);  $lastQq = $qqOk }

    if ($abOk -and -not (Get-NetTCPConnection -State Listen -LocalPort 6199 -ErrorAction SilentlyContinue)) {
        Write-Log 'WARN: 6199 not listening'
    }

    # keep the log bounded
    try {
        if ((Get-Item $LogFile).Length -gt 512000) {
            Set-Content -Path $LogFile -Value (Get-Content $LogFile -Tail 500) -Encoding UTF8
        }
    } catch { }

    Start-Sleep -Seconds $IntervalSec
}
