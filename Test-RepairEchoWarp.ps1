# Testa somente a montagem dos argumentos; nao acessa tarefas nem altera arquivos.
$ErrorActionPreference = 'Stop'
$source = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'Repair-EchoWarpBan.ps1') -Raw
$start = $source.IndexOf('$banFile = Join-Path $profileDir')
$end = $source.IndexOf('New-Item -ItemType Directory -Path $logDir', $start)
if ($start -lt 0 -or $end -lt 0) { throw 'Trecho de argumentos nao encontrado.' }
$argumentBlock = [scriptblock]::Create($source.Substring($start, $end - $start))
$profileDir = 'C:\Windows\System32\config\systemprofile'
$exe = 'C:\Program Files\EchoWarp\EchoWarp.exe'
$logDir = 'C:\ProgramData\EchoWarp\Logs'
$startupConfig = @{ Arguments = 'server --no-interactive --device 3 --port 4415 --password "test value"' }
. $argumentBlock
if (-not $directArguments.StartsWith($startupConfig.Arguments)) { throw 'Argumentos originais alterados.' }
if ($banFile -ne (Join-Path $env:ProgramData 'EchoWarp\ban_list.yaml')) { throw 'Caminho explicito incorreto.' }
if ($directArguments -notlike '*--ban-file*' -or $directArguments -notlike '*--log-file*') { throw 'Flags ausentes.' }
$startupConfig = @{ Arguments = 'server --ban-file "C:\Custom\bans.yaml" --log-file "C:\Custom\runtime.log"' }
. $argumentBlock
if ($directArguments -ne $startupConfig.Arguments) { throw 'Flags personalizadas alteradas.' }
if ($banFile -ne 'C:\Custom\bans.yaml' -or $runtimeLog -ne 'C:\Custom\runtime.log') { throw 'Caminhos personalizados incorretos.' }
Write-Output 'OK: argumentos preservados, caminhos explicitos e flags personalizadas validados.'
