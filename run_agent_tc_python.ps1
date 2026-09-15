$ErrorActionPreference = "Stop"

$BaseDir = Split-Path -Parent $MyInvocation.MyCommand.Path
& (Join-Path $BaseDir "PythonRodagem\run_agent_tc_python.ps1") @args
exit $LASTEXITCODE
