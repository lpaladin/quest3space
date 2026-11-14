$ErrorActionPreference='Stop'
$taskProject=Split-Path -Parent $PSScriptRoot
$taskConfig=Get-Content (Join-Path $taskProject 'storage.config.json') -Raw | ConvertFrom-Json
$taskRoot=$taskConfig.storageRoot
$taskPython=Join-Path $taskRoot 'runtime\Scripts\python.exe'
$env:TEMP=$taskConfig.temporaryDirectory;$env:TMP=$env:TEMP
& $taskPython -m pip install --no-cache-dir -r (Join-Path $taskProject 'pc\requirements-offline.txt')
if($LASTEXITCODE -ne 0){throw 'Offline Python dependency installation failed'}
$taskZip=Join-Path $taskRoot 'tools\downloads\OpenMVS_Windows_x64.zip'
$taskMvs=Join-Path $taskRoot 'tools\OpenMVS-2.4.0'
if(!(Test-Path (Join-Path $taskMvs 'vc17\x64\Release\TextureMesh.exe'))){
    & $taskPython (Join-Path $PSScriptRoot 'download.py') 'https://github.com/cdcseacave/openMVS/releases/download/v2.4.0/OpenMVS_Windows_x64.zip' $taskZip --sha256 '0c31660c15c9ebc4c106873cf67564d9570d404aef7a6403451da1b6178b2167'
    if($LASTEXITCODE -ne 0){throw 'Official OpenMVS download verification failed'}
    Expand-Archive -LiteralPath $taskZip -DestinationPath $taskMvs
}
& $taskPython -c 'import open3d; print("Open3D",open3d.__version__)'
if($LASTEXITCODE -ne 0){throw 'Open3D import failed'}
Write-Output "Offline tools ready on $taskRoot"
