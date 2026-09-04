$ErrorActionPreference = "Stop"

$BaseDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$EnvFile = Join-Path $BaseDir ".env"

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

Write-Host "========================================"
Write-Host "Agent TC - Python pos-rodagem"
Write-Host "========================================"

$LogDir = Get-ConfigValue "AGENT_TC_LOG_DIR" "S:\Teste automatico\Arquivos\AgenteTC\logs"
$PracticeMdsPaths = Get-ConfigValue "AGENT_TC_PRACTICE_MDS_PATHS" "C:\TC\TC12 - Simplificado\Cadastros\Practice Base Unificada.mds;C:\TC\TC12 - Simplificado\Practice Antigo\Practice Bases Individuais.mds"
$PracticeProjectSuite = Get-ConfigValue "AGENT_TC_PRACTICE_PROJECT_SUITE" "C:\TC\TC12 - Simplificado\TestesVisualPractice.pjs"
$SupremaMdsPath = Get-ConfigValue "AGENT_TC_SUPREMA_MDS_PATH" "C:\TC\tc12\PROJETO-TC12\Integracoes\Integracoes.mds"
$LogsBase = Get-ConfigValue "AGENT_TC_LOGS_BASE" "S:\Teste automatico\Arquivos\Arquivos De Log\ArquivosCompactados"
$Backend = Get-ConfigValue "AGENT_TC_BACKEND" "supabase"
$SystemHint = Get-ConfigValue "AGENT_TC_SYSTEM" ""

$MdsExplicit = $true
$MdsPath = Get-ConfigValue "AGENT_TC_MDS_PATH" ""
if (-not $MdsPath) {
    $MdsExplicit = $false
    $MdsPath = "C:\TC\Unico\Unico.mds"
}

$PjsExplicit = $true
$ProjectSuitePath = Get-ConfigValue "AGENT_TC_PROJECT_SUITE_PATH" ""
if (-not $ProjectSuitePath) {
    $PjsExplicit = $false
}

$TimesFolder = Get-ConfigValue "AGENT_TC_TIMES_FOLDER" ""
if (-not $TimesFolder -and (Test-Path -LiteralPath "C:\Tempos TC")) {
    $TimesFolder = "C:\Tempos TC"
}
if (-not $TimesFolder -and (Test-Path -LiteralPath "C:\TC\Tempos TC")) {
    $TimesFolder = "C:\TC\Tempos TC"
}

$VmName = if ($args.Count -ge 1 -and $args[0]) { $args[0] } else { $env:COMPUTERNAME }
$RunFolder = if ($args.Count -ge 2 -and $args[1]) { $args[1] } else { "" }
$VersionHint = if ($args.Count -ge 3 -and $args[2]) { $args[2] } else { "" }

if ($SystemHint.ToLowerInvariant() -eq "practice") {
    $MdsPath = $PracticeMdsPaths
    if (-not $PjsExplicit) {
        $ProjectSuitePath = $PracticeProjectSuite
    }
}
if ($SystemHint.ToLowerInvariant() -eq "suprema") {
    $MdsPath = $SupremaMdsPath
}

