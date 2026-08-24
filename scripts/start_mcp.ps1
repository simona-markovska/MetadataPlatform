# ============================================================================
# Metadata Intelligence Platform
# MCP + Dev Tunnel Startup Script
#
# Known-good POC configuration:
#   MCP server : http://127.0.0.1:8000/mcp
#   Transport  : Streamable HTTP
#   Tunnel     : majestic-horse-mtz22x4.eun1
# ============================================================================

$ErrorActionPreference = "Stop"

# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------

$ProjectRoot = "C:\Projects\MetadataPlatform"

$PythonExe = "C:\Users\SimonaMarkovska\AppData\Local\Programs\Python\Python314\python.exe"

$McpServer = "$ProjectRoot\mcp\metadata_server.py"

$TunnelExe = "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\Microsoft.devtunnel_Microsoft.Winget.Source_8wekyb3d8bbwe\devtunnel.exe"

$TunnelName = "majestic-horse-mtz22x4.eun1"

$McpHost = "127.0.0.1"
$McpPort = 8000


# ----------------------------------------------------------------------------
# Header
# ----------------------------------------------------------------------------

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host " Metadata Intelligence Platform" -ForegroundColor Cyan
Write-Host " MCP Server + Dev Tunnel" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""


# ----------------------------------------------------------------------------
# Validate files
# ----------------------------------------------------------------------------

if (-not (Test-Path $PythonExe)) {
    Write-Host "ERROR: Python executable not found:" -ForegroundColor Red
    Write-Host $PythonExe
    exit 1
}

if (-not (Test-Path $McpServer)) {
    Write-Host "ERROR: MCP server not found:" -ForegroundColor Red
    Write-Host $McpServer
    exit 1
}

if (-not (Test-Path $TunnelExe)) {
    Write-Host "ERROR: Dev Tunnel executable not found:" -ForegroundColor Red
    Write-Host $TunnelExe
    exit 1
}


# ----------------------------------------------------------------------------
# Check port 8000
# ----------------------------------------------------------------------------

Write-Host "[1/4] Checking MCP port $McpPort..." -ForegroundColor Yellow

$ExistingConnection = Get-NetTCPConnection `
    -LocalPort $McpPort `
    -State Listen `
    -ErrorAction SilentlyContinue

if ($ExistingConnection) {

    $ExistingPid = $ExistingConnection[0].OwningProcess

    Write-Host "Port $McpPort is already in use." -ForegroundColor Yellow
    Write-Host "Existing process ID: $ExistingPid"

    $ExistingProcess = Get-Process `
        -Id $ExistingPid `
        -ErrorAction SilentlyContinue

    if ($ExistingProcess) {
        Write-Host "Process: $($ExistingProcess.ProcessName)"
    }

    Write-Host ""
    Write-Host "Assuming the existing MCP server is already running." -ForegroundColor Green

} else {

    Write-Host "Port $McpPort is free." -ForegroundColor Green

    # ------------------------------------------------------------------------
    # Start MCP server
    # ------------------------------------------------------------------------

    Write-Host ""
    Write-Host "[2/4] Starting MCP server..." -ForegroundColor Yellow

    Start-Process `
        -FilePath $PythonExe `
        -ArgumentList "`"$McpServer`"" `
        -WorkingDirectory $ProjectRoot

    Write-Host "MCP server process started." -ForegroundColor Green

    # ------------------------------------------------------------------------
    # Wait for port 8000
    # ------------------------------------------------------------------------

    Write-Host "Waiting for MCP server to listen on port $McpPort..." -ForegroundColor Yellow

    $MaxAttempts = 30
    $Attempt = 0

    do {

        Start-Sleep -Seconds 1
        $Attempt++

        $Connection = Get-NetTCPConnection `
            -LocalPort $McpPort `
            -State Listen `
            -ErrorAction SilentlyContinue

        if ($Connection) {
            break
        }

        Write-Host "." -NoNewline

    } while ($Attempt -lt $MaxAttempts)

    Write-Host ""

    if (-not $Connection) {
        Write-Host "ERROR: MCP server did not start on port $McpPort." -ForegroundColor Red
        exit 1
    }

    Write-Host "MCP server is listening on $McpHost`:$McpPort" -ForegroundColor Green
}


# ----------------------------------------------------------------------------
# Start Dev Tunnel
# ----------------------------------------------------------------------------

Write-Host ""
Write-Host "[3/4] Starting Dev Tunnel..." -ForegroundColor Yellow

Write-Host ""
Write-Host "Tunnel: $TunnelName" -ForegroundColor Cyan
Write-Host ""

& $TunnelExe host $TunnelName


# ----------------------------------------------------------------------------
# End
# ----------------------------------------------------------------------------

Write-Host ""
Write-Host "[4/4] MCP environment stopped." -ForegroundColor Yellow