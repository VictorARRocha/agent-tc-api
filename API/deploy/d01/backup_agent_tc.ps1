# Backup do Agent TC na D01: dump do PostgreSQL + pacote do volume de evidencias.
#
# Cada execucao cria <Destino>\agent_tc_AAAAMMDD_HHMMSS\ com:
#   agent_tc.dump       dump do banco (pg_dump -Fc), validado com pg_restore --list
#   evidencias.tar.gz   conteudo de /data/evidencias do container agent-tc-api
#   manifesto.txt       data, tamanhos e containers usados
# Pastas de backup com mais de -RetencaoDias dias sao removidas no final.
# Os arquivos .env/docker.env NAO entram no backup: contem segredos e precisam
# de guarda separada e protegida.
#
# Uso (PowerShell da D01):
#   powershell -ExecutionPolicy Bypass -File .\backup_agent_tc.ps1
#   powershell -ExecutionPolicy Bypass -File .\backup_agent_tc.ps1 -Destino "\\servidor\pasta\AgenteTC\backups"
#   powershell -ExecutionPolicy Bypass -File .\backup_agent_tc.ps1 -InstalarTarefa -Horario 02:00
param(
    [string]$Destino = "S:\Teste automatico\Arquivos\AgenteTC\backups",
    [int]$RetencaoDias = 14,
    [switch]$InstalarTarefa,
    [string]$Horario = "02:00"
)

$ErrorActionPreference = "Stop"

$DeployDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$EnvFile = Join-Path $DeployDir "docker.env"
$PostgresContainer = "agent-tc-postgres"
$ApiContainer = "agent-tc-api"
$TaskName = "AgenteTC - Backup diario"

if ($RetencaoDias -lt 1) {
    throw "-RetencaoDias deve ser maior que zero."
}

