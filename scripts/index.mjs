import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const config = JSON.parse(await fs.readFile(path.join(root, 'workspace.json'), 'utf8'));
function safe(relative) {
  const target = path.resolve(root, relative);
  const local = path.relative(root, target);
  if (!local || local.startsWith('..') || path.isAbsolute(local) || local.split(path.sep)[0].toLowerCase() === 'missile_project') throw new Error('Unsafe data path');
  return target;
}
async function readJson(file) {
  try { return JSON.parse((await fs.readFile(file,'utf8')).replace(/^\uFEFF/,'')); }
  catch(error) { if(error.code === 'ENOENT') return null; throw error; }
}
async function runs(directory) {
  const found=[];
  async function visit(dir, depth) {
    if(depth > 4) return;
    for(const entry of await fs.readdir(dir,{withFileTypes:true}).catch(error=>{if(error.code==='ENOENT') return []; throw error;})) {
      if(entry.isSymbolicLink()) throw new Error('Symlink is not allowed in run catalog');
      const file=path.join(dir,entry.name);
      if(entry.isDirectory()) await visit(file,depth+1);
      else if(entry.name === 'run.json') {
        const record=await readJson(file);
        if(record.redirectManifest) continue;
        if(!['spaceclaim','fluent_meshing'].includes(record.stage)) throw new Error('Unknown run stage');
        if(!['PASSED','FAILED','STOPPED','GENERATED','RUNNING'].includes(record.status)) throw new Error('Unknown run status');
        found.push({...record, manifest:path.relative(root,file).replaceAll('\\','/'), runtimeVerified:record.status==='PASSED' && record.verification?.runtimeVerified===true});
      }
    }
  }
  await visit(directory,0);
  return found;
}
const historical=await readJson(path.join(safe(config.legacyRoot),'migration-manifest.json'));
const records=[...await runs(path.join(safe(config.dataRoot),'runs')), ...await runs(safe(config.feedbackRoot))];
records.sort((a,b)=>String(a.startedAt).localeCompare(String(b.startedAt)));
const index={schemaVersion:1,updatedAt:new Date().toISOString(),historical:{runtimeVerified:false,note:'历史文件保留原样，未重新运行验收。',manifest:historical?path.relative(root,path.join(safe(config.legacyRoot),'migration-manifest.json')).replaceAll('\\','/'):null,files:historical?.files??[]},runs:records,counts:{historicalFiles:historical?.files?.length??0,runs:records.length,verified:records.filter(x=>x.runtimeVerified).length,failed:records.filter(x=>x.status==='FAILED').length,stopped:records.filter(x=>x.status==='STOPPED').length}};
const output=path.join(safe(config.dataRoot),'index.json');
await fs.mkdir(path.dirname(output),{recursive:true});
await fs.writeFile(output+'.tmp',JSON.stringify(index,null,2)+'\n');
await fs.rename(output+'.tmp',output);
console.log(JSON.stringify({index:path.relative(root,output).replaceAll('\\','/'),...index.counts}));
