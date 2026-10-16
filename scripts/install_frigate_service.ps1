<#
.SYNOPSIS
    Install, configure, start, stop or remove the Frigate Windows SCM service.

.DESCRIPTION
    Registers FrigateService (frigate.util.windows_service) with the Windows
    Service Control Manager through pywin32, and supplies the three things a
    service needs that a process started from a desktop shell gets for free.

    The service account
        The service runs as NT AUTHORITY\LocalService, not as the user who
        installed it, so it cannot read that user's profile. Everything it has
        to reach - the checkout, the interpreter and the data directory - is
        checked to be outside a user profile, and it is told to fail loudly
        rather than start a service that will not manage.

    The data directory
        A service does not inherit the installing user's environment, so
        LOCALAPPDATA belongs to the service account and Frigate would keep the
        config and the recordings in the service profile. This resolves the
        directory once, creates it, gives the service account access to it, and
        pins it in the service's own environment as FRIGATE_BASE_DIR (along
        with other FRIGATE_* variables, `PLUS_API_KEY`, and the explicit go2rtc
        exec opt-in from this session, so startup settings carry over).

    Recovery
        The service reports a failure when Frigate dies, but the SCM does
        nothing about a failed service unless it is told to. This configures
        the recovery actions that turn a failure into a restart 30 seconds
        later, three times a day, and sets the flag that makes a non-zero exit
        count as a failure in the first place.

    Run it from an elevated PowerShell prompt. Install, Configure and Remove
    change machine state; Status needs no privileges.

.PARAMETER Action
    Install    register the service, then configure it and start it
    Configure  re-apply the data directory and recovery settings, restarting
               the service if it is running
    Start      start the service
    Stop       stop the service
    Remove     stop and unregister the service
    Status     show what is registered and how it is configured

.PARAMETER BaseDir
    Where the service keeps config, media, cache and tmp. Defaults to
    FRIGATE_BASE_DIR when it is set, otherwise %ProgramData%\Frigate. It has to
    be somewhere the service account can reach, which a user profile is not.

.PARAMETER PythonPath
    The interpreter that has Frigate and pywin32 installed. Defaults to the
    repository's virtual environment, then to python on PATH.

.PARAMETER SkipAccessCheck
    Do not check that the service account can reach the checkout, the
    interpreter and the data directory. Only for an install whose permissions
    have been arranged by hand.

.EXAMPLE
    .\install_frigate_service.ps1 -Action Install

.EXAMPLE
    .\install_frigate_service.ps1 -Action Configure -BaseDir D:\Frigate
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Install", "Configure", "Start", "Stop", "Remove", "Status")]
    [string]$Action,

    [string]$BaseDir,

    [string]$PythonPath,

    [switch]$SkipAccessCheck
)

$serviceName = "Frigate"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
# the account, and its SID so a non-English Windows works too
$serviceAccount = "NT AUTHORITY\LocalService"
$serviceAccountSid = "*S-1-5-19"

function Get-ServiceKey {
    return "HKLM:\SYSTEM\CurrentControlSet\Services\$serviceName"
}

function Test-ServiceRegistered {
    return [bool](Get-Service -Name $serviceName -ErrorAction SilentlyContinue)
}

function Resolve-Python {
    if ($PythonPath) {
        if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
            throw "The interpreter '$PythonPath' does not exist."
        }
        return (Resolve-Path -LiteralPath $PythonPath).Path
    }

    foreach ($candidate in @(
            (Join-Path $repoRoot ".venv\Scripts\python.exe"),
            (Join-Path $repoRoot "venv\Scripts\python.exe")
        )) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }

    $command = Get-Command python -ErrorAction SilentlyContinue
    if (-not $command) {
        throw "Python was not found. Pass -PythonPath with the interpreter Frigate is installed in."
    }
    return $command.Source
}

function Resolve-BaseDirectory {
    if ($BaseDir) {
        return [System.IO.Path]::GetFullPath($BaseDir)
    }
    if ($env:FRIGATE_BASE_DIR) {
        return [System.IO.Path]::GetFullPath($env:FRIGATE_BASE_DIR)
    }
    return [System.IO.Path]::GetFullPath((Join-Path $env:ProgramData "Frigate"))
}

