param([int]$Port=8765,[switch]$NoBrowser)
$ErrorActionPreference='Stop'
$taskRoot=Split-Path -Parent $MyInvocation.MyCommand.Path
$taskConfig=Get-Content -LiteralPath (Join-Path $taskRoot 'storage.config.json') -Raw | ConvertFrom-Json
$taskPython=Join-Path $taskConfig.storageRoot 'runtime\Scripts\python.exe'
if(!(Test-Path -LiteralPath $taskPython)){throw 'PC runtime missing. Run tools\setup-pc.ps1 first.'}
$taskLogDir=Join-Path $taskConfig.storageRoot 'logs'
New-Item -ItemType Directory -Force -Path $taskLogDir | Out-Null
$env:TEMP=$taskConfig.temporaryDirectory
$env:TMP=$taskConfig.temporaryDirectory
try {$taskHealth=Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 2} catch {$taskHealth=$null}
if($taskHealth.name -ne 'Quest3Space'){
    $taskProcess=Start-Process -FilePath $taskPython -ArgumentList @('-m','pc.server','--port',"$Port") -WorkingDirectory $taskRoot -RedirectStandardOutput (Join-Path $taskLogDir 'pc-stdout.log') -RedirectStandardError (Join-Path $taskLogDir 'pc-stderr.log') -WindowStyle Hidden -PassThru
    $taskProcess.Id | Set-Content -LiteralPath (Join-Path $taskLogDir 'pc.pid')
    $taskReady=$false
    for($taskTry=0;$taskTry -lt 30;$taskTry++){
        Start-Sleep -Milliseconds 500
        try {$taskHealth=Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 1;if($taskHealth.name -eq 'Quest3Space'){$taskReady=$true;break}}catch{}
        if($taskProcess.HasExited){break}
    }
    if(!$taskReady){throw "PC server failed to start. See $taskLogDir\pc-stderr.log"}
}
Write-Output "Quest3Space PC ready: http://127.0.0.1:$Port"
Write-Output "Data: $($taskConfig.storageRoot)"
if(!$NoBrowser){Start-Process "http://127.0.0.1:$Port"}
