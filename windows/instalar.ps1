# Faz o osu-tracker iniciar sozinho no logon do Windows, sem janela.
#
# Uso, no PowerShell, a partir da pasta do projeto:
#   powershell -ExecutionPolicy Bypass -File windows\instalar.ps1
# Para usar um atalho na pasta Inicializar em vez do Agendador de Tarefas:
#   powershell -ExecutionPolicy Bypass -File windows\instalar.ps1 -Atalho
#
# Nao precisa de administrador. Desfazer: windows\desinstalar.ps1

param([switch]$Atalho)

$ErrorActionPreference = "Stop"

$Projeto = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Projeto ".venv\Scripts\python.exe"
$PythonW = Join-Path $Projeto ".venv\Scripts\pythonw.exe"   # pythonw roda sem janela de console
$Argumentos = "-m osu_tracker"
$NomeTarefa = "osu-tracker"
$CaminhoAtalho = Join-Path ([Environment]::GetFolderPath("Startup")) "osu-tracker.lnk"

if (-not (Test-Path $PythonW)) {
    throw "Nao achei $PythonW. Crie o ambiente antes: py -m venv .venv ; .venv\Scripts\pip install -r requirements.txt"
}

# 1. Configuracao do jogador (o assistente pergunta o que faltar; se ja estiver configurado, Enter mantem).
Push-Location $Projeto
try {
    & $Python -m osu_tracker configurar
    if ($LASTEXITCODE -ne 0) { throw "Configuracao nao concluida; nada foi instalado." }
} finally {
    Pop-Location
}

# 2. Tira o agendamento antigo (coleta a cada 30 min), se existir.
if (Get-ScheduledTask -TaskName "osu-tracker coletor" -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName "osu-tracker coletor" -Confirm:$false
    Write-Host "Agendamento antigo 'osu-tracker coletor' removido."
}

function Instalar-Atalho {
    $Shell = New-Object -ComObject WScript.Shell
    $Lnk = $Shell.CreateShortcut($CaminhoAtalho)
    $Lnk.TargetPath = $PythonW
    $Lnk.Arguments = $Argumentos
    $Lnk.WorkingDirectory = $Projeto
    $Lnk.Description = "osu-tracker: guarda suas jogadas de osu!"
    $Lnk.Save()
    if (Get-ScheduledTask -TaskName $NomeTarefa -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $NomeTarefa -Confirm:$false
    }
    Start-Process -FilePath $PythonW -ArgumentList $Argumentos -WorkingDirectory $Projeto
    Write-Host "Atalho criado em $CaminhoAtalho e osu-tracker iniciado."
}

function Instalar-Tarefa {
    $Usuario = "$env:USERDOMAIN\$env:USERNAME"
    $Acao = New-ScheduledTaskAction -Execute $PythonW -Argument $Argumentos -WorkingDirectory $Projeto
    $Gatilho = New-ScheduledTaskTrigger -AtLogOn -User $Usuario
    $Principal = New-ScheduledTaskPrincipal -UserId $Usuario -LogonType Interactive -RunLevel Limited
    # Sem limite de tempo (fica no ar a sessao toda), roda na bateria e reinicia se cair.
    $Ajustes = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew `
        -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
    Register-ScheduledTask -TaskName $NomeTarefa -Action $Acao -Trigger $Gatilho -Principal $Principal `
        -Settings $Ajustes -Description "Detecta o osu! aberto e guarda suas jogadas num banco local" -Force | Out-Null
    if (Test-Path $CaminhoAtalho) { Remove-Item $CaminhoAtalho }
    Start-ScheduledTask -TaskName $NomeTarefa
    Write-Host "Tarefa '$NomeTarefa' registrada no Agendador de Tarefas e iniciada."
}

if ($Atalho) {
    Instalar-Atalho
} else {
    try {
        Instalar-Tarefa
    } catch {
        Write-Warning "O Agendador de Tarefas recusou ($($_.Exception.Message)). Usando atalho na pasta Inicializar."
        Instalar-Atalho
    }
}

Write-Host "Pronto. O osu-tracker vai iniciar sozinho a cada logon."
Write-Host "Log: $env:LOCALAPPDATA\osu-tracker\osu-tracker.log"