function Test-InUserProfile {
    param([string]$Path)

    $full = [System.IO.Path]::GetFullPath($Path)
    $roots = @()
    if ($env:USERPROFILE) { $roots += $env:USERPROFILE }
    $roots += (Join-Path $env:SystemDrive "Users")

    foreach ($root in $roots) {
        $prefix = $root.TrimEnd("\") + "\"
        if ($full.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            return $true
        }
    }
    return $false
}

function Assert-ServiceAccountCanReach {
    param([string]$Path, [string]$What)

    if ($SkipAccessCheck) { return }

    if (Test-InUserProfile -Path $Path) {
        throw @"
$What is inside a user profile:
    $Path

The service runs as $serviceAccount, and Windows does not let one account read
another account's profile, so it could not reach this. Either move it somewhere
shared (C:\Frigate and D:\Frigate are common choices; pass -BaseDir for the data
directory), or keep running Frigate from the launcher instead of as a service,
which runs as you. Pass -SkipAccessCheck if you have already granted the
service account access.
"@
    }
}

function Test-FrigateImportable {
    param([string]$Python)

    Push-Location $repoRoot
    try {
        & $Python -c "import frigate.util.windows_service" 2>&1 | Out-Null
        return ($LASTEXITCODE -eq 0)
    }
    finally {
        Pop-Location
    }
}

function Get-PythonHint {
    # only used to print a command the user can run, so never fail over it
    try { return Resolve-Python } catch { return "python" }
}

function Test-PyWin32 {
    param([string]$Python)

    # probe the modules the service imports: pywin32 is a distribution name,
    # there is no top-level `pywin32` module to import
    $result = & $Python -c "import servicemanager, win32event, win32service, win32serviceutil; print('ok')" 2>$null
    return ($result -eq "ok")
}

function Install-PyWin32 {
    param([string]$Python)

    if (Test-PyWin32 -Python $Python) { return }

    Write-Host "pywin32 is missing from $Python; installing it (it is part of requirements.txt)..."
    & $Python -m pip install -r (Join-Path $repoRoot "requirements.txt")
    if ($LASTEXITCODE -ne 0) {
        throw "Installing the Windows requirements failed (exit $LASTEXITCODE)."
    }
    if (-not (Test-PyWin32 -Python $Python)) {
        throw "pywin32 is still missing from $Python."
    }
}

function Initialize-BaseDirectory {
    param([string]$Path)

    foreach ($name in @("", "config", "media", "cache", "tmp")) {
        $directory = if ($name) { Join-Path $Path $name } else { $Path }
        New-Item -ItemType Directory -Force -Path $directory | Out-Null
    }

    # the service account has to be able to write the recordings there, and new
    # directories below this one inherit the grant
    $output = & icacls $Path /grant "${serviceAccountSid}:(OI)(CI)M" /T /C /Q 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Could not give $serviceAccount access to $Path (exit $LASTEXITCODE):`n$output"
    }

    Write-Host "Data directory:   $Path"
}

function Format-ServiceEnvironment {
    param([string[]]$Variables)

    return (($Variables | ForEach-Object {
                $name, $value = $_ -split "=", 2
                if ($name -eq "FRIGATE_BASE_DIR") {
                    "$name=$value"
                }
                else {
                    "$name=<set>"
                }
            }) -join "; ")
}

function Set-ServiceEnvironment {
    param([string]$Path)

    $variables = @("FRIGATE_BASE_DIR=$Path")

    # the rest of this session's Frigate configuration describes this install
    # too, so the service gets the same overrides (minus the two variables the
    # service itself sets for the process it starts)
    $skip = @("FRIGATE_BASE_DIR", "FRIGATE_SERVICE_MODE", "FRIGATE_SERVICE_STOP_EVENT")
    $configured = Get-ChildItem Env: |
        Where-Object {
            ($_.Name -like "FRIGATE_*" -or $_.Name -in @("PLUS_API_KEY", "GO2RTC_ALLOW_ARBITRARY_EXEC")) -and
            $skip -notcontains $_.Name
        } |
        Sort-Object Name

    foreach ($variable in $configured) {
        if ($variable.Value) {
            $variables += ("{0}={1}" -f $variable.Name, $variable.Value)
        }
    }

    New-ItemProperty -Path (Get-ServiceKey) -Name Environment -PropertyType MultiString `
        -Value $variables -Force | Out-Null

    Write-Host "Service env:      $(Format-ServiceEnvironment -Variables $variables)"
}

function Set-ServiceRecovery {
    $failure = & sc.exe failure $serviceName reset= 86400 actions= restart/30000/restart/30000/restart/30000 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Could not set the recovery actions (exit $LASTEXITCODE):`n$failure"
    }

    # without this, the SCM does not treat a service that exits with a non-zero
    # code as failed, so the actions above would never run
    $flag = & sc.exe failureflag $serviceName 1 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Could not set the failure flag (exit $LASTEXITCODE):`n$flag"
    }

    Write-Host "Recovery:         restart 30s after a failure, three times a day"
}

function Invoke-FrigateServiceCommand {
    param([string]$Python, [string[]]$Arguments)

    Push-Location $repoRoot
    try {
        & $Python -m frigate.util.windows_service @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "frigate.util.windows_service $($Arguments -join ' ') failed (exit $LASTEXITCODE)."
        }
    }
    finally {
        Pop-Location
    }
}

function Start-FrigateService {
    param([string]$Python)

    try {
        Start-Service -Name $serviceName -ErrorAction Stop
        Write-Host "Service '$serviceName' started."
    }
    catch {
        Write-Host ""
        Write-Host "The service was registered but did not start: $($_.Exception.Message)"
        Write-Host "Run it in a console to see why. It needs no privileges:"
        Write-Host ""
        Write-Host "    cd $repoRoot"
        Write-Host "    & '$Python' -m frigate.util.windows_service debug"
        Write-Host ""
        exit 1
    }
}