if (-not (Test-Path -LiteralPath $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
}

Write-Host "Base Agent TC: $BaseDir"
Write-Host "VM: $VmName"
Write-Host "Env: $EnvFile"
Write-Host "MDS: $MdsPath"
if ($ProjectSuitePath) { Write-Host "ProjectSuite: $ProjectSuitePath" }
Write-Host "Logs Agent TC: $LogDir"
Write-Host "Logs base: $LogsBase"
Write-Host "Backend: $Backend"
if ($TimesFolder) { Write-Host "Tempos TC: $TimesFolder" }
if ($VersionHint) { Write-Host "Filtro de versao: $VersionHint" }
Write-Host ""

if (-not (Test-Path -LiteralPath (Join-Path $BaseDir "cli\agent_tc_ingest.py"))) {
    throw "cli\agent_tc_ingest.py nao encontrado em $BaseDir"
}
if (-not (Test-Path -LiteralPath $EnvFile)) {
    throw ".env nao encontrado em $EnvFile"
}

if (-not $RunFolder) {
    $LogsRoot = Join-Path $LogsBase $VmName
    if (-not (Test-Path -LiteralPath $LogsRoot)) {
        throw "Pasta de logs da VM nao encontrada: $LogsRoot"
    }
    $candidates = Get-ChildItem -LiteralPath $LogsRoot -Directory
    if ($VersionHint) {
        $candidates = $candidates | Where-Object { $_.Name -like ($VersionHint + "*") }
    }
    $RunFolder = $candidates | Sort-Object LastWriteTime -Descending | Select-Object -First 1 -ExpandProperty FullName
    if (-not $RunFolder) {
        throw "Nenhuma pasta de rodagem encontrada em $LogsRoot"
    }
}

if (-not (Test-Path -LiteralPath $RunFolder)) {
    throw "Pasta de rodagem nao encontrada: $RunFolder"
}

$AutoSystemText = ($RunFolder + " " + $VersionHint).ToLowerInvariant()
if ($SystemHint.ToLowerInvariant() -eq "practice") {
    $MdsPath = $PracticeMdsPaths
    if (-not $PjsExplicit) {
        $ProjectSuitePath = $PracticeProjectSuite
    }
} elseif ($SystemHint.ToLowerInvariant() -eq "suprema") {
    $MdsPath = $SupremaMdsPath
} elseif (-not $MdsExplicit) {
    if ($AutoSystemText.Contains("practice")) {
        $MdsPath = $PracticeMdsPaths
        if (-not $PjsExplicit) {
            $ProjectSuitePath = $PracticeProjectSuite
        }
    }
    if ($AutoSystemText.Contains("suprema") -or $AutoSystemText.Contains("integracoes")) {
        $MdsPath = $SupremaMdsPath
    }
}

Write-Host "MDS selecionado: $MdsPath"
if ($ProjectSuitePath) { Write-Host "ProjectSuite selecionado: $ProjectSuitePath" }

$missingMds = @()
foreach ($path in $MdsPath.Split(";")) {
    $clean = $path.Trim().Trim('"')
    if ($clean -and -not (Test-Path -LiteralPath $clean)) {
        $missingMds += $clean
    }
}
if ($missingMds.Count -gt 0) {
    throw "Um ou mais arquivos .mds nao foram encontrados: $($missingMds -join '; ')"
}
if ($ProjectSuitePath -and -not (Test-Path -LiteralPath $ProjectSuitePath)) {
    throw "ProjectSuite .pjs nao encontrado em $ProjectSuitePath"
}

$Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$RunLog = Join-Path $LogDir "agent_tc_python_${VmName}_${Timestamp}.log"
$DoneFile = Join-Path $LogDir "agent_tc_python_finalizado_${VmName}_${Timestamp}.txt"

Write-Host "Pasta da rodagem:"
Write-Host $RunFolder
Write-Host ""
Write-Host "Log:"
Write-Host $RunLog
Write-Host ""

Set-Location -LiteralPath $BaseDir

$pythonArgs = @(
    "-3",
    (Join-Path $BaseDir "cli\agent_tc_ingest.py"),
    "--backend", $Backend,
    "--env", $EnvFile,
    "--run-folder", $RunFolder,
    "--mds", $MdsPath,
    "--output-root", $LogDir,
    "--vm", $VmName
)
if ($TimesFolder) {
    $pythonArgs += @("--times-folder", $TimesFolder)
}
if ($ProjectSuitePath) {
    $pythonArgs += @("--project-suite", $ProjectSuitePath)
}

& py @pythonArgs *> $RunLog
$ExitCode = $LASTEXITCODE

Write-Host ""
Write-Host "========================================"
Write-Host "Agent TC Python finalizado com codigo $ExitCode"
Write-Host "Log: $RunLog"
Write-Host "========================================"
Write-Host ""
Write-Host "Ultimas linhas do log:"
Write-Host "----------------------------------------"
if (Test-Path -LiteralPath $RunLog) {
    Get-Content -LiteralPath $RunLog -Tail 80
}
Write-Host "----------------------------------------"
Write-Host ""

if ($ExitCode -eq 0) {
    @(
        "Agent TC Python finalizado com sucesso.",
        "VM: $VmName",
        "Pasta: $RunFolder",
        "Data/Hora: $(Get-Date)",
        "Log: $RunLog"
    ) | Set-Content -LiteralPath $DoneFile
    Write-Host "ANALISE FINALIZADA COM SUCESSO."
} else {
    @(
        "Agent TC Python finalizado com erro.",
        "VM: $VmName",
        "Pasta: $RunFolder",
        "Data/Hora: $(Get-Date)",
        "Codigo: $ExitCode",
        "Log: $RunLog"
    ) | Set-Content -LiteralPath $DoneFile
    Write-Host "ANALISE FINALIZADA COM ERRO."
}

Write-Host "Marcador: $DoneFile"
exit $ExitCode
