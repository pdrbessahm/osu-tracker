# Tira o osu-tracker do logon do Windows e encerra o que estiver rodando.
# Seus dados (configuracao, banco e log) ficam onde estao.
#
# Uso, no PowerShell, a partir da pasta do projeto:
#   powershell -ExecutionPolicy Bypass -File windows\desinstalar.ps1
# Com -ManterRodando so tira do logon e deixa o detector que estiver rodando (usado pela janela).

param([switch]$ManterRodando)

$ErrorActionPreference = "Stop"

$NomeTarefa = "osu-tracker"
$CaminhoAtalho = Join-Path ([Environment]::GetFolderPath("Startup")) "osu-tracker.lnk"

foreach ($Nome in @($NomeTarefa, "osu-tracker coletor")) {
    if (Get-ScheduledTask -TaskName $Nome -ErrorAction SilentlyContinue) {
        if (-not $ManterRodando) { Stop-ScheduledTask -TaskName $Nome -ErrorAction SilentlyContinue }
        Unregister-ScheduledTask -TaskName $Nome -Confirm:$false
        Write-Host "Tarefa '$Nome' removida do Agendador de Tarefas."
    }
}

if (Test-Path $CaminhoAtalho) {
    Remove-Item $CaminhoAtalho
    Write-Host "Atalho removido de $CaminhoAtalho."
}

if (-not $ManterRodando) {
    # Encerra o detector iniciado pelo atalho ou pela janela (o da tarefa ja parou com Stop-ScheduledTask).
    Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe' OR Name = 'python.exe' OR Name = 'osu-tracker.exe'" |
        Where-Object { $_.CommandLine -like "*-m osu_tracker*" -or ($_.Name -eq "osu-tracker.exe" -and $_.CommandLine -like "*detectar*") } |
        ForEach-Object {
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
            Write-Host "osu-tracker encerrado (processo $($_.ProcessId))."
        }
}

Write-Host "Pronto. Seus dados continuam em $env:LOCALAPPDATA\osu-tracker; apague a pasta se quiser remove-los."
