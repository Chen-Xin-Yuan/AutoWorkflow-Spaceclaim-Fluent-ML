/** File-based run records. Callers pass their project root; no cwd dependencies. */
import fs from 'node:fs/promises';
import { existsSync, createReadStream, readFileSync } from 'node:fs';
import path from 'node:path';
import { randomUUID, createHash } from 'node:crypto';

// 输入：项目目录上一级的 workspace.json，以及调用方 parameters / inputs / outputDir / runId。
// 输出：dataRoot/runs/high/<stage>/<run_id>/；失败产物移至 feedbackRoot/<stage>/<run_id>/。
// workspace.json 覆盖下面的根目录默认值；outputDir 在运行时覆盖默认产物根目录。
// `run_id` 由调用方给出（数据库里的三段式 <geometry_id>__<condition_id>__<时间戳>）；
// 未给出时退回旧的自生成 ID（run-<时间戳>-<随机短串>），只用于不在数据库管下的手工运行。
// 命令名与事件名属于运行记录协议，修改时需同步读取这些记录的调用方。
const WORKSPACE_CONFIG_FILENAME = 'workspace.json';
const DEFAULT_WORKSPACE_ROOTS = {dataRoot:'data_lib', feedbackRoot:'feedback', inputRoot:'data_lib/inputs', legacyRoot:'data_lib/legacy'};
const RUNS_DIRECTORY_NAME = 'runs';
const HIGH_FIDELITY_DIRECTORY_NAME = 'high';
const REQUEST_FILENAME = 'request.json';
const RUN_MANIFEST_FILENAME = 'run.json';
// 运行 ID 会直接当目录名，只允许安全字符；数据库侧生成的是 [a-z0-9_] 与小写十六进制。
const RUN_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.-]*$/;

/** Read the workspace configuration that sits beside the project directory. */
function requireText(file) { return readFileSync(file,'utf8').replace(/^\uFEFF/,''); }

