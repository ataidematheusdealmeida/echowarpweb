# Execute no RMM como SYSTEM/administrador, em PowerShell 64 bits.
# Usa a conta real da tarefa EchoWarp para localizar a lista de bloqueios.
$ErrorActionPreference = 'Stop'
if ([Environment]::Is64BitOperatingSystem -and -not [Environment]::Is64BitProcess) {
    throw 'Execute este script no PowerShell 64 bits do RMM.'
}
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Execute como SYSTEM ou administrador.'
}
$task = Get-ScheduledTask -TaskName 'EchoWarp' -TaskPath '\'
$taskAccount = ([string]$task.Principal.UserId).Trim()
if (-not $taskAccount) {
    throw 'A tarefa nao informa uma conta de usuario. Confira o usuario da tarefa EchoWarp.'
}
try {
    if ($taskAccount -match '^S-1-') {
        $taskSid = New-Object Security.Principal.SecurityIdentifier($taskAccount)
    } else {
        $account = New-Object Security.Principal.NTAccount($taskAccount)
        $taskSid = $account.Translate([Security.Principal.SecurityIdentifier])
    }
} catch {
    throw "Nao foi possivel resolver a conta da tarefa '$taskAccount' para SID. Nenhum bloqueio foi alterado."
}
switch ($taskSid.Value) {
    'S-1-5-18' { $profileDir = Join-Path $env:SystemRoot 'System32\config\systemprofile' }
    'S-1-5-19' { $profileDir = Join-Path $env:SystemRoot 'ServiceProfiles\LocalService' }
    'S-1-5-20' { $profileDir = Join-Path $env:SystemRoot 'ServiceProfiles\NetworkService' }
    default {
        $profileKey = 'Registry::HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList\' + $taskSid.Value
        $profileDir = [Environment]::ExpandEnvironmentVariables((Get-ItemProperty -LiteralPath $profileKey -Name ProfileImagePath).ProfileImagePath)
    }
}
$programRoot = $env:ProgramW6432
if (-not $programRoot) { $programRoot = $env:ProgramFiles }
$exe = Join-Path $programRoot 'EchoWarp\EchoWarp.exe'
if (-not (Test-Path -LiteralPath $exe)) { throw "Executavel nao encontrado: $exe" }
$startupPath = Join-Path (Split-Path -Parent $exe) 'startup.xml'
$logDir = Join-Path $env:ProgramData 'EchoWarp\Logs'
$passwordToRedact = ''
$startupConfig = $null
if (Test-Path -LiteralPath $startupPath) {
    $startupConfig = Import-Clixml -LiteralPath $startupPath
    if ($startupConfig.LogDir) { $logDir = [string]$startupConfig.LogDir }
    if ([string]$startupConfig.Arguments -match '--password(?:=|\s+)(?:"([^"]*)"|(\S+))') {
        if ($Matches[1]) { $passwordToRedact = $Matches[1] } else { $passwordToRedact = $Matches[2] }
    }
}
if (-not $startupConfig -or [string]::IsNullOrWhiteSpace([string]$startupConfig.Arguments)) {
    throw "Configuracao de inicializacao ausente ou incompleta: $startupPath. Execute novamente o instalador original para recuperar os argumentos sem adivinhar senha/dispositivo."
}
if ([IO.Path]::GetFullPath([string]$startupConfig.Executable) -ne [IO.Path]::GetFullPath($exe)) {
    throw 'O startup.xml aponta para outro executavel. Nenhuma tarefa ou lista foi alterada.'
}
function Hide-EchoWarpSecret([string]$Text) {
    if ($passwordToRedact) { $Text = $Text.Replace($passwordToRedact, '[REDACTED]') }
    return [regex]::Replace($Text, '(?i)(--password(?:=|\s+))(?:"[^"]*"|\S+)', '$1[REDACTED]')
}
$banFile = Join-Path $profileDir 'AppData\Roaming\echowarp\ban_list.yaml'
$originalBanFile = $banFile
$directArguments = [string]$startupConfig.Arguments
if ($startupConfig -and [string]$startupConfig.Arguments -match '--ban-file(?:=|\s+)(?:"([^"]*)"|(\S+))') {
    $configuredBanFile = if ($Matches[1]) { $Matches[1] } else { $Matches[2] }
    if ([IO.Path]::IsPathRooted($configuredBanFile)) {
        $banFile = [IO.Path]::GetFullPath($configuredBanFile)
    } else {
        $banFile = [IO.Path]::GetFullPath((Join-Path (Split-Path -Parent $exe) $configuredBanFile))
    }
    $originalBanFile = $banFile
} else {
    # Caminho explicito evita depender do APPDATA herdado pela tarefa SYSTEM.
    $banFile = Join-Path $env:ProgramData 'EchoWarp\ban_list.yaml'
    $directArguments += ' --ban-file "' + $banFile + '"'
}
$runtimeLog = Join-Path $logDir 'echowarp.runtime.log'
if ($directArguments -notmatch '(?:^|\s)--log-file(?:=|\s)') {
    $directArguments += ' --log-file "' + $runtimeLog + '"'
} elseif ($directArguments -match '--log-file(?:=|\s+)(?:"([^"]*)"|(\S+))') {
    $runtimeLog = if ($Matches[1]) { $Matches[1] } else { $Matches[2] }
    if (-not [IO.Path]::IsPathRooted($runtimeLog)) {
        $runtimeLog = Join-Path (Split-Path -Parent $exe) $runtimeLog
    }
}
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
New-Item -ItemType Directory -Path (Split-Path -Parent $banFile) -Force | Out-Null
$backupStamp = (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N')
Export-ScheduledTask -TaskName 'EchoWarp' -TaskPath '\' |
    Set-Content -LiteralPath (Join-Path (Split-Path -Parent $exe) "task.backup-$backupStamp.xml") -Encoding UTF8
Write-Output "Conta da tarefa: $taskAccount (SID $($taskSid.Value))"
Write-Output "Lista de bloqueios: $banFile"

Disable-ScheduledTask -TaskName 'EchoWarp' -TaskPath '\' | Out-Null
try {
    Stop-ScheduledTask -TaskName 'EchoWarp' -TaskPath '\'
    for ($stopAttempt = 0; $stopAttempt -lt 10; $stopAttempt++) {
        if ((Get-ScheduledTask -TaskName 'EchoWarp' -TaskPath '\').State -ne 'Running') { break }
        Start-Sleep -Seconds 1
    }
    if ((Get-ScheduledTask -TaskName 'EchoWarp' -TaskPath '\').State -eq 'Running') {
        throw 'A tarefa nao encerrou; lista nao alterada.'
    }
    $processes = @(Get-Process -Name EchoWarp -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -eq $exe })
    foreach ($process in $processes) {
        Stop-Process -InputObject $process -Force
        $process.WaitForExit(10000) | Out-Null
        if (-not $process.HasExited) { throw 'O processo nao encerrou; lista nao alterada.' }
    }
    foreach ($file in @($originalBanFile, $banFile) | Select-Object -Unique) {
        if (Test-Path -LiteralPath $file) {
            $backupName = [IO.Path]::GetFileName($file) + '.backup-' + $backupStamp
            Rename-Item -LiteralPath $file -NewName $backupName
            Write-Output "Lista antiga preservada: $file.backup-$backupStamp"
        } else {
            Write-Output "Nenhuma lista existente em: $file"
        }
    }
    foreach ($serviceName in @('AudioEndpointBuilder', 'Audiosrv')) {
        $audioService = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
        if ($audioService -and $audioService.Status -ne 'Running') { Start-Service -Name $serviceName }
    }
    $action = New-ScheduledTaskAction -Execute $exe -Argument $directArguments -WorkingDirectory (Split-Path -Parent $exe)
    Set-ScheduledTask -TaskName 'EchoWarp' -TaskPath '\' -Action $action | Out-Null
    Write-Output 'Inicializacao corrigida: tarefa executa EchoWarp.exe diretamente, com os argumentos existentes.'
} finally {
    Enable-ScheduledTask -TaskName 'EchoWarp' -TaskPath '\' | Out-Null
    Start-ScheduledTask -TaskName 'EchoWarp' -TaskPath '\'
}

$running = $null
$lastPid = $null
$stableSamples = 0
for ($attempt = 0; $attempt -lt 45; $attempt++) {
    Start-Sleep -Seconds 1
    $running = Get-Process -Name EchoWarp -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -eq $exe } | Select-Object -First 1
    if ($running -and $running.Id -eq $lastPid) { $stableSamples++ } else { $stableSamples = 0 }
    $lastPid = if ($running) { $running.Id } else { $null }
    if ($stableSamples -ge 2) { break }
}
if (-not $running -or $stableSamples -lt 2) {
    $taskNow = Get-ScheduledTask -TaskName 'EchoWarp' -TaskPath '\'
    $info = Get-ScheduledTaskInfo -TaskName 'EchoWarp' -TaskPath '\'
    Write-Output "Estado da tarefa: $($taskNow.State)"
    Write-Output ('Ultimo resultado: 0x{0:X8}' -f ([long]$info.LastTaskResult))
    Write-Output "Ultima execucao: $($info.LastRunTime)"
    $diagnosticLogs = @($runtimeLog, (Join-Path $logDir 'startup.log'), (Join-Path $logDir 'echowarp.stdout.log'), (Join-Path $logDir 'echowarp.stderr.log'))
    foreach ($path in $diagnosticLogs | Select-Object -Unique) {
        if (Test-Path -LiteralPath $path) {
            Write-Output "LOG: $path (atualizado em $((Get-Item -LiteralPath $path).LastWriteTime))"
            Write-Output (Hide-EchoWarpSecret (Get-Content -LiteralPath $path -Tail 25 | Out-String))
        } else {
            Write-Output "LOG AUSENTE: $path"
        }
    }
    foreach ($action in $taskNow.Actions) {
        Write-Output "Executavel da tarefa: $($action.Execute)"
        Write-Output "Diretorio da tarefa: $($action.WorkingDirectory)"
    }
    throw 'Processo nao confirmado. Envie a saida de diagnostico acima para identificar a falha de inicializacao.'
}
Write-Output "EchoWarp reiniciado pela tarefa existente (PID $($running.Id)). Clique em Conectar no painel web."
Write-Output "Log do EchoWarp: $runtimeLog"
