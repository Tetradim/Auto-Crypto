# Auto-Crypto Launcher
# Runs the local source checkout with persistent paper-mode SQLite storage.

param(
    [int]$Port = 8004,
    [int]$FrontendPort = 3004,
    [string]$HostName = "127.0.0.1",
    [string]$DbPath = "",
    [switch]$NoBrowser,
    [switch]$InstallDeps,
    [switch]$ExchangeDeps,
    [switch]$StartDiscord,
    [switch]$SmokeTest
)

$ErrorActionPreference = "Stop"
$ProjectRoot = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
if (-not $ProjectRoot) { $ProjectRoot = (Get-Location).Path }

$DesktopPath = [Environment]::GetFolderPath("Desktop")
if (-not $DesktopPath) { $DesktopPath = Join-Path $HOME "Desktop" }
$LogFile = Join-Path $DesktopPath "Auto-Crypto.log"
$OwnedProcesses = New-Object System.Collections.Generic.List[System.Diagnostics.Process]
$ShutdownStarted = $false
$CancelKeyPressHandler = $null

function Write-Status {
    param([string]$Message, [string]$Level = "INFO")
    $color = switch ($Level) {
        "OK" { "Green" }
        "WARN" { "Yellow" }
        "ERROR" { "Red" }
        default { "Cyan" }
    }
    Write-Host "[$Level] $Message" -ForegroundColor $color
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss.fff"
    Add-Content -Path $LogFile -Value "$timestamp [$Level] $Message" -Encoding UTF8
}

function Join-ProcessArguments {
    param([string[]]$Arguments)
    return (($Arguments | ForEach-Object {
        $arg = $_
        if ([string]::IsNullOrEmpty($arg)) {
            '""'
        } elseif ($arg -match '[\s"]') {
            '"' + $arg.Replace('"', '\"') + '"'
        } else {
            $arg
        }
    }) -join " ")
}

function Test-PortOpen {
    param([int]$PortToCheck)
    try {
        $client = New-Object Net.Sockets.TcpClient
        $async = $client.BeginConnect("127.0.0.1", $PortToCheck, $null, $null)
        $connected = $async.AsyncWaitHandle.WaitOne(750, $false)
        if ($connected) { $client.EndConnect($async) }
        $client.Close()
        return $connected
    } catch {
        return $false
    }
}

function Test-AutoCryptoHealth {
    param([int]$PortToCheck)
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$PortToCheck/health" -Method Get -TimeoutSec 3
        return ($health.status -eq "ok" -and $health.default_mode -eq "paper")
    } catch {
        return $false
    }
}

function Wait-AutoCryptoHealth {
    param([int]$PortToCheck, [int]$Seconds = 45)
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-AutoCryptoHealth -PortToCheck $PortToCheck) { return $true }
        Start-Sleep -Milliseconds 750
    }
    return $false
}

function Get-PythonVersion {
    param(
        [string]$FilePath,
        [string[]]$ArgumentPrefix = @()
    )
    try {
        $args = @($ArgumentPrefix + @("-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"))
        $version = (& $FilePath @args 2>$null | Select-Object -First 1)
        if (-not $version) { return $null }
        return [version]$version
    } catch {
        return $null
    }
}

function Test-CompatiblePythonVersion {
    param([version]$Version)
    return $Version -and $Version.Major -eq 3 -and $Version.Minor -ge 11
}

function Find-CompatiblePython {
    $candidates = New-Object System.Collections.Generic.List[object]
    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($py) {
        foreach ($selector in @("-3.13", "-3.12", "-3.11")) {
            $candidates.Add([pscustomobject]@{
                FilePath = $py.Source
                ArgumentPrefix = @($selector)
                Label = "py $selector"
            })
        }
    }

    foreach ($name in @("python3.13.exe", "python3.12.exe", "python3.11.exe", "python.exe")) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) {
            $candidates.Add([pscustomobject]@{
                FilePath = $cmd.Source
                ArgumentPrefix = @()
                Label = $cmd.Source
            })
        }
    }

    foreach ($candidate in $candidates) {
        $version = Get-PythonVersion -FilePath $candidate.FilePath -ArgumentPrefix $candidate.ArgumentPrefix
        if (Test-CompatiblePythonVersion -Version $version) {
            return [pscustomobject]@{
                FilePath = $candidate.FilePath
                ArgumentPrefix = $candidate.ArgumentPrefix
                Version = $version
                Label = $candidate.Label
            }
        }
    }
    return $null
}

