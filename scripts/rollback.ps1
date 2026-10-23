param([Parameter(Mandatory=$true)][string]$Snapshot)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$root=[IO.Path]::GetFullPath((Split-Path $PSScriptRoot -Parent)).TrimEnd('\')
$archive=[IO.Path]::GetFullPath((Join-Path $root 'archive')).TrimEnd('\')
function Checked([string]$p) {
    $f=[IO.Path]::GetFullPath($p).TrimEnd('\')
    $protected=Join-Path $root 'DATCOM'
    if (-not $f.StartsWith($root+'\',[StringComparison]::OrdinalIgnoreCase) -or $f -eq $protected -or $f.StartsWith($protected+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe rollback path.' }
    return $f
}
$Snapshot=Checked $Snapshot
if (-not $Snapshot.StartsWith($archive+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Snapshot must be under workspace archive.' }
$manifest=Get-Content -LiteralPath (Join-Path $Snapshot 'backup-manifest.json') -Raw | ConvertFrom-Json
if (-not $manifest.backupVerified -or $manifest.workspaceRoot -ne $root) { throw 'Invalid backup manifest.' }
# Verify the complete backup before replacing any current directory.
foreach($file in $manifest.files) {
    $backup=Checked (Join-Path $Snapshot $file.backupPath)
    if ((Get-FileHash -LiteralPath $backup -Algorithm SHA256).Hash.ToLowerInvariant() -ne $file.sha256) { throw "Backup changed: $($file.originalPath)" }
}
$preserved=Checked (Join-Path $Snapshot ('rollback-preserved\'+[DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')))
[void][IO.Directory]::CreateDirectory($preserved)
foreach($relative in $manifest.roots) {
    $target=Checked (Join-Path $root $relative)
    $source=Checked (Join-Path $Snapshot ('files\'+$relative))
    if (Test-Path -LiteralPath $target) {
        $keep=Checked (Join-Path $preserved $relative)
        Move-Item -LiteralPath $target -Destination $keep
    }
    [void][IO.Directory]::CreateDirectory($target)
    & robocopy $source $target /E /COPY:DAT /DCOPY:DAT /R:1 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "Restore copy failed: $relative" }
}
foreach($file in $manifest.files) {
    $restored=Checked (Join-Path $root $file.originalPath)
    if ((Get-FileHash -LiteralPath $restored -Algorithm SHA256).Hash.ToLowerInvariant() -ne $file.sha256) { throw "Restore verification failed: $($file.originalPath)" }
}
Write-Output 'Original directories restored and verified. New data, feedback, documentation and experiments are preserved.'
Write-Output "Post-migration directory copies preserved at: $preserved"
