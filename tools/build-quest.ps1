param([ValidateSet('Configure','FixSetup','Build')][string]$Step='Build')
$ErrorActionPreference='Stop'
$taskProject=Join-Path (Split-Path -Parent $PSScriptRoot) 'quest-unity'
$taskData='E:\Quest3Space'
$env:TEMP=Join-Path $taskData 'temp';$env:TMP=$env:TEMP
$env:UPM_CACHE_ROOT=Join-Path $taskData 'tools\upm-cache'
$env:GRADLE_USER_HOME=Join-Path $taskData 'tools\gradle-cache'
$env:BEE_CACHE_DIRECTORY=Join-Path $taskData 'tools\bee-cache'
New-Item -ItemType Directory -Force $env:BEE_CACHE_DIRECTORY | Out-Null
$taskSource=Join-Path (Split-Path -Parent $PSScriptRoot) 'unity-source'
Copy-Item -Path "$taskSource\Runtime\*" -Destination "$taskProject\Assets\Scanner" -Force
Copy-Item -LiteralPath "$taskSource\Editor\QuestBuild.cs" -Destination "$taskProject\Assets\Editor\QuestBuild.cs" -Force
$taskLog=Join-Path $taskData "logs\unity-$($Step.ToLower()).log"
$taskArguments=@('-batchmode','-nographics','-projectPath',$taskProject,'-buildTarget','Android','-executeMethod',"QuestBuild.$Step",'-logFile',$taskLog)
if($Step -ne 'FixSetup'){$taskArguments+=@('-quit')}
$taskProcess=Start-Process -FilePath "$taskData\tools\Unity6\Editor\Unity.exe" -ArgumentList $taskArguments -WindowStyle Hidden -PassThru
$taskProcess.WaitForExit()
Get-Content -LiteralPath $taskLog -Tail 25
if($taskProcess.ExitCode -ne 0){throw "Unity $Step failed; see $taskLog"}
if($Step -eq 'Build'){Get-FileHash -LiteralPath "$taskData\builds\quest3space-0.2.0.apk" -Algorithm SHA256}
