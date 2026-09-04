$ErrorActionPreference = "Stop"

$BaseDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$LogDir = Join-Path $BaseDir "logs"

if (-not (Test-Path -LiteralPath $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
}

Set-Location -LiteralPath $BaseDir

Write-Host "Jenkins Bridge"
Write-Host "Base: $BaseDir"
Write-Host "Log: $(Join-Path $LogDir 'jenkins_bridge.log')"
Write-Host ""

& python "$BaseDir\jenkins_bridge.py" *>> (Join-Path $LogDir "jenkins_bridge.log")
