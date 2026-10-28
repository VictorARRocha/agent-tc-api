# Registra o RegravacaoBridge como tarefa agendada do usuario atual na D01.
# A tarefa inicia no logon e verifica a cada 5 minutos se o Bridge esta ativo;
# se o processo tiver caido, o Agendador o inicia de novo.
#
# Uso (PowerShell da D01, logado com o usuario que roda o Docker Desktop):
#   powershell -ExecutionPolicy Bypass -File .\instalar_tarefa_regravacao.ps1
#   powershell -ExecutionPolicy Bypass -File .\instalar_tarefa_regravacao.ps1 -Remover
param(
    [switch]$Remover
)

$ErrorActionPreference = "Stop"

$TaskName = "AgenteTC - RegravacaoBridge"
$BaseDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BridgeScript = Join-Path $BaseDir "regravacao_bridge.py"
$UserId = "$env:USERDOMAIN\$env:USERNAME"

function Get-BridgeProcesses {
    Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" |
        Where-Object { $_.CommandLine -like "*regravacao_bridge.py*" }
}

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
}

if ($Remover) {
    if ($existing) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Tarefa removida: $TaskName"
    }
    else {
        Write-Host "Tarefa nao encontrada: $TaskName"
    }
    exit 0
}

if (-not (Test-Path -LiteralPath $BridgeScript)) {
    throw "regravacao_bridge.py nao encontrado em $BaseDir"
}
if (-not (Test-Path -LiteralPath (Join-Path $BaseDir ".env"))) {
    throw ".env nao encontrado em $BaseDir. Copie .env.example para .env e preencha."
}

$PythonExe = $null
try {
    $PythonExe = (& py -3 -c "import sys; print(sys.executable)" | Select-Object -First 1)
}
catch {
    $PythonExe = $null
}
if (-not $PythonExe) {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCommand) {
        $PythonExe = $pythonCommand.Source
    }
}
if (-not $PythonExe) {
    throw "Python nao encontrado (nem 'py -3' nem 'python')."
}
$PythonExe = $PythonExe.Trim()

& $PythonExe -c "import requests, dotenv"
if ($LASTEXITCODE -ne 0) {
    throw "Dependencias ausentes. Rode: `"$PythonExe`" -m pip install -r `"$(Join-Path $BaseDir 'requirements.txt')`""
}

# pythonw.exe roda sem janela de console.
$PythonwExe = Join-Path (Split-Path -Parent $PythonExe) "pythonw.exe"
if (-not (Test-Path -LiteralPath $PythonwExe)) {
    $PythonwExe = $PythonExe
}

$manual = @(Get-BridgeProcesses)
if ($manual.Count -gt 0) {
    $pids = ($manual | ForEach-Object { $_.ProcessId }) -join ", "
    throw "Ja existe Bridge rodando fora da tarefa (PID $pids). Encerre esse processo e rode este script de novo."
}

$action = New-ScheduledTaskAction -Execute $PythonwExe -Argument "`"$BridgeScript`"" -WorkingDirectory $BaseDir
$triggers = @(
    (New-ScheduledTaskTrigger -AtLogOn -User $UserId),
    (New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5))
)
# IgnoreNew: se o Bridge ja esta rodando, a verificacao de 5 em 5 minutos nao abre outro.
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId $UserId -LogonType Interactive -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Description "Agent TC: executa as regravacoes de bases pedidas no dashboard (commit no SVN). Log em $BaseDir\logs\regravacao_bridge.log" `
    -Action $action `
    -Trigger $triggers `
    -Settings $settings `
    -Principal $principal `
    -Force | Out-Null

Start-ScheduledTask -TaskName $TaskName

Write-Host "Tarefa registrada e iniciada: $TaskName"
Write-Host "Python: $PythonwExe"
Write-Host "Log: $(Join-Path $BaseDir 'logs\regravacao_bridge.log')"
Write-Host "Para conferir: Get-ScheduledTask -TaskName '$TaskName' | Get-ScheduledTaskInfo"
