# Vigia dos containers do Agent TC na D01 (roda a cada 5 minutos pela tarefa "AgenteTC - Vigia Docker").
#
# O "restart: unless-stopped" do compose ja reinicia um container que CAI. Este vigia cobre o resto:
#   - Docker Desktop fechado: abre o Docker Desktop (os containers voltam sozinhos quando ele sobe);
#   - container rodando mas "unhealthy" (travado, sem responder ao healthcheck): faz docker restart;
#   - uma vez por dia: limpeza do banco (purge-inactive-versions) dentro do container da API.
# Container parado de proposito (stop/down) nao e tocado.
#
# Instalacao: instalar_tarefa_vigia.ps1. Log em logs\vigia_docker.log (so registra quando faz algo).
param(
    [string[]]$Projetos = @("agenttc_test", "d01"),
    [string]$DockerExe = "",
    [string]$DockerDesktopExe = "C:\Program Files\Docker\Docker\Docker Desktop.exe",
    # Nao reinicia de novo um container que subiu ha menos que isso (evita reiniciar em loop).
    [int]$MinutosEntreReinicios = 10,
    [string]$ContainerApi = "agent-tc-api",
    [int]$DiasRetencao = 30,
    [string]$LogFile = ""
)

$ErrorActionPreference = "Stop"
$BaseDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $LogFile) { $LogFile = Join-Path $BaseDir "logs\vigia_docker.log" }

function Write-Log([string]$Mensagem) {
    $dir = Split-Path -Parent $LogFile
    if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    if ((Test-Path -LiteralPath $LogFile) -and (Get-Item -LiteralPath $LogFile).Length -gt 5MB) {
        Move-Item -LiteralPath $LogFile -Destination ($LogFile + ".1") -Force
    }
    $linha = "[" + (Get-Date -Format "yyyy-MM-dd HH:mm:ss") + "] " + $Mensagem
    Add-Content -LiteralPath $LogFile -Value $linha -Encoding UTF8
}

function Resolve-Docker {
    if ($DockerExe) { return $DockerExe }
    $cmd = Get-Command docker -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $padrao = "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
    if (Test-Path -LiteralPath $padrao) { return $padrao }
    return $null
}

# Executa o docker e devolve as linhas de saida; $script:DockerOk diz se terminou sem erro.
function Invoke-Docker([string[]]$Argumentos) {
    $saida = & $script:Docker @Argumentos 2>$null
    $script:DockerOk = ($LASTEXITCODE -eq 0)
    return @($saida | Where-Object { $_ -ne $null -and "$_".Trim() -ne "" })
}

function Get-StartedAt([string]$Texto) {
    # O docker manda 9 casas de fracao de segundo; o .NET aceita ate 7.
    $t = $Texto -replace '(\.\d{7})\d+', '$1'
    # So cmdlets (sem metodos .NET): funciona tambem com o PowerShell em modo restrito.
    try { return Get-Date -Date $t } catch { return $null }
}

try {
    $script:Docker = Resolve-Docker
    if (-not $script:Docker) {
        Write-Log "docker.exe nao encontrado; nada a fazer."
        exit 1
    }

    # 1) Docker Desktop esta de pe?
    Invoke-Docker @("info", "--format", "{{.ServerVersion}}") | Out-Null
    if (-not $script:DockerOk) {
        $desktop = Get-Process -Name "Docker Desktop" -ErrorAction SilentlyContinue
        if ($desktop) {
            Write-Log "Docker ainda nao responde, mas o Docker Desktop esta aberto (iniciando). Confere de novo na proxima rodada."
        }
        elseif (Test-Path -LiteralPath $DockerDesktopExe) {
            Start-Process -FilePath $DockerDesktopExe
            Write-Log "Docker Desktop estava fechado: aberto agora. Os containers com restart unless-stopped sobem junto."
        }
        else {
            Write-Log "Docker nao responde e o Docker Desktop nao foi encontrado em $DockerDesktopExe."
        }
        exit 0
    }

    # 2) Containers travados (unhealthy) dos projetos do Agent TC.
    foreach ($projeto in $Projetos) {
        $nomes = Invoke-Docker @("ps", "--filter", "label=com.docker.compose.project=$projeto", "--filter", "health=unhealthy", "--format", "{{.Names}}")
        foreach ($nome in $nomes) {
            $info = (Invoke-Docker @("inspect", "-f", "{{.State.StartedAt}}", $nome)) | Select-Object -First 1
            $inicio = Get-StartedAt "$info"
            if ($inicio -and ((Get-Date) - $inicio).TotalMinutes -lt $MinutosEntreReinicios) {
                Write-Log "$nome ($projeto) esta unhealthy, mas subiu ha menos de $MinutosEntreReinicios min; aguardando."
                continue
            }
            Invoke-Docker @("restart", $nome) | Out-Null
            if ($script:DockerOk) { Write-Log "$nome ($projeto) estava unhealthy (travado): reiniciado." }
            else { Write-Log "$nome ($projeto) esta unhealthy e o docker restart falhou." }
        }
    }

    # 3) Limpeza diaria do banco (rodagens de versoes paradas ha mais de $DiasRetencao dias), uma vez por dia.
    # Roda dentro do container da API, que ja tem o Postgres e as evidencias; nas VMs o envio usa a API e nao alcanca o banco.
    $marcador = Join-Path (Split-Path -Parent $LogFile) "limpeza_ultima_data.txt"
    $hoje = Get-Date -Format "yyyy-MM-dd"
    $ultima = if (Test-Path -LiteralPath $marcador) { (Get-Content -LiteralPath $marcador -TotalCount 1) } else { "" }
    if ($ultima -ne $hoje) {
        $saida = Invoke-Docker @("exec", $ContainerApi, "python", "cli/agent_tc_maintenance.py", "purge-inactive-versions", "--retention-days", "$DiasRetencao", "--apply")
        if ($script:DockerOk) {
            Set-Content -LiteralPath $marcador -Value $hoje -Encoding ASCII
            Write-Log ("Limpeza diaria feita: " + (($saida -join " ") -replace '\s+', ' '))
        }
        else { Write-Log "Limpeza diaria falhou (container $ContainerApi); tenta de novo na proxima rodada." }
    }
    exit 0
}
catch {
    Write-Log ("Erro no vigia: " + $_.Exception.Message)
    exit 1
}
