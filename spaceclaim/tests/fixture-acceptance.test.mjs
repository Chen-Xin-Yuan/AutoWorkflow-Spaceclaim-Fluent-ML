import test from 'node:test';
import assert from 'node:assert/strict';
import {evaluateFixture,parseResult} from '../lib/fixture-acceptance.mjs';
test('circular acceptance rejects exit-success reports with wrong angles or empty topology',()=>{
  const result=parseResult('status=PASS\r\npattern_body_count=6\r\nangles_deg=0,60,120,180,240,300\r\nstep_deg=60\r\ncoverage_deg=360\r\nmerged_body_count=1\r\nmerged_face_count=30');
  assert.equal(evaluateFixture('circular',result).ok,true);
  assert.equal(evaluateFixture('circular',{...result,angles_deg:'0,60,120,180,240,299'}).ok,false);
  assert.equal(evaluateFixture('circular',{...result,merged_face_count:'0'}).ok,false);
  assert.equal(evaluateFixture('circular',{...result,pattern_body_count:'5'}).ok,false);
  assert.equal(evaluateFixture('circular',{...result,merged_body_count:'2'}).ok,false);
});
test('the acceptance gate refuses an unknown fixture instead of passing it',()=>{
  assert.throws(()=>evaluateFixture('box',{status:'PASS'}),/Unknown fixture/);
});