export function workspaceFor(projectDir, env = process.env) {
  const root = path.resolve(projectDir, '..');
  const configPath = path.join(root, WORKSPACE_CONFIG_FILENAME);
  // Run data always lives under the project's workspace, never under the caller's
  // temporary directory: a restricted session may refuse writes to %TEMP%, and the
  // workspace rules require runtime artifacts below the configured data root.
  if (!existsSync(configPath)) return {root, ...DEFAULT_WORKSPACE_ROOTS};
  return {root, configPath, ...JSON.parse(requireText(configPath))};
}
export function workspacePath(workspace, relative) {
  const file=path.resolve(workspace.root,relative);
  const local=path.relative(workspace.root,file);
  if(!local || local.startsWith('..') || path.isAbsolute(local) || local.split(path.sep)[0].toLowerCase()==='datcom') throw new Error('Unsafe workspace data path');
  return file;
}
export function relativePath(workspace,file) { return path.relative(workspace.root,path.resolve(file)).replaceAll('\\','/'); }
export async function hashFile(file) {
  const hash=createHash('sha256');
  for await (const chunk of createReadStream(file)) hash.update(chunk);
  return hash.digest('hex');
}
export async function inputRecord(workspace,file) {
  const resolved=path.resolve(file);
  const local=path.relative(workspace.root,resolved).split(path.sep)[0];
  if(local.toLowerCase()==='datcom') throw new Error('Protected project input');
  return {path:relativePath(workspace,resolved),sha256:await hashFile(resolved),size:(await fs.stat(resolved)).size};
}
export async function beginRun(stage, projectDir, {runId='',outputDir='',parameters={},inputs=[],env=process.env,fixture=''}={}) {
  const workspace=workspaceFor(projectDir,env);
  // 调用方给了 runId 就用它：数据库、队列指针、审核记录靠它对齐。没给才自生成（手工运行）。
  const resolvedRunId=runId || 'run-'+new Date().toISOString().replace(/[-:.TZ]/g,'').slice(0,14)+'-'+randomUUID().slice(0,8);
  if(!RUN_ID_PATTERN.test(resolvedRunId)) throw new Error(`Unsafe run id '${resolvedRunId}'`);
  const dir=outputDir ? path.join(path.resolve(outputDir),resolvedRunId) : workspacePath(workspace,path.join(workspace.dataRoot,RUNS_DIRECTORY_NAME,HIGH_FIDELITY_DIRECTORY_NAME,stage,resolvedRunId));
  const protectedPath=path.join(workspace.root,'DATCOM');
  if(dir.toLowerCase()===protectedPath.toLowerCase() || dir.toLowerCase().startsWith((protectedPath+path.sep).toLowerCase())) throw new Error('Protected project output');
  await fs.mkdir(dir,{recursive:false}).catch(async error=>{if(error.code!=='ENOENT')throw error; await fs.mkdir(path.dirname(dir),{recursive:true}); await fs.mkdir(dir);});
  const record={schemaVersion:1,runId:resolvedRunId,stage,status:'RUNNING',startedAt:new Date().toISOString(),finishedAt:null,fixture,inputs,artifacts:[],verification:{runtimeVerified:false},error:null};
  const run={workspace,dir,record};
  await fs.writeFile(path.join(dir,REQUEST_FILENAME),JSON.stringify(parameters,null,2)+'\n');
  await fs.writeFile(path.join(dir,RUN_MANIFEST_FILENAME),JSON.stringify(record,null,2)+'\n');
  return run;
}
async function artifactList(run) {
  const artifacts=[];
  async function visit(directory) {
    for(const entry of await fs.readdir(directory,{withFileTypes:true})) {
      if(entry.isSymbolicLink()) throw new Error('Unexpected link in run artifacts');
      const file=path.join(directory,entry.name);
      if(entry.isDirectory()) await visit(file);
      else if(entry.name!==RUN_MANIFEST_FILENAME) artifacts.push({path:relativePath(run.workspace,file),size:(await fs.stat(file)).size,sha256:await hashFile(file)});
    }
  }
  await visit(run.dir);
  return artifacts;
}
export async function finishRun(run,{status,verification={},error=null}={}) {
  if(status==='FAILED') {
    const destination=workspacePath(run.workspace,path.join(run.workspace.feedbackRoot,run.record.stage,run.record.runId));
    await fs.mkdir(path.dirname(destination),{recursive:true});
    // The only moved source is the unique directory this invocation created.
    if(path.basename(run.dir)!==run.record.runId && path.basename(run.dir)!==run.record.stage) throw new Error('Unexpected run directory');
    const old=run.dir;
    try { await fs.rename(old,destination); }
    catch(moveError) {
      if(moveError.code!=='EXDEV') throw moveError;
      await fs.cp(old,destination,{recursive:true,errorOnExist:true,force:false});
      // Keep the caller's original copy on a different volume.
    }
    run.dir=destination;
    run.record.pathMapping={from:relativePath(run.workspace,old),to:relativePath(run.workspace,destination)};
  }
  run.record={...run.record,status,finishedAt:new Date().toISOString(),verification:{...verification,runtimeVerified:status==='PASSED'&&verification.runtimeVerified===true},error:error?{type:error.name||error.type||'Error',message:error.message||String(error)}:null};
  run.record.artifacts=await artifactList(run);
  await fs.writeFile(path.join(run.dir,RUN_MANIFEST_FILENAME),JSON.stringify(run.record,null,2)+'\n');
  return run;
}
export function relocateResult(value,from,to) {
  if(typeof value==='string') return value===from || value.startsWith(from+path.sep) ? to+value.slice(from.length) : value;
  if(Array.isArray(value)) return value.map(item=>relocateResult(item,from,to));
  if(value&&typeof value==='object') return Object.fromEntries(Object.entries(value).map(([key,item])=>[key,relocateResult(item,from,to)]));
  return value;
}
