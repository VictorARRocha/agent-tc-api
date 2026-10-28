# Registra o vigia dos containers do Agent TC como tarefa agendada do usuario atual na D01.
# Roda no logon e a cada 5 minutos, sem janela: abre o Docker Desktop se estiver fechado e
# reinicia container travado (unhealthy). Detalhes em vigia_docker.ps1.
#
# Uso (PowerShell da D01, logado com o usuario que roda o Docker Desktop):
#   powershell -ExecutionPolicy Bypass -File .\instalar_tarefa_vigia.ps1
#   powershell -ExecutionPolicy Bypass -File .\instalar_tarefa_vigia.ps1 -Remover
param(
    [switch]$Remover
)

$ErrorActionPreference = "Stop"

$TaskName = "AgenteTC - Vigia Docker"
$BaseDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Launcher = Join-Path $BaseDir "vigia_docker_hidden.vbs"
$UserId = "$env:USERDOMAIN\$env:USERNAME"

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

foreach ($arquivo in @("vigia_docker.ps1", "vigia_docker_hidden.vbs")) {
    if (-not (Test-Path -LiteralPath (Join-Path $BaseDir $arquivo))) {
        throw "$arquivo nao encontrado em $BaseDir"
    }
}

# wscript + vbs: roda o PowerShell escondido (sem piscar janela a cada 5 minutos).
$action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "`"$Launcher`"" -WorkingDirectory $BaseDir
$triggers = @(
    (New-ScheduledTaskTrigger -AtLogOn -User $UserId),
    (New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5))
)
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId $UserId -LogonType Interactive -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Description "Agent TC: a cada 5 minutos abre o Docker Desktop se estiver fechado e reinicia container travado (unhealthy) dos projetos agenttc_test e d01. Log em $BaseDir\logs\vigia_docker.log" `
    -Action $action `
    -Trigger $triggers `
    -Settings $settings `
    -Principal $principal `
    -Force | Out-Null

Start-ScheduledTask -TaskName $TaskName

Write-Host "Tarefa registrada e iniciada: $TaskName"
Write-Host "Log (so registra quando faz algo): $(Join-Path $BaseDir 'logs\vigia_docker.log')"
Write-Host "Para conferir: Get-ScheduledTask -TaskName '$TaskName' | Get-ScheduledTaskInfo"
