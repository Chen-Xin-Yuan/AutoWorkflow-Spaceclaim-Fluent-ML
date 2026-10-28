/**
 * Headless SpaceClaim runner: writes the request, launches SpaceClaim, then grades the run against
 * fresh artifacts. A zero exit code is never accepted as proof that the geometry is correct.
 */

import { spawn } from "node:child_process";
import { access, mkdir, readFile, readdir, stat, unlink, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { beginRun, finishRun, relocateResult } from "./workspace-runtime.mjs";
import { discoverAwpRoots, readSetupConfig } from "./setup.mjs";
import { buildModelRequest } from "./missile-params.mjs";
import { buildRequestDocument, renderModelScript } from "./missile-template.mjs";

// 输入/输出与可调参数：explicit > preset > missile-schema.mjs 中的参数默认值。
// 安装位置按 SCDM_EXECUTABLE > 手动 setup 配置 > AWP_ROOT<版本> > AWP_ROOT 动态发现。
// buildMissile 输出根按 options.outputDir > SCDM_MISSILE_OUTPUT > workspace.json 的 dataRoot/runs/high；
// 由 workspace-runtime 创建唯一运行目录。验收在内存中完成。成功目录留 params.json、model.py、
// result.json、run.json 和模型文件（脚本报错时还有 diagnostic.txt）；request.json、error.txt
// 和预览图不保留。清单在清理后写定，只列出实际留下的文件。
// 失败时目录仍整份移到 feedback，避免成功位置留下未通过的产物。以下仅集中既有文件名和运行默认值。
// defaultRunRoot 保留原有临时目录回退，仅供其调用方/内部未托管路径使用。
const DEFAULT_TIMEOUT_MS = 300000;
const DEFAULT_EXECUTE = true;
const DEFAULT_MODEL_FILENAME = "missile.scdoc";
const DEFAULT_ACCEPTANCE_TOLERANCE = 2e-3;
const LEGACY_RUN_APPLICATION_DIRECTORY = "ansys-scdmdoc-mcp";
const LEGACY_RUN_DIRECTORY = "missile-runs";
const RUN_ARTIFACT_NAMES = {
  params: "params.json",
  script: "model.py",
  result: "result.json",
  diagnostic: "diagnostic.txt",
  image: "preview.png",
  assemblyImage: "preview-assembly.png",
  axialImage: "preview-axial.png",
  // 共享协议名：必须与 workspace-runtime.mjs 的 RUN_MANIFEST_FILENAME 保持一致。
  manifest: "run.json",
  error: "error.txt",
};

/**
 * The missile body's name. The region names come from the schema (`enclosure_name` / `boi_name`) and
 * travel in the request, so they are read from there rather than repeated here.
 */
const MISSILE_BODY_NAME = "Missile";

/**
 * Named selections for the CFD setup. The face kinds come from the analytic profile, so a run with a
 * different fillet_location maps whatever faces exist without any change here.
 */
const NAMED_SELECTION_GROUPS = {
  tip: "missile_nose_tip",
  cone: "missile_nose",
  shoulder: "missile_body",
  cylinder: "missile_body",
  base: "missile_body",
  fin: "missile_fin",
};

/** The missile points along -X, so the low-x cap is the inlet and the high-x cap the outlet. */
const ENCLOSURE_SELECTIONS = { front: "inlet", wall: "wall", back: "outlet" };

async function exists(file) {
  try { await access(file); return true; } catch { return false; }
}

export function defaultRunRoot(env = process.env) {
  return env.SCDM_MISSILE_OUTPUT || path.join(os.tmpdir(), LEGACY_RUN_APPLICATION_DIRECTORY, LEGACY_RUN_DIRECTORY);
}

export async function resolveSpaceClaimExecutable(env = process.env) {
  const candidates = [];
  if (env.SCDM_EXECUTABLE) candidates.push(env.SCDM_EXECUTABLE);
  const config = await readSetupConfig(env);
  if (config?.mode === "manual" && config.executablePath) candidates.push(config.executablePath);
  for (const item of discoverAwpRoots(env)) candidates.push(path.join(item.root, "scdm", "SpaceClaim.exe"));
  if (env.AWP_ROOT) candidates.push(path.join(path.resolve(env.AWP_ROOT), "scdm", "SpaceClaim.exe"));
  for (const candidate of candidates) {
    const resolved = path.resolve(candidate);
    if (await exists(resolved)) return resolved;
  }
  throw new Error("SpaceClaim.exe not found. Set SCDM_EXECUTABLE, run npm run setup, or configure AWP_ROOT<version>.");
}

/**
 * The installed product version comes from the AWP_ROOT layout (…\\v241\\scdm\\SpaceClaim.exe).
 * Scraping SpaceClaim.Api.V* folders is only a fallback: an installation can ship API folders for
 * other versions, so the highest folder is not necessarily the running product.
 */
export async function apiVersionForExecutable(executablePath, env = process.env) {
  const directory = path.dirname(executablePath);
  const fromLayout = /[\\/]v(\d+)[\\/]scdm$/i.exec(directory);
  if (fromLayout) return `V${fromLayout[1]}`;
  try {
    const { resolveChmPath, apiVersionFromChmPath } = await import("./paths.mjs");
    const version = apiVersionFromChmPath((await resolveChmPath(env)).path);
    if (version) return version;
  } catch {}
  const entries = await readdir(directory, { withFileTypes: true }).catch(() => []);
  const versions = entries
    .map((entry) => /^SpaceClaim\.Api\.(V\d+)$/i.exec(entry.name)?.[1])
    .filter(Boolean)
    .sort((left, right) => Number(right.slice(1)) - Number(left.slice(1)));
  return versions[0] ?? "";
}

function runDirectory(outputDir) {
  const stamp = new Date().toISOString().replace(/[-:.TZ]/g, "").slice(0, 14);
  return path.join(outputDir, `run-${stamp}-${Math.random().toString(36).slice(2, 8)}`);
}

function relativeDifference(actual, expected) {
  if (!Number.isFinite(actual) || !Number.isFinite(expected)) return Number.POSITIVE_INFINITY;
  if (expected === 0) return Math.abs(actual);
  return Math.abs(actual - expected) / Math.abs(expected);
}

/**
 * Acceptance gate. Every check must pass, on freshly produced artifacts, for the run to be
 * reported as runtime-verified.
 */
export function evaluateAcceptance({ request, result, modelPath, modelStat, resultStat, startedAt, regionLabels = {}, tolerance = DEFAULT_ACCEPTANCE_TOLERANCE }) {
  const checks = [];
  const add = (name, ok, detail) => checks.push({ name, ok: Boolean(ok), detail });
  const geometry = request.geometry;
  const params = request.params;
  // The names of the objects the physics keeps, straight from the request so a renamed enclosure is
  // graded against the name that was actually asked for.
  const enclosureName = request.regions?.enclosure?.bodyName ?? "Enclosure";
  const boiName = request.regions?.boi?.bodyName ?? "BOI";
  const keepNames = [MISSILE_BODY_NAME, boiName, enclosureName];
  // Reading a name back out of the host only works while it is ASCII; a non-ASCII name comes back as
  // <unreadable>, and then the String.Equals comparison in namedObjects is the only evidence there
  // is. Both paths are graded, so the parameter is free to be set to anything.
  const nameReadable = /^[\x20-\x7E]+$/.test(enclosureName);
  const expectedFaces = geometry?.expectedAreas?.faces ?? [];
  // Face areas are read at the revolve stage: after the fins are merged the cylinder face is
  // trimmed by the fin intersections, so it no longer equals the analytic revolve value.
  const revolveFaces = result?.diagnostics?.revolveFaces ?? [];
  const faceAreas = revolveFaces.map((face) => face.area).filter((area) => typeof area === "number");
  const closestTo = (target) => faceAreas.reduce((best, area) => (best === null || Math.abs(area - target) < Math.abs(best - target) ? area : best), null);

  add("result.json 由本次运行新生成", Boolean(resultStat) && resultStat.mtimeMs >= startedAt - 2000, resultStat ? new Date(resultStat.mtimeMs).toISOString() : "missing");
  add("status=PASS", result?.status === "PASS", result?.status ?? "missing");
  add("合并后实体数为 1", result?.modelBodyCount === 1, `modelBodyCount=${result?.modelBodyCount}`);

  const extents = result?.measurements?.extentsM;
  const expectedExtents = geometry?.expectedExtents;
  add("轴向长度 ≈ L", relativeDifference(extents?.axial, expectedExtents?.axial ?? params.L) <= tolerance, `axial=${extents?.axial} expected=${expectedExtents?.axial ?? params.L}`);
  add("横向与竖向 ≈ 按相位与片数算出的包围盒", relativeDifference(extents?.lateral, expectedExtents?.lateral) <= tolerance && relativeDifference(extents?.vertical, expectedExtents?.vertical) <= tolerance, `lateral=${extents?.lateral} vertical=${extents?.vertical} expected=${expectedExtents?.lateral}/${expectedExtents?.vertical}`);

  add("旋转体面数与剖面段数一致", revolveFaces.length === expectedFaces.length, `faces=${revolveFaces.length} expected=${expectedFaces.length}`);

  const expectedVolume = geometry?.expectedVolume;
  add("旋转体体积 ≈ 解析值", relativeDifference(result?.diagnostics?.bodyVolumeAfterRevolve, expectedVolume) <= tolerance, `measured=${result?.diagnostics?.bodyVolumeAfterRevolve} expected=${expectedVolume}`);

  const faceDetail = expectedFaces.map((face) => {
    const measured = closestTo(face.area);
    const delta = relativeDifference(measured, face.area);
    return { kind: face.kind, expected: face.area, measured, ok: delta <= tolerance };
  });
  add("每个剖面段对应的面面积 ≈ 解析值", faceDetail.every((face) => face.ok), faceDetail.map((face) => `${face.kind}=${face.measured === null ? "none" : face.measured.toFixed(6)}${face.ok ? "" : " ✗"}`).join(" "));

  const tipFace = faceDetail.find((face) => face.kind === "tip");
  if (tipFace) {
    add(`弹尖倒圆面面积 ≈ r_t 解析值 (r_t=${geometry.body.tip.r_t.toFixed(5)} m)`, tipFace.ok, `measured=${tipFace.measured} expected=${tipFace.expected}`);
  }
  const shoulderFace = faceDetail.find((face) => face.kind === "shoulder");
  if (shoulderFace) {
    add(`交接处倒圆面面积 ≈ r_f 解析值 (r_f=${geometry.body.fillet.r_f.toFixed(5)} m)`, shoulderFace.ok, `measured=${shoulderFace.measured} expected=${shoulderFace.expected}`);
  }

  add(".scdoc 由本次运行新生成", Boolean(modelStat) && modelStat.mtimeMs >= startedAt - 2000, modelStat ? new Date(modelStat.mtimeMs).toISOString() : "missing");

  // Where the fins actually sit. The model frame origin is the nose tip, so the station is literally
  // the distance from the nose; extents, areas and volume cannot see an axial slide. The seed fin is
  // a ruled section loft, so its measured centre of mass is the solid centroid, and the analytic
  // centroid offset converts that back into the visible leading-edge station. This has to be a tight
  // *absolute* tolerance: burying the root along the swept leading edge instead of radially moves
  // the centroid by only 1.2 mm, which a loose relative check would sleep through.
  const stationTolerance = 1e-4;
  const finPositions = result?.finPositions ?? [];
  for (const fin of geometry?.fins ?? []) {
    const measured = finPositions.find((item) => item.name === fin.name);
    const value = measured?.centerOfMassM?.x;
    const station = Number.isFinite(value) ? value - (fin.centroid[0] - fin.leAtSurface) : undefined;
    const fromTip = Number.isFinite(station) ? station - geometry.nose_tip_x : undefined;
    add(`舵面 ${fin.name} 可见前缘距弹尖 ≈ ${fin.leFromNoseTip} m（L_${fin.name === "front" ? "W" : "F"}）`, Number.isFinite(fromTip) && Math.abs(fromTip - fin.leFromNoseTip) <= stationTolerance, `measuredFromTip=${fromTip} expected=${fin.leFromNoseTip} station=${station} centroidMeasured=${value} centroidExpected=${fin.centroid[0]}`);
  }
  // The profile is only defined up to an axial shift, and tip blunting plus the resolved placement
  // are what pick that shift, so the body's own centre of mass is what proves the model ended up
  // where the parameters say.
  add("旋转体轴向质心 ≈ 解析值（弹尖基准与整体轴向位置）", relativeDifference(result?.diagnostics?.bodyCenterOfMassAfterRevolve?.x, geometry?.bodyCenterOfMass) <= 1e-4, `measured=${result?.diagnostics?.bodyCenterOfMassAfterRevolve?.x} expected=${geometry?.bodyCenterOfMass} noseTipX=${geometry?.nose_tip_x} placement=${request.placement?.mode}:${request.placement?.offset}`);

  // Simulation regions: the model, the enclosure and the BOI share one document and one .scdoc.
  const regionNames = ["enclosure", "boi"].filter((name) => request.regions?.[name]);
  if (regionNames.length) {
    const expectedBodies = 1 + regionNames.length;
    add("单一 .scdoc 内实体数正确（导弹 + 各区域）", result?.bodyCount === expectedBodies, `bodyCount=${result?.bodyCount} expected=${expectedBodies} names=${(result?.diagnostics?.bodyNames ?? []).join("/")}`);
  }
  for (const name of regionNames) {
    const expected = request.regions[name];
    const label = regionLabels[name] ?? name;
    const measured = result?.regions?.[name] ?? {};
    add(`${label} 为独立实体`, measured.faces >= 3, `faces=${measured.faces}`);
    add(`${label} 尺寸 ≈ 解析值`, relativeDifference(measured.extentsM?.axial, expected.expectedExtents.axial) <= tolerance && relativeDifference(measured.extentsM?.lateral, expected.expectedExtents.lateral) <= tolerance && relativeDifference(measured.extentsM?.vertical, expected.expectedExtents.vertical) <= tolerance, `axial=${measured.extentsM?.axial} lateral=${measured.extentsM?.lateral} expected=${expected.expectedExtents.axial}/${expected.expectedExtents.lateral}`);
    add(`${label} 体积 ≈ 解析值`, relativeDifference(measured.volumeM3, expected.expectedVolume) <= tolerance, `measured=${measured.volumeM3} expected=${expected.expectedVolume}`);
    // Length alone cannot tell which end a region was shortened at; its axial midpoint can.
    const midpoint = (expected.xFront + expected.xBack) / 2;
    add(`${label} 轴向中位 ≈ 解析值（长度是缩在哪一端的）`, relativeDifference(measured.centerOfMassM?.x, midpoint) <= tolerance, `measured=${measured.centerOfMassM?.x} expected=${midpoint}`);
  }

  // Placement is a property of the pair, not of either region alone, and it is restated here from
  // the parameters instead of being read out of the request: the block that gets centred is the
  // union of the missile and the BOI (which is the BOI, since the BOI encloses the missile), and
  // `model_offset` then nudges the whole block along +X. A wrong mode, a dropped nudge or a flipped
  // sign cannot pass.
  const boiRegion = request.regions?.boi;
  if (regionNames.length === 2 && request.placement && boiRegion) {
    const placement = request.placement;
    const mirror = (params.enclosure_back - params.enclosure_front) / 2;
    const boiSkew = (params.boi_back - params.boi_front) / 2;
    const rule = placement.mode === "ensemble" ? mirror - boiSkew : placement.mode === "missile" ? mirror : 0;
    const station = rule + placement.requested;
    add(`定位规则：${placement.mode} 居中站位 ${rule} + model_offset ${placement.requested} = ${station} m`, Math.abs(placement.offset - station) <= 1e-12, `station=${placement.offset} expected=${station} rule=${rule} model_offset=${placement.requested}`);
    // The measured half: where the BOI's midpoint actually is relative to the enclosure's. Both are
    // read back off the built bodies, and the expected difference follows from the station alone.
    const expectedGap = placement.offset + boiSkew - mirror;
    const measuredEnclosureMid = result?.regions?.enclosure?.centerOfMassM?.x;
    const measuredBoiMid = result?.regions?.boi?.centerOfMassM?.x;
    const measuredGap = (measuredBoiMid ?? NaN) - (measuredEnclosureMid ?? NaN);
    // A millimetre, not 0.1 mm: the enclosure's own measured midpoint carries the command's numeric
    // precision (4e-5 m observed), while anything a ruler would call misplaced is far larger.
    add(`实测：BOI 相对外壳的轴向中位差 ≈ 解析值（居中后按 model_offset 落位）`, Math.abs(measuredGap - expectedGap) <= 1e-3, `measuredGap=${measuredGap} expectedGap=${expectedGap} measuredBoiMid=${measuredBoiMid} measuredEnclosureMid=${measuredEnclosureMid}`);
  }

  // The enclosure must come from the official Prepare > Enclosure command, which produces the body
  // plus a component holding it; the hand-built revolve would satisfy the size checks but not this.
  const enclosure = request.regions?.enclosure;
  if (enclosure) {
    const measured = result?.regions?.enclosure ?? {};
    const components = result?.diagnostics?.componentNames ?? [];
    add("外壳由\"准备 > 外壳\"命令生成并落在独立组件中", enclosure.mode === "enclosure-command" && measured.componentCount >= 1, `mode=${enclosure.mode} componentCount=${measured.componentCount}`);
    add(`外壳组件名为 ${enclosureName}`, result?.diagnostics?.namedObjects?.[enclosureName] === true && (!nameReadable || components.includes(enclosureName)), `namedObjects=${JSON.stringify(result?.diagnostics?.namedObjects)} componentNames=${components.join("/")}`);
    add("外壳 cushion 为正（命令按包围盒 + 缓冲生成）", enclosure.cushion?.radius > 0, `cushionRadius=${enclosure.cushion?.radius} envelopeRadius=${enclosure.envelopeRadius}`);
  }

  // Named selections: the missile groups must cover every face of the missile exactly once. The
  // faces cannot be told apart by area alone, so the coverage is proven by summing the group areas
  // and comparing them with the body's own surface area.
  const hasTip = geometry?.expectedAreas?.faces?.some((face) => face.kind === "tip") ?? false;
  const selectionNames = [...(hasTip ? ["missile_nose_tip"] : []), "missile_nose", "missile_body", "missile_fin", "inlet", "wall", "outlet"];
  const missileGroups = ["missile_nose_tip", "missile_nose", "missile_body", "missile_fin"];
  const selectionReport = result?.namedSelections?.groups;
  if (selectionReport) {
    for (const name of selectionNames) {
      add(`命名选择 '${name}' 已创建`, (selectionReport[name]?.faces ?? 0) > 0, `faces=${selectionReport[name]?.faces} area=${selectionReport[name]?.areaM2}`);
    }
    const coveredFaces = missileGroups.reduce((total, name) => total + (selectionReport[name]?.faces ?? 0), 0);
    add("导弹所有面都在命名选择里（面数）", coveredFaces === result?.faceCount, `covered=${coveredFaces} faceCount=${result?.faceCount}`);
    const coveredArea = missileGroups.reduce((total, name) => total + (selectionReport[name]?.areaM2 ?? 0), 0);
    add("导弹所有面都在命名选择里（面积，无遗漏）", relativeDifference(coveredArea, result?.measurements?.surfaceAreaM2) <= 1e-9, `selectionArea=${coveredArea} surfaceArea=${result?.measurements?.surfaceAreaM2}`);
    // The split of the body family is structural: the tip cap, the cone, and cylinder + shoulder + base.
    const bodyKinds = geometry?.expectedAreas?.faces?.filter((face) => ["shoulder", "cylinder", "base"].includes(face.kind)).length ?? 0;
    add("弹体面划分与解析轮廓一致", (selectionReport.missile_nose_tip?.faces ?? 0) === (hasTip ? 1 : 0) && (selectionReport.missile_nose?.faces ?? 0) === 1 && (selectionReport.missile_body?.faces ?? 0) === bodyKinds, `tip=${selectionReport.missile_nose_tip?.faces ?? 0} nose=${selectionReport.missile_nose?.faces} body=${selectionReport.missile_body?.faces} expectedBody=${bodyKinds}`);
    const roundFaces = Number(result?.diagnostics?.roundCreatedFaces ?? 0);
    const expectedFinFaces = (geometry?.expectedFinFaceCount ?? 0) + (Number.isFinite(roundFaces) ? roundFaces : 0);
    // Healing merges the sliver faces into their neighbours, so the exact profile-curve count only
    // holds when the repair left the topology alone. When it did change, the fin family must still
    // carry at least one face per profile curve per instance: the sections survive, the slivers do not.
    const repairChangedFaces = Number(result?.diagnostics?.repair?.delta?.faces ?? 0) !== 0;
    const finFaceCount = selectionReport.missile_fin?.faces ?? 0;
    if (repairChangedFaces) {
      const profileCurves = Math.max(1, Math.round((geometry?.expectedFinFaceCount ?? 0) / ((geometry?.cruciform_count ?? 1) * 2)) - 1);
      const floor = profileCurves * (geometry?.cruciform_count ?? 1) * 2;
      add("舵面面数不少于每侧剖面曲线数（修复合并了小面）", finFaceCount >= floor, `fin=${finFaceCount} floor=${floor} expectedBeforeRepair=${expectedFinFaces} repairDelta=${result.diagnostics.repair.delta.faces}`);
    } else {
      add("舵面面数与翼型放样拓扑一致", finFaceCount === expectedFinFaces, `fin=${finFaceCount} expected=${expectedFinFaces} rounds=${roundFaces}`);
    }
    // Repair evidence: the inexact edges are cleared, no sliver face survives, and the healing did
    // not eat real geometry. A missing report is a failure, not a pass, because the model then
    // ships with the defects it was supposed to have repaired.
    const repair = result?.diagnostics?.repair ?? null;
    const limits = geometry?.repair ?? null;
    if (limits) {
      const inexact = repair?.inexact ?? {};
      const inexactOk = limits.inexactEdges
        ? inexact.success === true && inexact.problemAreasAfter === 0
        : true;
      add("非精确边已修复", inexactOk, `before=${inexact.problemAreasBefore} after=${inexact.problemAreasAfter} success=${inexact.success}${inexact.error ? ` error=${inexact.error}` : ""}`);
      const small = repair?.smallFaces ?? {};
      // The pass heals genuine slivers and must leave every designed round alone. What has to hold
      // is that nothing was damaged: no round lost, no deformation. A drop in the sliver count is
      // evidence of work done, not a requirement, because most sub-floor faces are the narrow but
      // legitimate fillet strips the guard now protects.
      const repaired = repair?.smallFacesAttempted === true;
      add("小面修复已尝试", repaired, `before=${small.facesBefore} after=${small.facesAfter} attempted=${small.fixed}${small.error ? ` error=${small.error}` : ""}`);
      // Every designed round must still be there. The forward junction is 1.5 mm, so each of its 48
      // strips is far under the small-face floor; a repair that eats them removes the fillet.
      const guards = Array.isArray(repair?.guardRadiiM) ? repair.guardRadiiM : [];
      const roundsBefore = Array.isArray(repair?.before?.roundFaces) ? repair.before.roundFaces : [];
      const roundsAfter = Array.isArray(repair?.after?.roundFaces) ? repair.after.roundFaces : [];
      const roundDetail = guards.map((radius, index) => `${(radius * 1000).toFixed(1)}mm:${roundsBefore[index] ?? "?"}→${roundsAfter[index] ?? "?"}`).join(" ");
      add("修复保留了全部设计圆角（根部与剖面）", repair?.roundsPreserved === true, roundDetail || "no guard radii");
      // The audit runs on the body that is saved, so it is the honest reading: everything between
      // the first repair and the save (named selections, component cleanup, suppression) can leave
      // new defects, and this is what catches them.
      const audit = result?.diagnostics?.finalGeometry ?? null;
      add("保存前非精确边已清零（最终几何）", audit !== null && Number(audit.problemAreasFinal ?? -1) === 0, `final=${audit?.problemAreasFinal} beforeAudit=${audit?.problemAreasBefore}`);
      add("保存前设计圆角仍完整（最终几何）", audit?.roundsPreserved === true, JSON.stringify({ before: audit?.roundFacesBefore, after: audit?.roundFacesAfter }));
      const delta = repair?.delta ?? {};
      // With `hex_flat_end` at its new default the forward seam holds the full 3 mm junction round
      // and no sliver forms at all, so the honest requirement is zero non-round faces below the
      // floor. Rounds are allowed to be that small: the 1.5 mm forward strips are 0.094 mm².
      const belowFloor = Number(small.facesAfter ?? -1);
      const roundsKept = guards.reduce((total, _radius, index) => total + (roundsAfter[index] ?? 0), 0);
      add("无舵根碎面（面积低于 1 mm² 且非圆角）", belowFloor === 0, `belowFloor=${belowFloor} roundFaces=${roundsKept} threshold=${limits.smallFaceAreaM2}`);
      add(`修复变形在允许范围内（体积 ≤ ${(limits.maxVolumeChange * 100).toFixed(2)}%）`, repair?.volumeWithinLimit === true, `volumeRel=${delta.volumeRel} limit=${limits.maxVolumeChange}`);
      add(`修复变形在允许范围内（表面积 ≤ ${(limits.maxSurfaceChange * 100).toFixed(2)}%）`, repair?.surfaceWithinLimit === true, `surfaceRel=${delta.surfaceRel} limit=${limits.maxSurfaceChange}`);
    }
    if (geometry?.rootJunctionFillet) {
      const rounds = result?.diagnostics?.edgeFillets ?? [];
      const band = geometry.rootJunctionFillet.tolerance ?? 0.1;
      const requestedRoot = geometry.rootJunctionFillet.radius;
      const holdsRadius = (round, requested) => round?.success === true
        && round.clamped !== true
        && round.radiusM > 0
        && Math.abs(round.radiusM - requested) / requested <= band;
      const stationCount = geometry.cruciform_count ?? 1;
      const forwardFloor = geometry.rootJunctionFillet.forwardMinRadius ?? requestedRoot;
      for (const station of geometry.rootJunctionFillet.stations ?? []) {
        const round = rounds.find((item) => item.label === "root-junction" && item.station === station.name && item.success);
        const chord = station.te - station.le;
        const mid = (station.le + station.te) / 2;
        const spansChord = Number.isFinite(round?.xMin) && Number.isFinite(round?.xMax)
          && round.xMin < mid && round.xMax > mid && (round.xMax - round.xMin) >= chord * 0.5;
        const enough = (round?.edges ?? 0) >= stationCount * 2;
        // The requested radius is what ConstantRound was asked for. One straight blend segment per
        // fin reads back as a cylinder of that radius, so the created surfaces are measured too: a
        // kernel success alone is not evidence that the blend came out at the requested size.
        const measuredRadii = Array.isArray(round?.measuredRadiiM) ? round.measuredRadiiM.map(Number).filter(Number.isFinite) : [];
        const usedRadius = Number(round?.radiusM);
        const measuredHits = Number.isFinite(usedRadius) && usedRadius > 0
          ? measuredRadii.filter((radius) => Math.abs(radius - usedRadius) <= usedRadius * band).length
          : 0;
        const measuredEnough = measuredHits >= stationCount;
        const detail = `radiusM=${round?.radiusM} requestedM=${round?.requestedM} clamped=${round?.clamped} edges=${round?.edges} x=${round?.xMin}..${round?.xMax} measuredCylinders=${measuredHits}/${measuredRadii.length}`;
        if (station.allowReducedRadius) {
          const present = round?.success === true && round.radiusM > 0;
          const aboveFloor = present && round.radiusM + 1e-12 >= forwardFloor;
          const withinRequest = present && round.radiusM <= requestedRoot * (1 + band) + 1e-12;
          const floorMm = (forwardFloor * 1000).toFixed(1);
          const requestMm = (requestedRoot * 1000).toFixed(0);
          add(`舵面 ${station.name} 与弹体交接圆角在 ${floorMm}–${requestMm} mm 之间`, aboveFloor && withinRequest && spansChord && enough && measuredEnough, detail);
        } else {
          add(`舵面 ${station.name} 与弹体交接圆角为 3 mm`, holdsRadius(round, requestedRoot) && spansChord && enough && measuredEnough, detail);
        }
      }
      const sectionBand = geometry.airfoil?.hex_fillet_tolerance ?? band;
      const measuredFillets = result?.diagnostics?.sectionFillets ?? [];
      for (const fin of geometry.fins ?? []) {
        for (const section of fin.sections ?? []) {
          if (!Number.isFinite(section.leadingFilletRadiusM)) continue;
          const measured = measuredFillets.find((item) => item.name === fin.name && Math.abs(Number(item.le) - section.le) <= 1e-6);
          const radiusOk = measured?.radiusM > 0 && Math.abs(measured.radiusM - section.leadingFilletRadiusM) / section.leadingFilletRadiusM <= sectionBand;
          // The read-back samples the arc ends and one mid point, so it can sit aft of the crown by
          // less than the radius. A shift larger than that radius fails here; the centre-of-mass
          // station check above is the 0.1 mm leading-edge gate.
          const stationOk = Number.isFinite(measured?.minXM) && Math.abs(measured.minXM - section.le) <= section.leadingFilletRadiusM;
          add(`舵面 ${fin.name} 前缘圆角 ${section.leadingFilletRadiusM} m 且前缘站位不变`, radiusOk && stationOk, `radiusM=${measured?.radiusM} minXM=${measured?.minXM} le=${section.le} clamped=${section.leadingFilletClamped}`);
        }
      }
      const measuredAngles = result?.diagnostics?.sectionAngles ?? [];
      const front = (geometry.fins ?? []).find((fin) => fin.name === "front");
      const rear = measuredAngles.find((item) => item.name === "rear");
      const frontAngle = measuredAngles.find((item) => item.name === "front");
      // Both fins must be radial and therefore coplanar at each station. A canted forward fin reads
      // as a scissor against the radial aft fin in any axial view, so the cant is gone and this gate
      // holds both stations to zero instead of to a bevel angle.
      const radialTolerance = 0.002;
      add("前舵保持径向（与后舵共面）", Number.isFinite(frontAngle?.angleRad) && Math.abs(frontAngle.angleRad) <= radialTolerance, `measuredRad=${frontAngle?.angleRad} expectedRad=0`);
      if (rear) add("后舵保持径向", Number.isFinite(rear.angleRad) && Math.abs(rear.angleRad) <= radialTolerance, `measuredRad=${rear.angleRad}`);
      if (Number.isFinite(front?.bodyIntersectionAngleRad)) {
        add("前舵倾斜角为 0（已取消斜置）", front.bodyIntersectionAngleRad === 0, `expectedRad=${front.bodyIntersectionAngleRad}`);
      }
    }
    // Both caps of the enclosure, one face each and the right size; the wall is the side surface.
    const capArea = Math.PI * request.regions.enclosure.radius ** 2;
    add("inlet / outlet 是外壳两端盖各一个面", selectionReport.inlet?.faces === 1 && selectionReport.outlet?.faces === 1 && relativeDifference(selectionReport.inlet?.areaM2, capArea) <= 1e-3 && relativeDifference(selectionReport.outlet?.areaM2, capArea) <= 1e-3, `inlet=${selectionReport.inlet?.areaM2} outlet=${selectionReport.outlet?.areaM2} expected=${capArea}`);
    const wallArea = 2 * Math.PI * request.regions.enclosure.radius * request.regions.enclosure.length;
    add("wall 是外壳侧面（圆柱面）", selectionReport.wall?.faces === 1 && relativeDifference(selectionReport.wall?.areaM2, wallArea) <= 1e-3, `wall=${selectionReport.wall?.areaM2} expected=${wallArea}`);
  } else if (request.regions?.enclosure) {
    add("命名选择已创建", false, "result.json 里没有 namedSelections");
  }

  // Physics: exactly the missile, the BOI and the enclosure survive; everything else the structure
  // tree carries is suppressed. SpaceClaim has no getter for that state, so what is checked is that
  // the keep set came out exactly right and that the call succeeded for every other object.
  const physics = result?.physics;
  if (physics) {
    // Four objects: the missile body, the BOI, the enclosure body (which lives inside the component,
    // so its name does not read back) and the component carrying it. The names are matched by the
    // script rather than echoed, because the enclosure's own body name is read-only.
    const named = result?.diagnostics?.namedObjects ?? {};
    add(`物理抑制：保留的对象只有 ${keepNames.join(" / ")}`, (physics.kept ?? []).length === 4 && keepNames.every((name) => named[name] === true), `kept=${(physics.kept ?? []).map((item) => item.name).join("/")} namedObjects=${JSON.stringify(named)}`);
    add("物理抑制：结构里其余对象全部抑制成功", (physics.failed ?? []).length === 0, `suppressed=${(physics.suppressed ?? []).length} failed=${JSON.stringify(physics.failed ?? [])}`);
  } else if (request.regions?.enclosure) {
    add("物理抑制已执行", false, "result.json 里没有 physics");
  }

  // The empty components the circular pattern leaves behind are deleted, not suppressed. They may
  // only go if they really hold no body, and the body list has to survive the deletion untouched.
  const removed = result?.diagnostics?.deletedComponents;
  if (removed) {
    add("阵列残留的空组件已删除（删前确认里面没有 body）", removed.every((item) => item.bodies === 0 && !item.bodiesError && !item.deleteError), `removed=${removed.length} detail=${JSON.stringify(removed)}`);
    const components = result?.diagnostics?.componentNames ?? [];
    add(`删除后结构里只剩 ${enclosureName} 组件`, components.length === 1 && result?.diagnostics?.namedObjects?.[enclosureName] === true, `components=${components.join("/")}`);
  } else if (request.regions?.enclosure) {
    add("空组件已清理", false, "result.json 里没有 deletedComponents");
  }

  return { ok: checks.every((check) => check.ok), checks };
}

export async function runSpaceClaim({ scriptPath, paramsPath, executablePath, timeoutMs = DEFAULT_TIMEOUT_MS, env = process.env, diagnosticPath = "" }) {
  const childEnv = { ...env, SCDM_MISSILE_PARAMS: paramsPath };
  if (diagnosticPath) childEnv.SCDM_MISSILE_DIAGNOSTIC = diagnosticPath;
  const args = [`/RunScript=${scriptPath}`, `/ScriptArgs=${paramsPath}`, "/Headless=True", "/ExitAfterScript=True"];
  const started = Date.now();
  const outcome = await new Promise((resolve) => {
    const child = spawn(executablePath, args, { cwd: path.dirname(executablePath), windowsHide: true, env: childEnv, stdio: "ignore" });
    let settled = false;
    const finish = (value) => { if (settled) return; settled = true; clearTimeout(timer); resolve(value); };
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      if (process.platform === "win32") {
        const terminator = spawn("taskkill", ["/PID", String(child.pid), "/T", "/F"], { windowsHide: true, stdio: "ignore" });
        terminator.once("error", () => { child.kill(); finish({ timedOut: true, code: null }); });
        terminator.once("close", () => finish({ timedOut: true, code: null }));
      } else { child.kill(); finish({ timedOut: true, code: null }); }
    }, timeoutMs);
    child.once("error", (error) => finish({ error: error.message, code: null }));
    child.once("close", (code) => finish({ code, timedOut }));
  });
  return { ...outcome, elapsedMs: Date.now() - started, args };
}

