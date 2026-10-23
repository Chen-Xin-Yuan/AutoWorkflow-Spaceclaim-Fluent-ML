param(
    [string]$Label = '',
    [string[]]$Include = @('spaceclaim', 'fluent_meshing', 'scripts', 'workspace.json', 'AGENTS.md', 'README.md')
)
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Split-Path $PSScriptRoot -Parent)).TrimEnd('\')
$protected = Join-Path $root 'DATCOM'

function Checked([string]$path) {
    $full = [IO.Path]::GetFullPath($path).TrimEnd('\')
    if (-not $full.StartsWith($root + '\', [StringComparison]::OrdinalIgnoreCase)) { throw "Unsafe path outside the workspace: $full" }
    if ($full -eq $protected -or $full.StartsWith($protected + '\', [StringComparison]::OrdinalIgnoreCase)) { throw "Protected project path: $full" }
    return $full
}

$stamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')
$name = if ($Label) { "snapshot-$Label-$stamp" } else { "snapshot-$stamp" }
$destination = Checked (Join-Path $root (Join-Path 'archive' $name))
if (Test-Path -LiteralPath $destination) { throw "Snapshot destination already exists: $destination" }

$manifest = New-Object System.Collections.Generic.List[object]
$totalBytes = 0

foreach ($item in $Include) {
    $source = Checked (Join-Path $root $item)
    if (-not (Test-Path -LiteralPath $source)) { throw "Snapshot source is missing: $source" }
    $relative = $source.Substring($root.Length + 1)
    $target = Join-Path $destination $relative
    [void][IO.Directory]::CreateDirectory((Split-Path $target -Parent))
    if (Test-Path -LiteralPath $source -PathType Container) {
        # /E copies subdirectories including empty ones; /COPY:DAT keeps data, attributes and timestamps.
        # /DCOPY:T keeps directory timestamps; /R:1 /W:1 avoids long retry loops on locked files.
        & robocopy $source $target /E /COPY:DAT /DCOPY:T /R:1 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
        if ($LASTEXITCODE -ge 8) { throw "robocopy failed for $source with exit code $LASTEXITCODE" }
        $files = @(Get-ChildItem -LiteralPath $target -Recurse -Force -File)
    } else {
        Copy-Item -LiteralPath $source -Destination $target -Force
        $files = @(Get-Item -LiteralPath $target -Force)
    }
    foreach ($file in $files) {
        $destinationRelative = $file.FullName.Substring($destination.Length + 1).Replace('\', '/')
        $totalBytes += $file.Length
        $manifest.Add([ordered]@{
            path     = $destinationRelative
            size     = $file.Length
            sha256   = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
            modified = $file.LastWriteTimeUtc.ToString('o')
        })
    }
    Write-Output ("copied {0,-16} {1,6} files" -f $item, $files.Count)
}

# Git bundles protect committed history independently of the copied .git directories.
foreach ($repo in @('spaceclaim', 'fluent_meshing')) {
    $source = Checked (Join-Path $root $repo)
    if (-not (Test-Path -LiteralPath (Join-Path $source '.git'))) { continue }
    $bundle = Join-Path $destination "$repo.bundle"
    $git = (Get-Command git.exe -ErrorAction Stop).Source
    # git writes progress and CRLF notes to stderr; those are not failures, so the
    # global ErrorActionPreference must not turn them into terminating errors.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $bundleOutput = & $git -C $source bundle create $bundle --all 2>&1
    $bundleCode = $LASTEXITCODE
    $ErrorActionPreference = $previous
    $bundleOutput | ForEach-Object { Write-Output "  git: $_" }
    if ($bundleCode -ne 0) {
        # A repository without commits cannot be bundled; the copied .git tree and
        # the untouched working copy remain the record for that repository.
        Write-Output "  note: $repo has no bundle (exit $bundleCode); copied .git tree is the record"
        if (Test-Path -LiteralPath $bundle) { Remove-Item -LiteralPath $bundle -Force }
        continue
    }
    $file = Get-Item -LiteralPath $bundle
    $totalBytes += $file.Length
    $manifest.Add([ordered]@{
        path     = $file.Name
        size     = $file.Length
        sha256   = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        modified = $file.LastWriteTimeUtc.ToString('o')
    })
}

# Git state of each repository, so untracked content is recoverable even without reading .git.
$status = New-Object System.Collections.Generic.List[string]
foreach ($repo in @('spaceclaim', 'fluent_meshing')) {
    $source = Checked (Join-Path $root $repo)
    if (-not (Test-Path -LiteralPath (Join-Path $source '.git'))) { continue }
    $git = (Get-Command git.exe -ErrorAction Stop).Source
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $status.Add("=== $repo status --porcelain=v1 ===")
    $status.Add(((& $git -C $source status --porcelain=v1 2>&1) | Out-String).TrimEnd())
    $status.Add("=== $repo diff --stat (unstaged) ===")
    $status.Add(((& $git -C $source diff --stat 2>&1) | Out-String).TrimEnd())
    $status.Add("=== $repo diff --cached --stat ===")
    $status.Add(((& $git -C $source diff --cached --stat 2>&1) | Out-String).TrimEnd())
    $status.Add("=== $repo rev-parse HEAD ===")
    $status.Add(((& $git -C $source rev-parse HEAD 2>&1) | Out-String).TrimEnd())
    $ErrorActionPreference = $previous
}
$status | Set-Content -LiteralPath (Join-Path $destination 'git-state.txt') -Encoding utf8
$file = Get-Item -LiteralPath (Join-Path $destination 'git-state.txt')
$totalBytes += $file.Length
$manifest.Add([ordered]@{
    path     = $file.Name
    size     = $file.Length
    sha256   = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    modified = $file.LastWriteTimeUtc.ToString('o')
})

$record = [ordered]@{
    schemaVersion            = 1
    createdAt                = [DateTime]::UtcNow.ToString('o')
    workspaceRoot            = $root
    included                 = $Include
    excluded                 = @('DATCOM', 'archive', 'data_lib', 'feedback', 'docx', 'experiments', 'fluent_solution', 'ML_training', '.vscode')
    fileCount                = $manifest.Count
    totalBytes               = $totalBytes
    note                     = 'Complete snapshot of both repositories (tracked, untracked, local configuration, dependencies and Git metadata) plus the root entry points and their documentation. Runtime data lives outside these paths and is intentionally not copied.'
}
$record | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $destination 'snapshot.json') -Encoding utf8
$manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $destination 'file-manifest.json') -Encoding utf8

# Verify the copy against the manifest before declaring the snapshot usable.
$verify = New-Object System.Collections.Generic.List[object]
$manifestPath = Join-Path $destination 'file-manifest.json'
$expected = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
$missing = 0; $mismatched = 0; $skipped = 0
foreach ($entry in $expected) {
    if ($entry.path -eq 'file-manifest.json' -or $entry.path -eq 'snapshot.json') { $skipped += 1; continue }
    $target = Join-Path $destination $entry.path
    if (-not (Test-Path -LiteralPath $target)) { $missing += 1; $verify.Add("missing: $($entry.path)"); continue }
    $actual = Get-FileHash -LiteralPath $target -Algorithm SHA256
    if ($actual.Hash.ToLowerInvariant() -ne $entry.sha256) { $mismatched += 1; $verify.Add("hash mismatch: $($entry.path)") }
}
$verify | Set-Content -LiteralPath (Join-Path $destination 'verification.txt') -Encoding utf8
Write-Output ""
Write-Output "snapshot : $destination"
Write-Output "files    : $($manifest.Count) ($([Math]::Round($totalBytes / 1MB, 1)) MB)"
Write-Output "verified : $($manifest.Count - $skipped - $missing - $mismatched) ok, $missing missing, $mismatched hash mismatches"
if ($missing -or $mismatched) { exit 1 }
exit 0