function Invoke-CompatiblePython {
    param(
        [object]$PythonInfo,
        [string[]]$Arguments
    )
    $fullArgs = @($PythonInfo.ArgumentPrefix + $Arguments)
    & $PythonInfo.FilePath @fullArgs
}

function Start-OwnedProcess {
    param(
        [string]$FilePath,
        [string[]]$ArgumentList,
        [string]$WorkingDirectory,
        [switch]$Visible
    )
    $startParams = @{
        FilePath = $FilePath
        WorkingDirectory = $WorkingDirectory
        PassThru = $true
    }
    if ($ArgumentList -and $ArgumentList.Count -gt 0) {
        $startParams.ArgumentList = Join-ProcessArguments -Arguments $ArgumentList
    }
    if (-not $Visible) {
        $startParams.WindowStyle = "Hidden"
    }
    $process = Start-Process @startParams
    $OwnedProcesses.Add($process)
    return $process
}

function Stop-ProcessTree {
    param([int]$ProcessId)
    try {
        $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId = $ProcessId" -ErrorAction SilentlyContinue)
        foreach ($child in $children) {
            Stop-ProcessTree -ProcessId $child.ProcessId
        }
        $current = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
        if ($current) {
            Write-Status "Stopping process $($current.ProcessName) ($($current.Id))"
            Stop-Process -Id $current.Id -Force -ErrorAction SilentlyContinue
        }
    } catch {
    }
}

function Stop-OwnedProcesses {
    for ($i = $OwnedProcesses.Count - 1; $i -ge 0; $i--) {
        $process = $OwnedProcesses[$i]
        Stop-ProcessTree -ProcessId $process.Id
    }
}

function Invoke-LauncherCleanup {
    if ($script:ShutdownStarted) { return }
    $script:ShutdownStarted = $true
    Stop-OwnedProcesses
}

function Register-LauncherShutdownHandlers {
    try {
        $script:CancelKeyPressHandler = [ConsoleCancelEventHandler]{
            param($sender, $eventArgs)
            $eventArgs.Cancel = $true
            Write-Status "Shutdown requested; stopping Auto-Crypto" "WARN"
            Invoke-LauncherCleanup
            exit 0
        }
        [Console]::CancelKeyPress += $script:CancelKeyPressHandler
    } catch {
    }
}

if ($SmokeTest) {
    Write-Status "Running launcher smoke test"
    $quoted = Join-ProcessArguments -Arguments @("-m", "uvicorn", "autocrypto.app:create_app_from_env", "--factory", "--port", "8004")
    if (-not $quoted.Contains("autocrypto.app:create_app_from_env")) {
        throw "Argument joining smoke test failed."
    }
    $spaced = Join-ProcessArguments -Arguments @("--db", "C:\Users\Lite OS\Desktop\auto crypto.sqlite3")
    if (-not $spaced.Contains('"C:\Users\Lite OS\Desktop\auto crypto.sqlite3"')) {
        throw "Spaced argument quoting smoke test failed."
    }
    if (-not (Get-Command Start-Process -ErrorAction SilentlyContinue)) {
        throw "Start-Process is unavailable."
    }
    Write-Status "Launcher smoke test passed" "OK"
    exit 0
}

Register-LauncherShutdownHandlers

