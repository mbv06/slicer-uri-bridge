#Requires -Version 5.1
<#
.SYNOPSIS
    Slicer URI Bridge Windows installer.

.DESCRIPTION
    1. Finds Python 3.11+
    2. Creates a private virtual environment
    3. Installs / upgrades Slicer URI Bridge into that environment
    4. Adds the environment Scripts directory to the user PATH
    5. Creates config if missing
    6. Registers URI handlers
    7. Shows how to test the registered handler

    To update later, run this installer again.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File install-windows.ps1
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ProjectSpec = 'https://github.com/mbv06/slicer-uri-bridge/archive/refs/heads/main.zip'
$AppHome     = Join-Path $env:LOCALAPPDATA 'slicer-uri-bridge'
$VenvDir     = Join-Path $AppHome 'venv'
$ScriptsDir  = Join-Path $VenvDir 'Scripts'
$BridgeExe   = Join-Path $ScriptsDir 'slicer-uri-bridge.exe'
$MinMajor    = 3
$MinMinor    = 11

function Log($Message) {
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Test-ShouldPauseOnExit {
    try {
        if (-not [Environment]::UserInteractive) { return $false }
        if ($Host.Name -ne 'ConsoleHost') { return $false }

        $commandLine = Get-CimInstance Win32_Process -Filter "ProcessId = $PID" -ErrorAction Stop |
                       Select-Object -ExpandProperty CommandLine
        if (-not $commandLine) { return $false }

        return $commandLine -match '(?i)(^|\s)-(c|command|file|encodedcommand)\b'
    }
    catch {
        return $false
    }
}

function Wait-BeforeExit {
    if (Test-ShouldPauseOnExit) {
        Write-Host ''
        Read-Host 'Press Enter to close this window' | Out-Null
    }
}

function Write-SummaryLine {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Label,

        [Parameter(Mandatory = $true)]
        [string]$Value
    )

    Write-Host ("  {0,-12}" -f $Label) -ForegroundColor Cyan -NoNewline
    Write-Host " $Value"
}

function Format-ArgumentForProcess {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Value
    )

    if ($Value -notmatch '[\s"]') {
        return $Value
    }

    return '"' + ($Value -replace '"', '\"') + '"'
}

function Format-CommandForDisplay {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,

        [Parameter()]
        [string[]]$Arguments = @()
    )

    $parts = @($FilePath) + @($Arguments)
    $quotedParts = foreach ($part in $parts) {
        if ($part -match '[\s"]') {
            '"' + ($part -replace '"', '\"') + '"'
        }
        else {
            $part
        }
    }

    return ($quotedParts -join ' ')
}

function Invoke-NativeCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,

        [Parameter()]
        [string[]]$Arguments = @(),

        [Parameter()]
        [string]$FailureMessage = 'Command failed.'
    )

    Write-Host ("Running: {0}" -f (Format-CommandForDisplay -FilePath $FilePath -Arguments $Arguments)) -ForegroundColor DarkGray

    $stdoutPath = [System.IO.Path]::GetTempFileName()
    $stderrPath = [System.IO.Path]::GetTempFileName()
    $argumentLine = (@($Arguments) | ForEach-Object { Format-ArgumentForProcess $_ }) -join ' '

    try {
        $process = Start-Process -FilePath $FilePath `
                                 -ArgumentList $argumentLine `
                                 -NoNewWindow `
                                 -Wait `
                                 -PassThru `
                                 -RedirectStandardOutput $stdoutPath `
                                 -RedirectStandardError $stderrPath

        $stdout = if (Test-Path -LiteralPath $stdoutPath) {
            @(Get-Content -LiteralPath $stdoutPath -ErrorAction SilentlyContinue)
        } else {
            @()
        }
        $stderr = if (Test-Path -LiteralPath $stderrPath) {
            @(Get-Content -LiteralPath $stderrPath -ErrorAction SilentlyContinue)
        } else {
            @()
        }

        $output = @($stdout + $stderr)
        $exitCode = $process.ExitCode
    }
    finally {
        Remove-Item -LiteralPath $stdoutPath, $stderrPath -Force -ErrorAction SilentlyContinue
    }

    if ($output) {
        foreach ($line in @($output)) {
            Write-Host $line
        }
    }

    if ($exitCode -ne 0) {
        Die $FailureMessage
    }

    return @($output)
}

function Die($Message) {
    Write-Host "Error: $Message" -ForegroundColor Red
    Wait-BeforeExit
    exit 1
}

function Test-PythonCompatible($PythonPath) {
    try {
        $version = & $PythonPath -c 'import sys; print(sys.version_info.major); print(sys.version_info.minor)' 2>$null
        if (-not $version) { return $false }
        $parts = @($version)
        $major = [int]$parts[0]
        $minor = [int]$parts[1]
        return ($major -gt $MinMajor) -or ($major -eq $MinMajor -and $minor -ge $MinMinor)
    }
    catch {
        return $false
    }
}

