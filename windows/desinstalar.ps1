# Tira o osu-tracker do logon do Windows e encerra o que estiver rodando.
# Seus dados (configuracao, banco e log) ficam onde estao.
#
# Uso, no PowerShell, a partir da pasta do projeto:
#   powershell -ExecutionPolicy Bypass -File windows\desinstalar.ps1

$ErrorActionPreference = "Stop"

$NomeTarefa = "osu-tracker"
$CaminhoAtalho = Join-Path ([Environment]::GetFolderPath("Startup")) "osu-tracker.lnk"

foreach ($Nome in @($NomeTarefa, "osu-tracker coletor")) {
    if (Get-ScheduledTask -TaskName $Nome -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $Nome -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $Nome -Confirm:$false
        Write-Host "Tarefa '$Nome' removida do Agendador de Tarefas."
    }
}

if (Test-Path $CaminhoAtalho) {
    Remove-Item $CaminhoAtalho
    Write-Host "Atalho removido de $CaminhoAtalho."
}

# Encerra o detector iniciado pelo atalho (o da tarefa ja parou com Stop-ScheduledTask).
Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe' OR Name = 'python.exe'" |
    Where-Object { $_.CommandLine -like "*-m osu_tracker*" } |
    ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force
        Write-Host "osu-tracker encerrado (processo $($_.ProcessId))."
    }

Write-Host "Pronto. Seus dados continuam em $env:LOCALAPPDATA\osu-tracker; apague a pasta se quiser remove-los."