try {
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host "  Auto-Crypto Launcher" -ForegroundColor Cyan
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Status "Project root: $ProjectRoot"
    Write-Status "Launcher log: $LogFile"

    if (-not (Test-Path (Join-Path $ProjectRoot "pyproject.toml"))) {
        throw "pyproject.toml not found. Run this launcher from the Auto-Crypto checkout."
    }

    if (-not $DbPath) {
        $DbPath = Join-Path $ProjectRoot "data\auto_crypto.sqlite3"
    }
    New-Item -ItemType Directory -Path (Split-Path -Parent $DbPath) -Force | Out-Null

    $venvPath = Join-Path $ProjectRoot ".venv"
    $venvPython = Join-Path $venvPath "Scripts\python.exe"
    if (-not (Test-Path $venvPython)) {
        $pythonInfo = Find-CompatiblePython
        if (-not $pythonInfo) {
            throw "Python 3.11+ was not found. Install Python 3.11 or newer and rerun."
        }
        Write-Status "Creating virtual environment with $($pythonInfo.Label) ($($pythonInfo.Version))"
        Invoke-CompatiblePython -PythonInfo $pythonInfo -Arguments @("-m", "venv", $venvPath)
        $InstallDeps = $true
    }

    if ($InstallDeps) {
        $installTarget = if ($ExchangeDeps) { ".[exchange]" } else { "." }
        Write-Status "Installing Auto-Crypto dependencies ($installTarget)"
        & $venvPython -m pip install --upgrade pip
        & $venvPython -m pip install -e $installTarget
    }

    $env:AUTO_CRYPTO_HOST = $HostName
    $env:AUTO_CRYPTO_PORT = "$Port"
    $env:AUTO_CRYPTO_DB_PATH = $DbPath
    if (-not $env:AUTO_CRYPTO_ALLOWED_EXCHANGES) {
        $env:AUTO_CRYPTO_ALLOWED_EXCHANGES = "paper"
    }

    $healthUrl = "http://127.0.0.1:$Port/health"
    $docsUrl = "http://127.0.0.1:$Port/docs"

    if (Test-PortOpen -PortToCheck $Port) {
        if (-not (Test-AutoCryptoHealth -PortToCheck $Port)) {
            throw "Port $Port is already in use by another service. Stop that service or pass -Port <free port>."
        }
        Write-Status "Auto-Crypto is already running on port $Port" "WARN"
    } else {
        Write-Status "Starting Auto-Crypto API on $HostName`:$Port"
        Start-OwnedProcess -FilePath $venvPython -ArgumentList @("-m", "uvicorn", "autocrypto.app:create_app_from_env", "--factory", "--host", $HostName, "--port", "$Port") -WorkingDirectory $ProjectRoot | Out-Null
        if (-not (Wait-AutoCryptoHealth -PortToCheck $Port -Seconds 60)) {
            throw "Auto-Crypto did not become healthy at $healthUrl. Check $LogFile."
        }
        Write-Status "Auto-Crypto API is healthy" "OK"
    }

    if ($StartDiscord) {
        if (-not $env:DISCORD_BOT_TOKEN) {
            throw "DISCORD_BOT_TOKEN is required when using -StartDiscord."
        }
        Write-Status "Starting Discord slash-command bot"
        Start-OwnedProcess -FilePath $venvPython -ArgumentList @("-c", "from autocrypto.discord_bot import run_from_env; run_from_env()") -WorkingDirectory $ProjectRoot | Out-Null
    }

    if (-not $NoBrowser) {
        Write-Status "Opening API docs"
        Start-Process $docsUrl | Out-Null
    }

    Write-Host ""
    Write-Host "Ready: $docsUrl" -ForegroundColor Green
    Write-Host "Health: $healthUrl" -ForegroundColor Gray
    Write-Host "Frontend port reserved: $FrontendPort" -ForegroundColor Gray
    Write-Host "Database: $DbPath" -ForegroundColor Gray
    if (-not $StartDiscord) {
        Write-Host "Discord bot: pass -StartDiscord after setting DISCORD_BOT_TOKEN." -ForegroundColor Gray
    }
    Write-Host "Close this window or press Ctrl+C to stop processes started by this launcher." -ForegroundColor Gray
    Write-Host ""

    while ($true) {
        foreach ($process in @($OwnedProcesses)) {
            if ($process.HasExited) {
                throw "Process $($process.Id) exited unexpectedly."
            }
        }
        Start-Sleep -Seconds 1
    }
} catch {
    Write-Status $_.Exception.Message "ERROR"
    exit 1
} finally {
    Invoke-LauncherCleanup
}
