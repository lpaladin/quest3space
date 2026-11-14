param([string]$Python='C:\Users\hy\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe')
$ErrorActionPreference='Stop'
$taskProject=Split-Path -Parent $PSScriptRoot
$taskConfig=Get-Content -LiteralPath (Join-Path $taskProject 'storage.config.json') -Raw | ConvertFrom-Json
$taskRuntime=Join-Path $taskConfig.storageRoot 'runtime'
foreach($taskDir in @($taskConfig.storageRoot,$taskConfig.captureDirectory,$taskConfig.reconstructionDirectory,$taskConfig.temporaryDirectory,$taskConfig.exportDirectory)) {New-Item -ItemType Directory -Path $taskDir -Force | Out-Null}
$env:TEMP=$taskConfig.temporaryDirectory;$env:TMP=$env:TEMP
if(!(Test-Path -LiteralPath "$taskRuntime\Scripts\python.exe")){& $Python -m venv $taskRuntime;if($LASTEXITCODE -ne 0){throw 'venv creation failed'}}
& "$taskRuntime\Scripts\python.exe" -m pip install --disable-pip-version-check --no-cache-dir -r (Join-Path $taskProject 'pc\requirements.txt')
if($LASTEXITCODE -ne 0){throw 'PC dependencies failed to install'}
