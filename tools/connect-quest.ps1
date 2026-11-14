param([string]$Apk='', [string]$Serial='', [string]$Server='http://192.168.31.193:8765',[switch]$NewSession)
$ErrorActionPreference='Stop'
$taskProject=Split-Path -Parent $PSScriptRoot
$taskConfig=Get-Content -LiteralPath (Join-Path $taskProject 'storage.config.json') -Raw | ConvertFrom-Json
$taskAdb=Join-Path $taskConfig.storageRoot 'tools\platform-tools\adb.exe'
if(!(Test-Path -LiteralPath $taskAdb)){throw 'Android platform-tools missing on E drive'}
$taskDeviceArgs=@();if($Serial){$taskDeviceArgs=@('-s',$Serial)}
function Run-Adb([string[]]$AdbArgs){& $taskAdb @taskDeviceArgs @AdbArgs;if($LASTEXITCODE -ne 0){throw "adb command failed: $AdbArgs"}}
Run-Adb @('get-state')
if($Apk){Run-Adb @('install','-r',$Apk)}
if(([Uri]$Server).Host -in @('127.0.0.1','localhost')){Run-Adb @('reverse','tcp:8765','tcp:8765')}
$taskTokenPath=Join-Path $taskConfig.storageRoot 'pairing-token.txt'
if(!(Test-Path -LiteralPath $taskTokenPath)){throw 'Start PC service before pairing'}
$taskConnection=Join-Path $taskConfig.temporaryDirectory 'quest-connection.json'
@{server=$Server;token=(Get-Content -LiteralPath $taskTokenPath -Raw).Trim();new_session=[bool]$NewSession} | ConvertTo-Json | Set-Content -LiteralPath $taskConnection -Encoding utf8
Run-Adb @('shell','mkdir','-p','/sdcard/Android/data/space.quest3.scanner/files')
Run-Adb @('push',$taskConnection,'/sdcard/Android/data/space.quest3.scanner/files/connection.json')
Run-Adb @('shell','am','start','-n','space.quest3.scanner/com.unity3d.player.UnityPlayerGameActivity')
Write-Output "Quest paired with $Server. Default connection uses Wi-Fi; keep PC and Quest on the same LAN."
