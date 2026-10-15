import fs from 'node:fs/promises';
import path from 'node:path';

const [baselineDir, variantDir] = process.argv.slice(2);
async function load(dir) {
  const read = async (name) => JSON.parse(await fs.readFile(path.join(dir, name), 'utf8'));
  const record = await read('run.json');
  const document = await read('params.json');
  const result = await read('result.json');
  const model = await fs.stat(path.join(dir, 'missile.scdoc'));
  return { dir, record, document, result, modelBytes: model.size, modelMtime: new Date(model.mtimeMs).toISOString() };
}
const named = (document, name) => document.regions.find((region) => region.name === name);
const summary = (run) => ({
  run: path.basename(path.dirname(run.dir)),
  status: run.record.status,
  runtimeVerified: run.record.verification.runtimeVerified,
  checkCount: run.record.verification.checks.length,
  failedChecks: run.record.verification.checks.filter((check) => !check.ok).map((check) => `${check.name} :: ${check.detail}`),
  model: { bytes: run.modelBytes, savedAt: run.modelMtime, apiVersion: run.document.apiVersion },
  parameters: {
    L_N: run.document.params.L_N, L: run.document.params.L, D: run.document.params.D,
    L_W: run.document.params.L_W, C_RW: run.document.params.C_RW, C_TW: run.document.params.C_TW, B_W: run.document.params.B_W,
    L_F: run.document.params.L_F, C_RF: run.document.params.C_RF, C_TF: run.document.params.C_TF, B_F: run.document.params.B_F,
    k_nose: run.document.params.k_nose, k_tip: run.document.params.k_tip, t_fin: run.document.params.t_fin,
  },
  inheritedDefaults: { root_overlap: run.document.params.root_overlap, fillet_location: run.document.params.fillet_location, cruciform_count: run.document.params.cruciform_count, enclosure_name: run.document.params.enclosure_name, boi_name: run.document.params.boi_name, model_placement: run.document.params.model_placement, model_offset: run.document.params.model_offset },
  sources: { preset: Object.entries(run.document.sources).filter(([, value]) => value === 'preset').map(([field]) => field), cli: Object.entries(run.document.sources).filter(([, value]) => value === 'cli' || value === 'explicit').map(([field]) => field) },
  derived: { r_f: run.document.geometry?.body?.fillet?.r_f ?? run.document.geometry?.fillet?.r_f, r_t: run.document.geometry?.body?.tip?.r_t ?? run.document.geometry?.tip?.r_t, tip_to_tip: run.document.geometry?.tip_to_tip, nose_tip_x: run.document.geometry?.nose_tip_x, enclosureX: [named(run.document, 'enclosure').xFront, named(run.document, 'enclosure').xBack], boiX: [named(run.document, 'boi').xFront, named(run.document, 'boi').xBack] },
  measured: {
    bodyCount: run.result.modelBodyCount,
    bodyFaces: run.result.faceCount,
    extentsM: run.result.measurements.extentsM,
    volumeM3: run.result.measurements.volumeM3,
    surfaceAreaM2: run.result.measurements.surfaceAreaM2,
    enclosure: run.result.regions.enclosure,
    boi: run.result.regions.boi,
    namedSelections: run.result.namedSelections.names.slice().sort(),
    selectionGroups: Object.keys(run.result.namedSelections.groups).sort(),
    keptObjects: run.result.physics.kept.map((item) => item.name),
    suppressedObjects: run.result.physics.suppressed.map((item) => `${item.type}:${item.name}`),
  },
});
console.log(JSON.stringify({ baseline: summary(await load(baselineDir)), variant: summary(await load(variantDir)) }, null, 2));
