export function parseResult(text) {
  return Object.fromEntries(text.split(/\r?\n/).filter(line=>line.includes('=')).map(line=>{const at=line.indexOf('=');return [line.slice(0,at),line.slice(at+1)];}));
}
export function evaluateFixture(fixture, result) {
  if(fixture!=='circular') throw new Error('Unknown fixture');
  const checks=[];
  const check=(name,ok)=>checks.push({name,ok:Boolean(ok)});
  check('result reports PASS',result.status==='PASS');
  check('six pattern bodies',Number(result.pattern_body_count)===6);
  const angles=String(result.angles_deg||'').split(',').map(Number);
  check('six angles at 60 degree intervals',angles.length===6&&angles.every((angle,i)=>Math.abs(angle-i*60)<=1e-5));
  check('full circle coverage',Number(result.coverage_deg)===360&&Number(result.step_deg)===60);
  check('one merged body',Number(result.merged_body_count)===1);
  check('nonzero merged faces',Number(result.merged_face_count)>0);
  return {ok:checks.every(item=>item.ok),checks};
}
