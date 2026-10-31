param(
    [Parameter(Position=0)]
    [ValidateSet('spaceclaim','fluent_meshing','verify-spaceclaim','verify-meshing','spaceclaim-mcp','index','db','watch','control','dispatch','review','train','docs')]
    [string]$Mode='index',
    [string]$Geometry,
    [string[]]$WorkflowArgs=@(),
    # Remaining positional arguments are merged into WorkflowArgs, so
    # `run.ps1 db check` equals `run.ps1 db -WorkflowArgs @('check')`.
    # Keep this file ASCII: Windows PowerShell 5.1 reads .ps1 without BOM as ANSI.
    [Parameter(ValueFromRemainingArguments=$true)]
    [string[]]$RemainingArgs=@()
)
if ($RemainingArgs.Count) { $WorkflowArgs = @($WorkflowArgs) + @($RemainingArgs) }
$ErrorActionPreference='Stop'
$root=Split-Path $PSScriptRoot -Parent
$node=(Get-Command node.exe -ErrorAction Stop).Source
$python=$null
function Get-Python {
    if (-not $script:python) { $script:python=(Get-Command python.exe -ErrorAction Stop).Source }
    return $script:python
}
switch($Mode) {
    'db' { & (Get-Python) (Join-Path $PSScriptRoot 'db.py') @WorkflowArgs }
    'watch' { & (Get-Python) (Join-Path $PSScriptRoot 'watch.py') @WorkflowArgs }
    'control' { & (Get-Python) (Join-Path $PSScriptRoot 'control.py') @WorkflowArgs }
    'dispatch' { & (Get-Python) (Join-Path $PSScriptRoot 'dispatch.py') @WorkflowArgs }
    'review' { & (Get-Python) (Join-Path $PSScriptRoot 'review.py') @WorkflowArgs }
    'train' { & (Get-Python) (Join-Path $PSScriptRoot 'train.py') @WorkflowArgs }
    'docs' { & (Get-Python) (Join-Path $PSScriptRoot 'docs.py') @WorkflowArgs }
    'spaceclaim' { & $node (Join-Path $root 'spaceclaim\scripts\missile.mjs') @WorkflowArgs }
    'spaceclaim-mcp' {
        # No banners, captures or PowerShell object formatting on JSON-RPC stdout.
        if ($WorkflowArgs.Count) { throw 'MCP mode accepts no workflow arguments.' }
        if (-not ('WorkspaceMcpRelay' -as [type])) {
            Add-Type -TypeDefinition @'
using System;
using System.Diagnostics;
using System.Threading.Tasks;
public static class WorkspaceMcpRelay {
 public static int Run(string executable,string server) {
  var info=new ProcessStartInfo(executable) {UseShellExecute=false,CreateNoWindow=true,RedirectStandardInput=true,RedirectStandardOutput=true,RedirectStandardError=true};
  info.ArgumentList.Add(server);
  info.WorkingDirectory=System.IO.Path.GetDirectoryName(server);
  using(var child=Process.Start(info)) {
   var input=Task.Run(async()=>{try {await Console.OpenStandardInput().CopyToAsync(child.StandardInput.BaseStream);child.StandardInput.Close();}catch(System.IO.IOException){}catch(ObjectDisposedException){}});
   var output=child.StandardOutput.BaseStream.CopyToAsync(Console.OpenStandardOutput());
   var errors=child.StandardError.BaseStream.CopyToAsync(Console.OpenStandardError());
   child.WaitForExit();
   Task.WaitAll(output,errors);
   return child.ExitCode;
  }
 }
}
'@
        }
        exit [WorkspaceMcpRelay]::Run($node,(Join-Path $root 'spaceclaim\server.mjs'))
    }
    'fluent_meshing' { & (Join-Path $root 'fluent_meshing\run_meshing.cmd') @WorkflowArgs }
    'verify-spaceclaim' { & $node (Join-Path $root 'spaceclaim\scripts\verify-geometry.mjs') @WorkflowArgs }
    'verify-meshing' {
        # The model must be named explicitly: reusing a historical geometry would
        # not prove that this workflow works on the newly generated model.
        if (-not $Geometry) { throw 'verify-meshing requires -Geometry <path to .scdoc>; there is no default model.' }
        $resolved=(Resolve-Path -LiteralPath $Geometry -ErrorAction Stop).Path
        & (Join-Path $root 'fluent_meshing\run_meshing.cmd') --geometry $resolved @WorkflowArgs
    }
    # index is now a read-only DB view; index.mjs was retired (it scanned the disk
    # and had drifted: 14 of 24 entries pointed at deleted directories).
    'index' { & (Get-Python) (Join-Path $PSScriptRoot 'db.py') export --format index }
}
exit $LASTEXITCODE
