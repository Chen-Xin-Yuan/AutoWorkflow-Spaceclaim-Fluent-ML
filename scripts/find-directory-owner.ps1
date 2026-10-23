param([Parameter(Mandatory=$true)][string]$Path)
$ErrorActionPreference='Stop'
$target=[IO.Path]::GetFullPath($Path).TrimEnd('\')
$root=[IO.Path]::GetFullPath((Split-Path $PSScriptRoot -Parent)).TrimEnd('\')
if(-not $target.StartsWith($root+'\',[StringComparison]::OrdinalIgnoreCase) -or $target.StartsWith((Join-Path $root 'DATCOM'),[StringComparison]::OrdinalIgnoreCase)){throw 'Diagnostic target must be a non-protected workspace directory.'}
Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;
public static class DirectoryOwner {
 [StructLayout(LayoutKind.Sequential)] struct Entry { public IntPtr Object; public UIntPtr Pid; public UIntPtr Handle; public uint Access; public ushort Trace; public ushort Type; public uint Flags; public uint Reserved; }
 [DllImport("ntdll.dll")] static extern int NtQuerySystemInformation(int cls,IntPtr info,int length,out int returned);
 [DllImport("kernel32.dll")] static extern IntPtr OpenProcess(uint access,bool inherit,int pid);
 [DllImport("kernel32.dll")] static extern IntPtr GetCurrentProcess();
 [DllImport("kernel32.dll")] static extern bool DuplicateHandle(IntPtr source,IntPtr handle,IntPtr target,out IntPtr duplicate,uint access,bool inherit,uint options);
 [DllImport("kernel32.dll")] static extern uint GetFileType(IntPtr handle);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode)] static extern uint GetFinalPathNameByHandle(IntPtr handle,StringBuilder name,uint size,uint flags);
 [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
 public static string[] Find(string target) {
  var candidates=new HashSet<int>();
  foreach(var p in Process.GetProcesses()) { try { string n=p.ProcessName.ToLowerInvariant(); if(n.Contains("codex")||n=="code"||n=="pwsh"||n=="powershell"||n=="cmd"||n=="python"||n=="node"||n.Contains("terminal")||n=="conhost") candidates.Add(p.Id); } catch{} p.Dispose(); }
  IntPtr buffer=IntPtr.Zero; int size=1048576, returned=0, status;
  do { if(buffer!=IntPtr.Zero)Marshal.FreeHGlobal(buffer); buffer=Marshal.AllocHGlobal(size); status=NtQuerySystemInformation(64,buffer,size,out returned); if(status==unchecked((int)0xc0000004))size=Math.Max(size*2,returned+65536); } while(status==unchecked((int)0xc0000004)&&size<=67108864);
  var result=new List<string>(); var processes=new Dictionary<int,IntPtr>();
  try {
   if(status!=0)throw new Exception("Cannot enumerate directory handles: "+status);
   long count=Marshal.ReadInt64(buffer); int entrySize=Marshal.SizeOf<Entry>();
   for(long i=0;i<count;i++) {
    var entry=Marshal.PtrToStructure<Entry>(IntPtr.Add(buffer,checked(16+(int)i*entrySize))); int pid=checked((int)entry.Pid.ToUInt64()); if(!candidates.Contains(pid))continue;
    IntPtr process; if(!processes.TryGetValue(pid,out process)){process=OpenProcess(0x40,false,pid);processes[pid]=process;} if(process==IntPtr.Zero)continue;
    IntPtr duplicate; if(!DuplicateHandle(process,new IntPtr(unchecked((long)entry.Handle.ToUInt64())),GetCurrentProcess(),out duplicate,0,false,2))continue;
    try { if(GetFileType(duplicate)!=1)continue; var name=new StringBuilder(2048); if(GetFinalPathNameByHandle(duplicate,name,2048,0)==0)continue; string normalized=name.ToString(); if(normalized.StartsWith(@"\\?\"))normalized=normalized.Substring(4); if(String.Equals(normalized.TrimEnd('\\'),target,StringComparison.OrdinalIgnoreCase)){using(var p=Process.GetProcessById(pid)) result.Add(p.ProcessName+" PID="+pid+" started="+p.StartTime.ToString("o"));} } catch{} finally{CloseHandle(duplicate);}
   }
  } finally {foreach(var handle in processes.Values)if(handle!=IntPtr.Zero)CloseHandle(handle);if(buffer!=IntPtr.Zero)Marshal.FreeHGlobal(buffer);}
  return result.ToArray();
 }
}
'@
[DirectoryOwner]::Find($target) | Select-Object -Unique
