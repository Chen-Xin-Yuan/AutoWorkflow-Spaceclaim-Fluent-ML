import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { resolveSpaceClaimExecutable, apiVersionForExecutable, runSpaceClaim } from '../lib/spaceclaim-runner.mjs';
import { beginRun, finishRun, relativePath } from '../lib/workspace-runtime.mjs';
import { parseResult, evaluateFixture } from '../lib/fixture-acceptance.mjs';

// === 输入 / 输出 / 可调默认参数（集中修改区） ===
// 输入：下列源码 fixture；SpaceClaim 安装通过 AWP/本机 setup/执行器环境覆盖解析。
// 输出：--out 覆盖默认根目录；空值保留 workspace.json 解析，运行时创建唯一 run。
// 下列文件名相对于本次 run.dir；request.json/run.json 由共享运行记录模块管理。
// timeout 可由 --timeout 覆盖；几何与验收断言保留在 fixture 和 acceptance 模块中。
const DEFAULT_OUTPUT_DIR='';
const DEFAULT_TIMEOUT_MS=300000;
const MAX_TIMEOUT_MS=1800000;
const FIXTURE_SOURCE_PARTS=['scripts','spaceclaim-circular-repeat-smoke.py'];
const OUTPUT_FILES={script:'fixture.py',model:'circular.scdoc',result:'result.txt',diagnostic:'diagnostic.txt',request:'request.json',error:'error.txt'};

const projectDir=fileURLToPath(new URL('..',import.meta.url));
let outputDir=DEFAULT_OUTPUT_DIR, timeoutMs=DEFAULT_TIMEOUT_MS;
for(let i=2;i<process.argv.length;i++) {
  const option=process.argv[i];
  if(option==='--out') outputDir=process.argv[++i];
  else if(option==='--timeout') timeoutMs=Number(process.argv[++i]);
  else if(option==='--help') { console.log('verify:geometry [--out directory] [--timeout milliseconds]');process.exit(0); }
  else throw new Error('Unknown option: '+option);
}
if(!Number.isInteger(timeoutMs)||timeoutMs<=0||timeoutMs>MAX_TIMEOUT_MS) throw new Error('Invalid timeout');
const run=await beginRun('spaceclaim',projectDir,{outputDir,parameters:{fixture:'circular',timeoutMs}});
let status='FAILED', verification={}, failure=null;
try {
  const source=path.join(projectDir,...FIXTURE_SOURCE_PARTS);
  const executable=await resolveSpaceClaimExecutable();
  const version=await apiVersionForExecutable(executable);
  if(!/^V\d+$/.test(version)) throw new Error('Cannot identify installed API version');
  const script=path.join(run.dir,OUTPUT_FILES.script);
  await fs.writeFile(script,`# Python Script, API Version = ${version}\n`+await fs.readFile(source,'utf8'));
  const model=path.join(run.dir,OUTPUT_FILES.model);
  const resultPath=path.join(run.dir,OUTPUT_FILES.result);
  const diagnostic=path.join(run.dir,OUTPUT_FILES.diagnostic);
  const started=Date.now();
  const env={...process.env,SCDM_RUNTIME_MODEL:model,SCDM_RUNTIME_RESULT:resultPath,SCDM_RUNTIME_DIAGNOSTIC:diagnostic};
  const outcome=await runSpaceClaim({scriptPath:script,paramsPath:path.join(run.dir,OUTPUT_FILES.request),executablePath:executable,timeoutMs,env});
  const modelStat=await fs.stat(model).catch(()=>null);
  const resultStat=await fs.stat(resultPath).catch(()=>null);
  const parsed=parseResult(await fs.readFile(resultPath,'utf8').catch(()=>''));
  const evaluated=evaluateFixture('circular',parsed);
  const checks=[...evaluated.checks,{name:'fresh nonempty model',ok:Boolean(modelStat&&modelStat.size>0&&modelStat.mtimeMs>=started-2000)},{name:'fresh result',ok:Boolean(resultStat&&resultStat.size>0&&resultStat.mtimeMs>=started-2000)},{name:'completed process without timeout',ok:outcome.code===0&&!outcome.timedOut&&!outcome.error}];
  verification={runtimeVerified:checks.every(item=>item.ok),fixture:'circular',checks,result:parsed,elapsedMs:outcome.elapsedMs};
  if(!verification.runtimeVerified) throw new Error(outcome.timedOut?'SpaceClaim timed out':'Fresh geometry acceptance failed');
  status='PASSED';
} catch(error) {
  status='FAILED'; failure=error;
  await fs.writeFile(path.join(run.dir,OUTPUT_FILES.error),`${error.name}: ${error.message}\n`);
}
await finishRun(run,{status,verification,error:failure});
console.log(JSON.stringify({status,runId:run.record.runId,runDir:relativePath(run.workspace,run.dir),verification,error:run.record.error},null,2));
process.exitCode=status==='PASSED'?0:1;