async function readJson(file) {
  try { return JSON.parse(await readFile(file, "utf8")); } catch { return null; }
}

/**
 * A successful model directory keeps the parameter JSON, the script, the model file and the text
 * records that explain how the model was produced: the manifest and the script's result JSON.
 * A script exception also leaves `diagnostic.txt`, so that name stays. Previews, the request
 * snapshot and the runner's own `error.txt` are execution intermediates and are removed.
 * The manifest is written after this pruning, so it only lists files that really remain.
 */
export async function retainModelOutputs(dir, modelFileName) {
  const keep = new Set([RUN_ARTIFACT_NAMES.params, RUN_ARTIFACT_NAMES.script, RUN_ARTIFACT_NAMES.result, RUN_ARTIFACT_NAMES.diagnostic, RUN_ARTIFACT_NAMES.manifest, modelFileName]);
  const entries = await readdir(dir, { withFileTypes: true });
  await Promise.all(entries.filter((entry) => entry.isFile() && !keep.has(entry.name)).map((entry) => unlink(path.join(dir, entry.name))));
}

/**
 * Full build: validate, write the request, optionally execute SpaceClaim, then grade the artifacts.
 */
async function buildMissileCore(options = {}) {
  const {
    explicit = {},
    preset = {},
    outputDir = "",
    fileName = DEFAULT_MODEL_FILENAME,
    timeoutMs = DEFAULT_TIMEOUT_MS,
    env = process.env,
    execute = DEFAULT_EXECUTE,
  } = options;

  const runDir = options.managedRunDir || runDirectory(outputDir || defaultRunRoot(env));
  const modelPath = path.join(runDir, fileName);
  const paramsPath = path.join(runDir, RUN_ARTIFACT_NAMES.params);
  const scriptPath = path.join(runDir, RUN_ARTIFACT_NAMES.script);
  const resultPath = path.join(runDir, RUN_ARTIFACT_NAMES.result);
  const diagnosticPath = path.join(runDir, RUN_ARTIFACT_NAMES.diagnostic);
  const imagePath = path.join(runDir, RUN_ARTIFACT_NAMES.image);
  const assemblyImagePath = path.join(runDir, RUN_ARTIFACT_NAMES.assemblyImage);
  const axialImagePath = path.join(runDir, RUN_ARTIFACT_NAMES.axialImage);
  const REGION_LABELS = { enclosure: "外壳", boi: "加密区 BOI" };

  const request = buildModelRequest({ explicit, preset, outputDir: runDir, modelFile: modelPath });
  if (!request.ok) {
    return { ok: false, status: "invalid-parameters", runDir, request, errors: request.errors, warnings: request.warnings, corrections: request.corrections };
  }

  // Generate-only mode must work on a machine without SpaceClaim, so a missing installation is
  // only fatal when the caller actually asked for execution.
  let executablePath = "";
  let apiVersion = "";
  let executableError = "";
  try {
    executablePath = await resolveSpaceClaimExecutable(env);
    apiVersion = await apiVersionForExecutable(executablePath, env);
  } catch (error) {
    if (execute) throw error;
    executableError = error.message;
  }

  await mkdir(runDir, { recursive: true });
  // The region body names ride on the region objects themselves (they come from `enclosure_name` /
  // `boi_name`), so the request document, the script and the gate all agree on one source.
  const regionRequests = ["enclosure", "boi"].map((name) => request.regions[name]);
  const keepNames = [MISSILE_BODY_NAME, request.regions.boi?.bodyName ?? "BOI", request.regions.enclosure?.bodyName ?? "Enclosure"];
  const document = buildRequestDocument(request, { model: modelPath, result: resultPath, diagnostic: diagnosticPath, image: imagePath, assemblyImage: assemblyImagePath, axialImage: axialImagePath }, regionRequests, { groups: NAMED_SELECTION_GROUPS, enclosure: ENCLOSURE_SELECTIONS, keep: keepNames, wall: ENCLOSURE_SELECTIONS.wall });
  document.apiVersion = apiVersion;
  await writeFile(paramsPath, `${JSON.stringify(document, null, 2)}\n`, "utf8");
  await writeFile(scriptPath, renderModelScript(apiVersion), "utf8");

  if (!execute) {
    return { ok: true, status: "script-generated", runDir, paramsPath, scriptPath, modelPath, imagePath, apiVersion, executablePath, executableError, request };
  }

  const startedAt = Date.now();
  const outcome = await runSpaceClaim({ scriptPath, paramsPath, executablePath, timeoutMs, env, diagnosticPath });
  const result = await readJson(resultPath);
  const modelStat = await stat(modelPath).catch(() => null);
  const resultStat = await stat(resultPath).catch(() => null);
  const imageStat = await stat(imagePath).catch(() => null);
  const assemblyImageStat = await stat(assemblyImagePath).catch(() => null);
  const acceptance = evaluateAcceptance({ request, result, modelPath, modelStat, resultStat, startedAt, regionLabels: REGION_LABELS });
  const diagnostic = await readFile(diagnosticPath, "utf8").catch(() => "");
  if (acceptance.ok && document.geometry?.rootJunctionFillet) {
    const applied = (result?.diagnostics?.edgeFillets ?? []).filter((item) => item.label === "root-junction" && item.success);
    if (applied.length) {
      document.geometry.rootJunctionFillet.applied = applied.map((item) => ({
        station: item.station,
        radiusM: item.radiusM,
        radiusMaxM: item.radiusMaxM ?? item.radiusM,
        requestedM: item.requestedM,
        clamped: item.clamped === true,
        edges: item.edges,
      }));
      await writeFile(paramsPath, `${JSON.stringify(document, null, 2)}\n`, "utf8");
    }
  }

  return {
    ok: acceptance.ok && !outcome.timedOut && !outcome.error && outcome.code === 0,
    status: acceptance.ok && !outcome.timedOut && !outcome.error && outcome.code === 0 ? "PASS" : "FAIL",
    runDir,
    paramsPath,
    scriptPath,
    modelPath,
    resultPath,
    diagnosticPath,
    imagePath: imageStat ? imagePath : "",
    assemblyImagePath: assemblyImageStat ? assemblyImagePath : "",
    apiVersion,
    executablePath,
    outcome,
    result,
    acceptance,
    diagnostic: diagnostic.trim(),
    request,
  };
}

