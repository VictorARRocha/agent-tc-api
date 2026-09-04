$ErrorActionPreference = "Stop"

$BaseDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$EnvFile = Join-Path $BaseDir ".env"
$LogDir = Join-Path $BaseDir "logs"
$LogFile = Join-Path $LogDir "agent_tc_api.log"

function Get-AgentTcEnvValue($Name) {
    if (-not (Test-Path -LiteralPath $EnvFile)) {
        return $null
    }

    $prefix = "$Name="
    $matches = Get-Content -LiteralPath $EnvFile | Where-Object { $_.Trim().StartsWith($prefix) }
    if (-not $matches) {
        return $null
    }

    return (($matches | Select-Object -Last 1).Split("=", 2)[1]).Trim().Trim('"').Trim("'")
}

function Get-ConfigValue($Name, $DefaultValue) {
    $envItem = Get-Item -LiteralPath "Env:$Name" -ErrorAction SilentlyContinue
    $envValue = if ($envItem) { $envItem.Value } else { $null }
    if ($envValue) {
        return $envValue
    }

    $fileValue = Get-AgentTcEnvValue $Name
    if ($fileValue) {
        return $fileValue
    }

    return $DefaultValue
}

if (-not (Test-Path -LiteralPath $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
}

if (-not (Test-Path -LiteralPath (Join-Path $BaseDir "cli\agent_tc_api.py"))) {
    throw "cli\agent_tc_api.py nao encontrado em $BaseDir"
}
if (-not (Test-Path -LiteralPath $EnvFile)) {
    throw ".env nao encontrado em $EnvFile"
}

$HostName = Get-ConfigValue "AGENT_TC_API_HOST" "0.0.0.0"
$Port = Get-ConfigValue "AGENT_TC_API_PORT" "8000"
$Backend = Get-ConfigValue "AGENT_TC_BACKEND" "supabase"

$PythonArgs = @("-3")
$UsePyLauncher = $true
try {
    & py -3 --version *> $null
}
catch {
    $UsePyLauncher = $false
}

if (-not $UsePyLauncher) {
    $PythonArgs = @()
}

Write-Host "========================================"
Write-Host "Agent TC - API"
Write-Host "========================================"
Write-Host "Base Agent TC: $BaseDir"
Write-Host "Env: $EnvFile"
Write-Host "Backend: $Backend"
Write-Host "Host: $HostName"
Write-Host "Porta: $Port"
Write-Host "Log: $LogFile"
Write-Host ""

Set-Location -LiteralPath $BaseDir

if ($UsePyLauncher) {
    & py @PythonArgs "$BaseDir\cli\agent_tc_api.py" `
        --backend $Backend `
        --env $EnvFile `
        --host $HostName `
        --port $Port 2>&1 | Tee-Object -FilePath $LogFile -Append
}
else {
    & python "$BaseDir\cli\agent_tc_api.py" `
        --backend $Backend `
        --env $EnvFile `
        --host $HostName `
        --port $Port 2>&1 | Tee-Object -FilePath $LogFile -Append
}
