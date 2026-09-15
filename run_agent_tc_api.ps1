$ErrorActionPreference = "Stop"

$BaseDir = Split-Path -Parent $MyInvocation.MyCommand.Path
& (Join-Path $BaseDir "API\run_agent_tc_api.ps1") @args
exit $LASTEXITCODE