// Keep the modeling contract intact while collecting failures from every phase.
export async function buildMissile(options = {}) {
  const projectDir = fileURLToPath(new URL("..", import.meta.url));
  const env = options.env || process.env;
  const run = await beginRun("spaceclaim", projectDir, {
    outputDir: options.outputDir || env.SCDM_MISSILE_OUTPUT || "",
    parameters: { explicit: options.explicit || {}, preset: options.preset || {}, execute: options.execute ?? DEFAULT_EXECUTE, fileName: options.fileName || DEFAULT_MODEL_FILENAME },
    fixture: "recorded", env,
  });
  try {
    const fileName = options.fileName || DEFAULT_MODEL_FILENAME;
    if (path.basename(fileName) !== fileName || /[\\/]/.test(fileName) || fileName === "." || fileName === "..") throw new Error("Model filename must be a leaf filename; use outputDir for its directory.");
    const result = await buildMissileCore({ ...options, managedRunDir: run.dir });
    const status = result.ok ? result.status === "script-generated" ? "GENERATED" : "PASSED" : "FAILED";
    const from = run.dir;
    // Prune before the manifest is finalised so run.json lists only the files that really remain.
    if (status === "PASSED") await retainModelOutputs(run.dir, fileName);
    await finishRun(run, { status, verification: { runtimeVerified: status === "PASSED", checks: result.acceptance?.checks || [] }, error: status === "FAILED" ? { type: result.status, message: result.errors?.join("; ") || "Fresh artifact acceptance failed" } : null });
    return { ...relocateResult(result, from, run.dir), runId: run.record.runId, runManifest: path.join(run.dir, RUN_ARTIFACT_NAMES.manifest) };
  } catch (error) {
    await writeFile(path.join(run.dir,RUN_ARTIFACT_NAMES.error), `${error.name}: ${error.message}\n`, "utf8");
    await finishRun(run, { status: "FAILED", error });
    error.feedbackDir = run.dir;
    throw error;
  }
}