if ($InstalarTarefa) {
    $UserId = "$env:USERDOMAIN\$env:USERNAME"
    $scriptPath = $MyInvocation.MyCommand.Path
    $arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`" -Destino `"$Destino`" -RetencaoDias $RetencaoDias"
    $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arguments -WorkingDirectory $DeployDir
    $trigger = New-ScheduledTaskTrigger -Daily -At $Horario
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 4)
    # Interactive: o Docker Desktop roda na sessao deste usuario.
    $principal = New-ScheduledTaskPrincipal -UserId $UserId -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $TaskName -Description "Agent TC: backup diario do PostgreSQL e das evidencias em $Destino" `
        -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
    Write-Host "Tarefa registrada: $TaskName (diaria as $Horario, destino $Destino, retencao $RetencaoDias dias)"
    Write-Host "Para testar agora: Start-ScheduledTask -TaskName '$TaskName'"
    exit 0
}

function Get-DockerEnvValue($Name, $DefaultValue) {
    if (Test-Path -LiteralPath $EnvFile) {
        $line = Get-Content -LiteralPath $EnvFile | Where-Object { $_.Trim().StartsWith("$Name=") } | Select-Object -Last 1
        if ($line) {
            $value = $line.Split("=", 2)[1].Trim().Trim('"').Trim("'")
            if ($value) {
                return $value
            }
        }
    }
    return $DefaultValue
}

function Invoke-Docker {
    param([string[]]$Arguments, [string]$Step)
    & docker @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Falha em '$Step' (docker $($Arguments -join ' '), codigo $LASTEXITCODE)."
    }
}

function Write-BackupLog($Message) {
    $line = "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $Message"
    Write-Host $line
    Add-Content -LiteralPath $LogFile -Value $line -Encoding UTF8
}

try {
    if (-not (Test-Path -LiteralPath $Destino)) {
        New-Item -ItemType Directory -Path $Destino -Force | Out-Null
    }
}
catch {
    # Destino indisponivel (ex.: unidade S: nao mapeada na sessao da tarefa).
    $fallbackLog = Join-Path $DeployDir "backup_agent_tc_erro.log"
    $message = "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] ERRO: destino $Destino indisponivel: $($_.Exception.Message)"
    Add-Content -LiteralPath $fallbackLog -Value $message -Encoding UTF8
    Write-Host $message
    exit 1
}
$LogFile = Join-Path $Destino "backup_agent_tc.log"
$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$Target = Join-Path $Destino "agent_tc_$Stamp"
$DbUser = Get-DockerEnvValue "POSTGRES_USER" "agent_tc"
$DbName = Get-DockerEnvValue "POSTGRES_DB" "agent_tc"

try {
    Write-BackupLog "Inicio do backup em $Target"
    New-Item -ItemType Directory -Path $Target | Out-Null

    # 1. Banco: dump dentro do container, validacao e copia para o destino.
    Invoke-Docker @("exec", $PostgresContainer, "pg_dump", "-U", $DbUser, "-d", $DbName, "-Fc", "-f", "/tmp/agent_tc_backup.dump") "pg_dump"
    Invoke-Docker @("exec", $PostgresContainer, "sh", "-c", "pg_restore --list /tmp/agent_tc_backup.dump > /dev/null") "validacao do dump"
    Invoke-Docker @("cp", "${PostgresContainer}:/tmp/agent_tc_backup.dump", (Join-Path $Target "agent_tc.dump")) "copia do dump"
    Invoke-Docker @("exec", $PostgresContainer, "rm", "-f", "/tmp/agent_tc_backup.dump") "limpeza do dump temporario"
    Write-BackupLog "Banco copiado: agent_tc.dump"

    # 2. Evidencias: pacote do volume montado na API.
    Invoke-Docker @("exec", $ApiContainer, "tar", "-czf", "/tmp/evidencias_backup.tar.gz", "-C", "/data", "evidencias") "pacote das evidencias"
    Invoke-Docker @("cp", "${ApiContainer}:/tmp/evidencias_backup.tar.gz", (Join-Path $Target "evidencias.tar.gz")) "copia das evidencias"
    Invoke-Docker @("exec", $ApiContainer, "rm", "-f", "/tmp/evidencias_backup.tar.gz") "limpeza do pacote temporario"
    Write-BackupLog "Evidencias copiadas: evidencias.tar.gz"

    $files = Get-ChildItem -LiteralPath $Target -File
    $manifest = @(
        "Backup Agent TC",
        "Data: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')",
        "Maquina: $env:COMPUTERNAME",
        "Banco: $DbName (usuario $DbUser, container $PostgresContainer)",
        "Evidencias: /data/evidencias (container $ApiContainer)",
        ""
    ) + ($files | ForEach-Object { "{0}  {1:N0} bytes" -f $_.Name, $_.Length })
    $manifest | Set-Content -LiteralPath (Join-Path $Target "manifesto.txt") -Encoding UTF8
    foreach ($file in $files) {
        if ($file.Length -le 0) {
            throw "Arquivo de backup vazio: $($file.Name)"
        }
    }
    Write-BackupLog "Backup concluido: $Target"
}
catch {
    Write-BackupLog "ERRO: $($_.Exception.Message)"
    # Backup incompleto nao deve ser confundido com um valido.
    if (Test-Path -LiteralPath $Target) {
        Rename-Item -LiteralPath $Target -NewName ("FALHOU_agent_tc_$Stamp")
    }
    exit 1
}

# 3. Retencao: remove somente pastas criadas por este script.
$limit = (Get-Date).AddDays(-$RetencaoDias)
Get-ChildItem -LiteralPath $Destino -Directory |
    Where-Object { $_.Name -match '^(FALHOU_)?agent_tc_\d{8}_\d{6}$' -and $_.CreationTime -lt $limit } |
    ForEach-Object {
        Remove-Item -LiteralPath $_.FullName -Recurse -Force
        Write-BackupLog "Removido pela retencao: $($_.Name)"
    }

exit 0
