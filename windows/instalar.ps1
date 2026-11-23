# Faz o osu-tracker iniciar sozinho no logon do Windows, sem janela.
#
# Uso, no PowerShell, a partir da pasta do projeto:
#   powershell -ExecutionPolicy Bypass -File windows\instalar.ps1
# Para usar um atalho na pasta Inicializar em vez do Agendador de Tarefas:
#   powershell -ExecutionPolicy Bypass -File windows\instalar.ps1 -Atalho
#
# A janela do osu-tracker (e o osu-tracker.exe) usa este mesmo script, passando o que iniciar:
#   -Executavel <programa> -Argumentos "<argumentos>" -Pasta <pasta de trabalho> -SemConfigurar -NaoIniciar
#
# Nao precisa de administrador. Desfazer: windows\desinstalar.ps1

param(
    [switch]$Atalho,
    [string]$Executavel,      # padrao: pythonw.exe do .venv do projeto
    [string]$Argumentos,      # padrao: -m osu_tracker
    [string]$Pasta,           # padrao: pasta do projeto
    [switch]$SemConfigurar,   # nao roda o assistente de console (a janela ja configurou)
    [switch]$NaoIniciar       # so registra para o proximo logon (a janela inicia o detector ela mesma)
)

$ErrorActionPreference = "Stop"

$Projeto = Split-Path -Parent $PSScriptRoot
$NomeTarefa = "osu-tracker"
$CaminhoAtalho = Join-Path ([Environment]::GetFolderPath("Startup")) "osu-tracker.lnk"

if (-not $Executavel) {
    $Python = Join-Path $Projeto ".venv\Scripts\python.exe"
    $Executavel = Join-Path $Projeto ".venv\Scripts\pythonw.exe"   # pythonw roda sem janela de console
    if (-not $Argumentos) { $Argumentos = "-m osu_tracker" }
    if (-not $Pasta) { $Pasta = $Projeto }
    if (-not (Test-Path $Executavel)) {
        throw "Nao achei $Executavel. Crie o ambiente antes: py -m venv .venv ; .venv\Scripts\pip install -r requirements.txt"
    }

    # 1. Configuracao do jogador (o assistente pergunta o que faltar; se ja estiver configurado, Enter mantem).
    if (-not $SemConfigurar) {
        Push-Location $Projeto
        try {
            & $Python -m osu_tracker configurar
            if ($LASTEXITCODE -ne 0) { throw "Configuracao nao concluida; nada foi instalado." }
        } finally {
            Pop-Location
        }
    }
} else {
    if (-not (Test-Path $Executavel)) { throw "Nao achei $Executavel." }
    if (-not $Pasta) { $Pasta = Split-Path -Parent $Executavel }
}

# 2. Tira o agendamento antigo (coleta a cada 30 min), se existir.
if (Get-ScheduledTask -TaskName "osu-tracker coletor" -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName "osu-tracker coletor" -Confirm:$false
    Write-Host "Agendamento antigo 'osu-tracker coletor' removido."
}

function Iniciar-Agora {
    if ($Argumentos) {
        Start-Process -FilePath $Executavel -ArgumentList $Argumentos -WorkingDirectory $Pasta
    } else {
        Start-Process -FilePath $Executavel -WorkingDirectory $Pasta
    }
}

function Instalar-Atalho {
    $Shell = New-Object -ComObject WScript.Shell
    $Lnk = $Shell.CreateShortcut($CaminhoAtalho)
    $Lnk.TargetPath = $Executavel
    $Lnk.Arguments = $Argumentos
    $Lnk.WorkingDirectory = $Pasta
    $Lnk.Description = "osu-tracker: guarda suas jogadas de osu!"
    $Lnk.Save()
    if (Get-ScheduledTask -TaskName $NomeTarefa -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $NomeTarefa -Confirm:$false
    }
    if (-not $NaoIniciar) { Iniciar-Agora }
    Write-Host "Atalho criado em $CaminhoAtalho."
}

function Instalar-Tarefa {
    $Usuario = "$env:USERDOMAIN\$env:USERNAME"
    if ($Argumentos) {
        $Acao = New-ScheduledTaskAction -Execute $Executavel -Argument $Argumentos -WorkingDirectory $Pasta
    } else {
        $Acao = New-ScheduledTaskAction -Execute $Executavel -WorkingDirectory $Pasta
    }
    $Gatilho = New-ScheduledTaskTrigger -AtLogOn -User $Usuario
    $Principal = New-ScheduledTaskPrincipal -UserId $Usuario -LogonType Interactive -RunLevel Limited
    # Sem limite de tempo (fica no ar a sessao toda), roda na bateria e reinicia se cair.
    $Ajustes = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew `
        -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
    Register-ScheduledTask -TaskName $NomeTarefa -Action $Acao -Trigger $Gatilho -Principal $Principal `
        -Settings $Ajustes -Description "Detecta o osu! aberto e guarda suas jogadas num banco local" -Force | Out-Null
    if (Test-Path $CaminhoAtalho) { Remove-Item $CaminhoAtalho }
    if (-not $NaoIniciar) { Start-ScheduledTask -TaskName $NomeTarefa }
    Write-Host "Tarefa '$NomeTarefa' registrada no Agendador de Tarefas."
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

if ($NaoIniciar) {
    Write-Host "Pronto. O osu-tracker vai iniciar sozinho a partir do proximo logon."
} else {
    Write-Host "Pronto. O osu-tracker ja esta rodando e vai iniciar sozinho a cada logon."
}
Write-Host "Log: $env:LOCALAPPDATA\osu-tracker\osu-tracker.log"