function Invoke-ServiceAction {
    switch ($Action) {
        "Status" {
            if (-not (Test-ServiceRegistered)) {
                Write-Host "Service '$serviceName' is not registered."
                Write-Host "Install it with: .\install_frigate_service.ps1 -Action Install"
                return
            }

            $service = Get-Service -Name $serviceName
            Write-Host "Status:           $($service.Status)"
            Write-Host "Start type:       $($service.StartType)"

            $environment = (Get-ItemProperty -Path (Get-ServiceKey) -Name Environment `
                    -ErrorAction SilentlyContinue).Environment
            if ($environment) {
                Write-Host "Service env:      $(Format-ServiceEnvironment -Variables $environment)"
            }
            else {
                Write-Host "Service env:      not set, so Frigate will use the service account's"
                Write-Host "                  own directory. Run -Action Configure to pin it."
            }
            return
        }

        "Remove" {
            if (-not (Test-ServiceRegistered)) {
                Write-Host "Service '$serviceName' is not registered."
                return
            }

            Stop-Service -Name $serviceName -Force -ErrorAction SilentlyContinue
            $removed = & sc.exe delete $serviceName 2>&1
            if ($LASTEXITCODE -ne 0) {
                throw "Could not remove the service (exit $LASTEXITCODE):`n$removed"
            }
            Write-Host "Service '$serviceName' removed."
            return
        }

        "Install" {
            if (Test-ServiceRegistered) {
                throw "The '$serviceName' service is already installed. Use -Action Configure to change its settings, or -Action Remove first."
            }

            $python = Resolve-Python
            $base = Resolve-BaseDirectory

            Write-Host "Interpreter:      $python"
            Write-Host "Checkout:         $repoRoot"
            Assert-ServiceAccountCanReach -Path $python -What "The interpreter"
            Assert-ServiceAccountCanReach -Path $repoRoot -What "The Frigate checkout"
            Assert-ServiceAccountCanReach -Path $base -What "The data directory"

            if (-not $BaseDir -and -not $env:FRIGATE_BASE_DIR) {
                $userTree = Join-Path $env:LOCALAPPDATA "Frigate"
                if ((Test-Path -LiteralPath $userTree) -and ($userTree -ne $base)) {
                    Write-Host ""
                    Write-Host "Note: this user's own Frigate install uses:"
                    Write-Host "          $userTree"
                    Write-Host "      the service will use:"
                    Write-Host "          $base"
                    Write-Host "      A service cannot read a user profile, so sharing one tree means"
                    Write-Host "      moving it outside one (for example D:\Frigate) and passing"
                    Write-Host "      -BaseDir there."
                    Write-Host ""
                }
            }

            Install-PyWin32 -Python $python

            if (-not (Test-FrigateImportable -Python $python)) {
                throw "$python cannot import frigate.util.windows_service from $repoRoot. Pass -PythonPath with the interpreter Frigate is installed in (the repository's own virtual environment by default)."
            }

            Initialize-BaseDirectory -Path $base

            # pywin32's own installer registers the service with its
            # pythonservice.exe host and records the class in the registry, and
            # takes the name, display name and description from that class.
            # The options have to come before the command: pywin32 reads them
            # with getopt, which stops at the first non-option argument.
            Invoke-FrigateServiceCommand -Python $python -Arguments @(
                "--startup", "delayed",
                "--username", $serviceAccount,
                "install"
            )

            Set-ServiceEnvironment -Path $base
            Set-ServiceRecovery
            Start-FrigateService -Python $python
            return
        }

        "Configure" {
            if (-not (Test-ServiceRegistered)) {
                throw "The '$serviceName' service is not installed. Use -Action Install first."
            }

            $base = Resolve-BaseDirectory
            Assert-ServiceAccountCanReach -Path $base -What "The data directory"
            Initialize-BaseDirectory -Path $base

            $wasRunning = (Get-Service -Name $serviceName).Status -eq "Running"
            if ($wasRunning) {
                Write-Host "Stopping the service so the new environment applies..."
                Stop-Service -Name $serviceName -Force
            }

            Set-ServiceEnvironment -Path $base
            Set-ServiceRecovery

            if ($wasRunning) {
                Start-FrigateService -Python (Get-PythonHint)
            }
            else {
                Write-Host "Service '$serviceName' configured. Start it with -Action Start."
            }
            return
        }

        "Start" {
            if (-not (Test-ServiceRegistered)) {
                throw "The '$serviceName' service is not installed. Use -Action Install first."
            }
            Start-FrigateService -Python (Get-PythonHint)
            return
        }

        "Stop" {
            if (-not (Test-ServiceRegistered)) {
                throw "The '$serviceName' service is not installed."
            }
            Write-Host "Stopping service '$serviceName'; Frigate is given 20 seconds to shut down cleanly."
            Stop-Service -Name $serviceName -Force -ErrorAction SilentlyContinue
            Write-Host "Service stopped."
            return
        }
    }
}

try {
    Invoke-ServiceAction
}
catch {
    Write-Host ""
    Write-Host "ERROR: $($_.Exception.Message)"
    exit 1
}
