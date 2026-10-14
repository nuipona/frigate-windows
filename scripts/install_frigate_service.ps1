<#
.SYNOPSIS
    Install, start, stop, or remove the Frigate Windows SCM service.

.DESCRIPTION
    Uses pywin32 to register FrigateService (frigate.util.windows_service)
    with the Windows Service Control Manager. pywin32 is part of
    docker/main/requirements-windows.txt, so a bootstrap install already has
    it; this checks and installs it if the script is run on its own.

.PARAMETER Action
    Install, Start, Stop, Remove, Status

.EXAMPLE
    .\install_frigate_service.ps1 -Action Install
    .\install_frigate_service.ps1 -Action Start
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Install", "Start", "Stop", "Remove", "Status")]
    [string]$Action
)

$serviceName = "Frigate"
$pythonModule = "frigate.util.windows_service"

function Test-PyWin32 {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) { $python = Get-Command python3 -ErrorAction SilentlyContinue }
    if (-not $python) {
        Write-Error "Python not found in PATH."
        return $false
    }
    # probe the modules the service imports: pywin32 is a distribution name,
    # there is no top-level `pywin32` module to import
    $result = & $python.Source -c "import servicemanager, win32event, win32service, win32serviceutil; print('ok')" 2>$null
    return ($result -eq "ok")
}

function Invoke-ServiceAction {
    param([string]$Action)
    $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    $repoRoot = (Resolve-Path (Join-Path $scriptDir "..")).Path

    Write-Host "Action: $Action for service '$serviceName'"
    Write-Host "Repo root: $repoRoot"

    switch ($Action) {
        "Status" {
            $svc = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
            if ($svc) {
                Write-Host "Status: $($svc.Status)"
            } else {
                Write-Host "Service '$serviceName' not registered."
            }
        }
        "Install" {
            if (-not (Test-PyWin32)) {
                Write-Host "pywin32 is missing; installing it (it is part of docker/main/requirements-windows.txt)..."
                & python -m pip install -r (Join-Path $repoRoot "docker/main/requirements-windows.txt")
            }
            $env:PYTHONPATH = "$repoRoot;$env:PYTHONPATH"
            & python -c "
import sys, os
sys.path.insert(0, '$repoRoot')
from frigate.util.windows_service import FrigateService
import win32serviceutil
win32serviceutil.InstallService(
    FrigateService,
    serviceName='$serviceName',
    displayName='Frigate NVR (Windows Native)',
    description='Realtime object detection NVR for IP cameras — native Windows port',
    startType=win32serviceutil.SERVICE_AUTO_START,
    interactive=False,
    username='NT AUTHORITY\LocalService',
    password=''
)
"
            Write-Host "Service installed. Use 'Start' to begin."
        }
        "Start" {
            Start-Service -Name $serviceName -ErrorAction Stop
            Write-Host "Service started."
        }
        "Stop" {
            Stop-Service -Name $serviceName -Force -ErrorAction SilentlyContinue
            Write-Host "Service stopped."
        }
        "Remove" {
            Stop-Service -Name $serviceName -Force -ErrorAction SilentlyContinue
            & python -c "
import sys
sys.path.insert(0, '$repoRoot')
from frigate.util.windows_service import FrigateService
import win32serviceutil
win32serviceutil.RemoveService('$serviceName')
"
            Write-Host "Service removed."
        }
    }
}

Invoke-ServiceAction -Action $Action