function Find-Python {
    $candidates = @(
        'python3.14', 'python3.13', 'python3.12', 'python3.11',
        'python3', 'python', 'py'
    )

    foreach ($candidate in $candidates) {
        $path = Get-Command $candidate -ErrorAction SilentlyContinue |
                Select-Object -ExpandProperty Source -ErrorAction SilentlyContinue
        if ($path -and (Test-PythonCompatible $path)) {
            return $path
        }
    }

    # Try the py launcher with version flags
    $pyLauncher = Get-Command 'py' -ErrorAction SilentlyContinue |
                  Select-Object -ExpandProperty Source -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        foreach ($ver in @('-3.14', '-3.13', '-3.12', '-3.11')) {
            try {
                $check = & $pyLauncher $ver -c 'import sys; print(sys.executable)' 2>$null
                if ($check -and (Test-PythonCompatible $check)) {
                    return $check
                }
            }
            catch {}
        }
    }

    return $null
}

function Add-ToUserPath($Directory) {
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    if (-not $userPath) { $userPath = '' }

    $entries = $userPath.Split(';', [StringSplitOptions]::RemoveEmptyEntries)
    $normalized = $entries | ForEach-Object { $_.TrimEnd('\') }
    $target = $Directory.TrimEnd('\')

    if ($normalized -contains $target) {
        return $false
    }

    $newPath = if ($userPath) { "$userPath;$Directory" } else { $Directory }
    [Environment]::SetEnvironmentVariable('Path', $newPath, 'User')

    # Update the current session so the command is immediately available
    if ($env:Path -notlike "*$target*") {
        $env:Path = "$env:Path;$Directory"
    }

    return $true
}

function Main {
    Log 'Checking Python 3.11+'
    $python = Find-Python
    if (-not $python) {
        $winget = Get-Command 'winget' -ErrorAction SilentlyContinue |
                  Select-Object -ExpandProperty Source -ErrorAction SilentlyContinue
        if ($winget) {
            Die @"
Python $MinMajor.$MinMinor+ was not found.

Install Python by running:

  winget install Python.Python.3.12

Then open a new terminal window and run this installer again.
"@
        }

        Die @"
Python $MinMajor.$MinMinor+ was not found.

Install Python from:
  https://www.python.org/downloads/windows/

Then open a new terminal window and run this installer again.
"@
    }
    Write-Host "Using Python: $python"

    Log 'Checking built-in venv support'
    Invoke-NativeCommand -FilePath $python -Arguments @('-c', 'import venv') -FailureMessage 'This Python does not support the built-in venv module. Install a full Python distribution and try again.' | Out-Null

    Log 'Creating private Python environment'
    if (-not (Test-Path $AppHome)) {
        New-Item -ItemType Directory -Path $AppHome -Force | Out-Null
    }
    Invoke-NativeCommand -FilePath $python -Arguments @('-m', 'venv', $VenvDir) -FailureMessage 'Failed to create virtual environment.' | Out-Null

    $venvPython = Join-Path $ScriptsDir 'python.exe'

    Log 'Installing / upgrading Slicer URI Bridge'
    Invoke-NativeCommand -FilePath $venvPython -Arguments @('-m', 'pip', 'install', '--upgrade', $ProjectSpec) -FailureMessage 'pip install failed.' | Out-Null

    Log 'Adding Scripts directory to user PATH'
    $added = Add-ToUserPath $ScriptsDir
    if ($added) {
        Write-Host "Added to PATH: $ScriptsDir"
    }
    else {
        Write-Host 'Already on PATH.'
    }

    Log 'Creating config if needed'
    Invoke-NativeCommand -FilePath $BridgeExe -Arguments @('init-config') -FailureMessage 'Failed to create config.' | Out-Null

    Log 'Registering URI handlers'
    Invoke-NativeCommand -FilePath $BridgeExe -Arguments @('register', '--auto') -FailureMessage 'Failed to register URI handlers.' | Out-Null

    $configDir = Join-Path $env:APPDATA 'slicer-uri-bridge'

    Write-Host ''
    Write-Host 'Done! Slicer URI Bridge is installed.' -ForegroundColor Green
    Write-Host ''
    Write-SummaryLine -Label 'Command:' -Value 'slicer-uri-bridge'
    Write-SummaryLine -Label 'Config:' -Value "$configDir\config.toml"
    Write-SummaryLine -Label 'Logs:' -Value "$configDir\bridge.log"
    Write-SummaryLine -Label 'Test:' -Value 'slicer-uri-bridge test'
    Write-SummaryLine -Label 'Environment:' -Value $VenvDir
    Write-Host ''
    Write-Host '  If "slicer-uri-bridge" is not found, open a new terminal window.' -ForegroundColor DarkGray
    Write-Host '  To update later, just run this installer again.' -ForegroundColor DarkGray
    Write-Host ''
    Wait-BeforeExit
}

try {
    Main
}
catch {
    $message = $_.Exception.Message
    if (-not $message) {
        $message = ($_ | Out-String).Trim()
    }
    Die $message
}
