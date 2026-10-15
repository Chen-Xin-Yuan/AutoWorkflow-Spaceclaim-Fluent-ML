param([switch]$BackupOnly, [string]$ResumeSnapshot='')
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$root = [IO.Path]::GetFullPath((Split-Path $PSScriptRoot -Parent)).TrimEnd('\')
if ($root -ne 'D:\2_my_sci_train') { throw 'Unexpected workspace root.' }
$protected = Join-Path $root 'missile_project'
if (Test-Path -LiteralPath (Join-Path $root 'data_lib\legacy\migration-manifest.json')) {
    throw 'Migration has already been verified. Use rollback.ps1 to restore; do not migrate the reorganized folders again.'
}
function Checked([string]$p) {
    $full = [IO.Path]::GetFullPath($p).TrimEnd('\')
    if (-not $full.StartsWith($root + '\', [StringComparison]::OrdinalIgnoreCase)) { throw "Path outside workspace: $full" }
    if ($full -eq $protected -or $full.StartsWith($protected + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Protected project path.' }
    return $full
}
function HashFile([string]$p) {
    $stream = [IO.File]::OpenRead($p)
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try { return [Convert]::ToHexString($algorithm.ComputeHash($stream)).ToLowerInvariant() }
    finally { $stream.Dispose(); $algorithm.Dispose() }
}
function MakeDir([string]$p) {
    if ([IO.Path]::GetFullPath($p).TrimEnd('\') -eq $root) { return }
    [void][IO.Directory]::CreateDirectory((Checked $p))
}
function MoveChecked([string]$from, [string]$to) {
    $source = Checked $from; $target = Checked $to
    if (-not (Test-Path -LiteralPath $source)) {
        if (Test-Path -LiteralPath $target) { return }
        throw "Missing source and destination: $source"
    }
    if (Test-Path -LiteralPath $target) { throw "Refusing to overwrite destination: $target" }
    MakeDir (Split-Path $target -Parent)
    try { Move-Item -LiteralPath $source -Destination $target }
    catch {
        if (-not (Test-Path -LiteralPath $source -PathType Container) -or (Test-Path -LiteralPath $target)) { throw }
        # A directory held as another process's cwd cannot be renamed. Copy it
        # without deleting the original; the final hash pass verifies the copy.
        MakeDir $target
        & robocopy $source $target /E /COPY:DAT /DCOPY:DAT /R:1 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
        if ($LASTEXITCODE -ge 8) { throw "Locked-directory copy failed: $source" }
        Write-Output "Copied occupied source; cleanup postponed: $source"
    }
}
function RemoveEmpty([string]$p) {
    $p = Checked $p
    if (-not (Test-Path -LiteralPath $p)) { return }
    if ((Get-ChildItem -LiteralPath $p -Force | Measure-Object).Count -ne 0) { throw "Expected empty directory: $p" }
    Remove-Item -LiteralPath $p
}
$candidates = @('docs', 'prompt', 'mcp_skill', 'spaceclaim', 'fluent_meshing', 'fluent_solution')
if ($ResumeSnapshot) {
    $snapshot = Checked $ResumeSnapshot
    if (-not $snapshot.StartsWith((Join-Path $root 'archive') + '\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Resume snapshot must be under archive.' }
    $manifest=Get-Content -LiteralPath (Join-Path $snapshot 'backup-manifest.json') -Raw | ConvertFrom-Json
    if (-not $manifest.backupVerified -or $manifest.workspaceRoot -ne $root) { throw 'Invalid resume manifest.' }
    $records=@($manifest.files)
    foreach($record in $records) {
        if((HashFile (Checked (Join-Path $snapshot $record.backupPath))) -ne $record.sha256) { throw 'Backup changed before resume.' }
    }
} else {
$stamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')
$snapshot = Checked (Join-Path $root "archive\migration-$stamp")
MakeDir $snapshot
$records = [Collections.Generic.List[object]]::new()
$directoryRecords = [Collections.Generic.List[string]]::new()
$gitStates = @{}
foreach ($relative in @('mcp_skill\spaceclaim\ansys_scdmdoc_mcp','mcp_skill\Fluent_meshing_mcp','mcp_skill\Fluent_skill','mcp_skill\autoCFD')) {
    $gitStates[$relative] = @(& git -C (Join-Path $root $relative) status --porcelain=v1 --untracked-files=normal)
    if ($LASTEXITCODE -ne 0) { throw "Cannot capture Git state: $relative" }
}
foreach ($candidate in $candidates) {
    $source = Checked (Join-Path $root $candidate)
    if (-not (Test-Path -LiteralPath $source -PathType Container)) { throw "Missing original directory: $candidate" }
    $items = @(Get-ChildItem -LiteralPath $source -Recurse -Force)
    if (@($items | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint }).Count) { throw "Reparse point in $candidate; inspect before migration." }
    $backup = Checked (Join-Path $snapshot "files\$candidate")
    MakeDir $backup
    & robocopy $source $backup /E /COPY:DAT /DCOPY:DAT /R:1 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "Backup copy failed: $candidate" }
    $directoryRecords.Add($candidate)
    foreach ($item in $items) {
        $originalRelative = $item.FullName.Substring($root.Length + 1).Replace('\','/')
        if ($item.PSIsContainer) { $directoryRecords.Add($originalRelative); continue }
        $backupRelative = 'files/' + $originalRelative
        $backupPath = Checked (Join-Path $snapshot $backupRelative)
        $hash = HashFile $item.FullName
        if (-not (Test-Path -LiteralPath $backupPath -PathType Leaf) -or (Get-Item -LiteralPath $backupPath -Force).Length -ne $item.Length -or (HashFile $backupPath) -ne $hash) { throw "Backup verification failed: $originalRelative" }
        $records.Add([pscustomobject]@{ originalPath=$originalRelative; backupPath=$backupRelative; size=$item.Length; sha256=$hash; lastWriteTimeUtc=$item.LastWriteTimeUtc.ToString('o') })
    }
    Write-Output "Backed up and verified: $candidate"
}
$manifest = [ordered]@{ schemaVersion=1; createdAt=[DateTime]::UtcNow.ToString('o'); workspaceRoot=$root; protectedDirectory='missile_project'; roots=$candidates; directories=$directoryRecords.ToArray(); gitStates=$gitStates; fileCount=$records.Count; totalBytes=($records | Measure-Object -Property size -Sum).Sum; files=$records.ToArray(); backupVerified=$true }
$manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $snapshot 'backup-manifest.json') -Encoding utf8
Write-Output "BACKUP_VERIFIED=$snapshot; files=$($records.Count)"
}
if ($BackupOnly) { exit 0 }
foreach ($relative in @('data_lib\inputs','data_lib\legacy','data_lib\runs','feedback','ML_training','docx\prompts','docx\skills','experiments')) { MakeDir (Join-Path $root $relative) }
# Existing module folders contain data, not source repositories.
if (-not (Test-Path -LiteralPath (Join-Path $root 'data_lib\legacy\spaceclaim'))) {
    MoveChecked (Join-Path $root 'spaceclaim') (Join-Path $root 'data_lib\legacy\spaceclaim')
}
MoveChecked (Join-Path $root 'fluent_meshing\mesh') (Join-Path $root 'data_lib\legacy\fluent_meshing\reference')
RemoveEmpty (Join-Path $root 'fluent_meshing')
MoveChecked (Join-Path $root 'mcp_skill\spaceclaim\ansys_scdmdoc_mcp') (Join-Path $root 'spaceclaim')
MoveChecked (Join-Path $root 'mcp_skill\Fluent_meshing_mcp') (Join-Path $root 'fluent_meshing')
MoveChecked (Join-Path $root 'mcp_skill\autoCFD') (Join-Path $root 'experiments\autoCFD')
MoveChecked (Join-Path $root 'mcp_skill\Fluent_skill') (Join-Path $root 'docx\skills\Fluent_skill')
foreach ($item in @(Get-ChildItem -LiteralPath (Join-Path $root 'docs') -Force)) { MoveChecked $item.FullName (Join-Path $root ('docx\' + $item.Name)) }
MoveChecked (Join-Path $root 'prompt\prompt_for_sc.md') (Join-Path $root 'docx\prompts\prompt_for_sc.md')
MoveChecked (Join-Path $root 'prompt\params_mine.json') (Join-Path $root 'data_lib\inputs\params_mine.json')
MoveChecked (Join-Path $root 'fluent_meshing\geometry') (Join-Path $root 'data_lib\inputs\fluent_meshing\geometry')
$generated = Join-Path $root 'data_lib\legacy\fluent_meshing\generated'
foreach ($name in @('logs','runs','missile_mesh_workflow_files','missile_mesh.msh.h5')) { MoveChecked (Join-Path $root ('fluent_meshing\' + $name)) (Join-Path $generated $name) }
foreach ($file in @(Get-ChildItem -LiteralPath (Join-Path $root 'fluent_meshing') -Filter '*.trn' -File -Force)) { MoveChecked $file.FullName (Join-Path $generated $file.Name) }
function Destination([string]$original) {
    $meshPrefix = 'mcp_skill/Fluent_meshing_mcp/'
    if ($original.StartsWith($meshPrefix)) {
        $suffix = $original.Substring($meshPrefix.Length)
        if ($suffix.StartsWith('geometry/')) { return 'data_lib/inputs/fluent_meshing/' + $suffix }
        if ($suffix -match '^(logs/|runs/|missile_mesh_workflow_files/|missile_mesh\.msh\.h5$|[^/]+\.trn$)') { return 'data_lib/legacy/fluent_meshing/generated/' + $suffix }
        return 'fluent_meshing/' + $suffix
    }
    $pairs = [ordered]@{
        'mcp_skill/spaceclaim/ansys_scdmdoc_mcp/'='spaceclaim/'
        'mcp_skill/autoCFD/'='experiments/autoCFD/'
        'mcp_skill/Fluent_skill/'='docx/skills/Fluent_skill/'
        'fluent_meshing/mesh/'='data_lib/legacy/fluent_meshing/reference/'
        'spaceclaim/'='data_lib/legacy/spaceclaim/'
        'docs/'='docx/'
    }
    foreach ($prefix in $pairs.Keys) { if ($original.StartsWith($prefix)) { return $pairs[$prefix] + $original.Substring($prefix.Length) } }
    if ($original -eq 'prompt/prompt_for_sc.md') { return 'docx/prompts/prompt_for_sc.md' }
    if ($original -eq 'prompt/params_mine.json') { return 'data_lib/inputs/params_mine.json' }
    if ($original.StartsWith('fluent_solution/')) { return $original }
    throw "Unclassified original file: $original"
}
$mapping = [Collections.Generic.List[object]]::new()
foreach ($record in $records) {
    $destinationRelative = Destination $record.originalPath
    $destination = Checked (Join-Path $root $destinationRelative)
    if (-not (Test-Path -LiteralPath $destination -PathType Leaf) -or (Get-Item -LiteralPath $destination -Force).Length -ne $record.size -or (HashFile $destination) -ne $record.sha256) { throw "Migration verification failed: $destinationRelative" }
    $mapping.Add([pscustomobject]@{ originalPath=$record.originalPath; path=$destinationRelative; size=$record.size; sha256=$record.sha256 })
}
@{ schemaVersion=1; snapshot=$snapshot.Substring($root.Length+1).Replace('\','/'); verifiedAt=[DateTime]::UtcNow.ToString('o'); files=$mapping.ToArray(); status='FILES_VERIFIED_RUNTIME_PENDING' } | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $snapshot 'migration-map.json') -Encoding utf8
$historical = @($mapping | Where-Object { $_.path.StartsWith('data_lib/') })
@{ schemaVersion=1; createdAt=[DateTime]::UtcNow.ToString('o'); source='migration'; runtimeVerified=$false; snapshot=$snapshot.Substring($root.Length+1).Replace('\','/'); files=$historical } | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $root 'data_lib\legacy\migration-manifest.json') -Encoding utf8
Write-Output "MIGRATION_READY=$snapshot; files=$($mapping.Count); old empty parents retained until runtime acceptance"
