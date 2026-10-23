$ErrorActionPreference='Stop'
$root=[IO.Path]::GetFullPath((Split-Path $PSScriptRoot -Parent)).TrimEnd('\')
$protected=Join-Path $root 'DATCOM'
function Checked([string]$p) {
    $full=[IO.Path]::GetFullPath($p).TrimEnd('\')
    if(-not $full.StartsWith($root+'\',[StringComparison]::OrdinalIgnoreCase) -or $full -eq $protected -or $full.StartsWith($protected+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Unsafe retirement path.'}
    return $full
}
$records=@(Get-ChildItem -LiteralPath (Checked (Join-Path $root 'data_lib\runs')) -Filter run.json -Recurse -File | ForEach-Object {Get-Content -LiteralPath $_.FullName -Raw | ConvertFrom-Json})
if(-not @($records | Where-Object {$_.stage -eq 'spaceclaim' -and $_.status -eq 'PASSED' -and $_.fixture -eq 'circular' -and $_.verification.runtimeVerified}).Count){throw 'SpaceClaim acceptance is required before retirement.'}
if(-not @($records | Where-Object {$_.stage -eq 'fluent_meshing' -and $_.status -eq 'PASSED' -and $_.profile -eq 'recorded' -and $_.verification.runtimeVerified}).Count){throw 'Fluent acceptance is required before retirement.'}
$archive=Checked (Join-Path $root ('archive\retired-originals-'+[DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')))
[void][IO.Directory]::CreateDirectory($archive)
$oldMesh=Checked (Join-Path $root 'mcp_skill\Fluent_meshing_mcp')
if(Test-Path -LiteralPath $oldMesh){
    $destination=Checked (Join-Path $archive 'Fluent_meshing_mcp')
    [void][IO.Directory]::CreateDirectory($destination)
    foreach($item in @(Get-ChildItem -LiteralPath $oldMesh -Force)){
        if($item.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Unexpected link in old workflow.'}
        $source=Checked $item.FullName
        $target=Checked (Join-Path $destination $item.Name)
        if(Test-Path -LiteralPath $target){throw 'Retirement destination exists.'}
        Move-Item -LiteralPath $source -Destination $target
    }
}
foreach($relative in @('mcp_skill\Fluent_meshing_mcp','mcp_skill\spaceclaim','mcp_skill','docs','prompt')){
    $directory=Checked (Join-Path $root $relative)
    if(Test-Path -LiteralPath $directory){
        $remaining=@(Get-ChildItem -LiteralPath $directory -Recurse -Force)
        if(@($remaining | Where-Object {-not $_.PSIsContainer -or ($_.Attributes -band [IO.FileAttributes]::ReparsePoint)}).Count){throw "Unexpected remaining files: $relative"}
        if($remaining.Count){Write-Warning "Only occupied empty children remain: $relative";continue}
        try {Remove-Item -LiteralPath $directory}
        catch {Write-Warning "Empty directory is still occupied: $relative"}
    }
}
@{schemaVersion=1;retiredAt=[DateTime]::UtcNow.ToString('o');purpose='Retired original copy; active workflow is fluent_meshing at workspace root';path=$archive.Substring($root.Length+1)} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $archive 'retirement.json') -Encoding utf8
Write-Output "Old workflow files retired to $archive"
