param([Parameter(Mandatory = $true)][string]$WorkingDirectory)
$ErrorActionPreference = 'Stop'

# Optional offline installer. The normal UI route is Scripts > File > Import Script.
# Never patch an OKWW framework file, replace a whole config, or remove a directory.
$projectRoot = $PSScriptRoot
$workingRoot = (Resolve-Path -LiteralPath $WorkingDirectory).Path
foreach ($requiredFile in @('main.py', 'config.py', 'ok\core\task_manager.py')) {
    if (-not (Test-Path -LiteralPath (Join-Path $workingRoot $requiredFile) -PathType Leaf)) {
        throw "Not a supported OKWW working directory: $workingRoot (missing $requiredFile)"
    }
}
$hashes = Get-Content -LiteralPath (Join-Path $projectRoot 'release\checksums.json') -Raw | ConvertFrom-Json
$runtimeFiles = @('AutoRechallenge.py', 'ArMedicine.py', 'MedicineMonitor.py', 'MedicineFarmEcho.py', 'manifest.json')
foreach ($fileName in $runtimeFiles) {
    $sourcePath = Join-Path $projectRoot $fileName
    $actualHash = (Get-FileHash -LiteralPath $sourcePath -Algorithm SHA256).Hash
    if ($actualHash -ne $hashes.$fileName) { throw "Checksum mismatch: $fileName" }
}

$targetRoot = Join-Path $workingRoot 'ok_import\AutoRechallenge'
if (Test-Path -LiteralPath $targetRoot) {
    $existingManifest = Join-Path $targetRoot 'manifest.json'
    if (-not (Test-Path -LiteralPath $existingManifest)) { throw 'Import folder is occupied by unknown files.' }
    $existingId = (Get-Content -LiteralPath $existingManifest -Raw | ConvertFrom-Json).file_name
    if ($existingId -ne 'AutoRechallenge') { throw 'Import identifier mismatch.' }
}
$backupRoot = Join-Path $projectRoot ('.runtime-backup\' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Path $backupRoot | Out-Null
New-Item -ItemType Directory -Path $targetRoot -Force | Out-Null
foreach ($fileName in $runtimeFiles) {
    $targetPath = Join-Path $targetRoot $fileName
    if (Test-Path -LiteralPath $targetPath) {
        Copy-Item -LiteralPath $targetPath -Destination (Join-Path $backupRoot $fileName)
    }
    Copy-Item -LiteralPath (Join-Path $projectRoot $fileName) -Destination $targetPath -Force
}

Write-Output "Installed: $targetRoot"
Write-Output "Previous files: $backupRoot"
Write-Output 'Restart OKWW, then enable 自动用药监测 in 实时触发 when needed; it defaults to disabled.'
