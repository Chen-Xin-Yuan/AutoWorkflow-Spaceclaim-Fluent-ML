import assert from "node:assert/strict";
import test from "node:test";
import { mkdir, mkdtemp, readdir, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { buildModelRequest } from "../lib/missile-params.mjs";
import { apiVersionForExecutable, buildMissile, defaultRunRoot, evaluateAcceptance, resolveSpaceClaimExecutable, retainModelOutputs } from "../lib/spaceclaim-runner.mjs";

const regionResult = (request) => Object.fromEntries(["enclosure", "boi"].map((name) => [name, {
  faces: 3,
  extentsM: { ...request.regions[name].expectedExtents },
  volumeM3: request.regions[name].expectedVolume,
  centerOfMassM: { x: (request.regions[name].xFront + request.regions[name].xBack) / 2, y: 0, z: 0 },
  componentCount: request.regions[name].mode === "enclosure-command" ? 1 : 0,
}]));

const finPositions = (request) => request.geometry.fins.map((fin) => ({
  name: fin.name,
  index: 0,
  centerOfMassM: { x: fin.centroid[0], y: fin.centroid[1], z: 0 },
}));

const selectionReport = (request) => {
  const kind = (name) => request.geometry.expectedAreas.faces.find((face) => face.kind === name)?.area ?? 0;
  const groups = {
    missile_nose_tip: { faces: request.geometry.expectedAreas.faces.some((face) => face.kind === "tip") ? 1 : 0, areaM2: kind("tip") },
    missile_nose: { faces: 1, areaM2: kind("cone") },
    missile_body: { faces: ["shoulder", "cylinder", "base"].filter((name) => request.geometry.expectedAreas.faces.some((face) => face.kind === name)).length, areaM2: kind("shoulder") + kind("cylinder") + kind("base") },
    missile_fin: { faces: request.geometry.expectedFinFaceCount, areaM2: 0.4 },
    inlet: { faces: 1, areaM2: Math.PI * request.regions.enclosure.radius ** 2 },
    wall: { faces: 1, areaM2: 2 * Math.PI * request.regions.enclosure.radius * request.regions.enclosure.length },
    outlet: { faces: 1, areaM2: Math.PI * request.regions.enclosure.radius ** 2 },
  };
  return { groups, names: Object.keys(groups), expected: {} };
};

const physicsReport = () => ({
  kept: [
    { type: "DesignBody", name: "Missile", collection: "Bodies" },
    { type: "DesignBody", name: "BOI", collection: "Bodies" },
    { type: "DesignBody", name: "<unreadable>", collection: "Bodies" },
    { type: "Component", name: "Enclosure", collection: "Components" },
  ],
  suppressed: [
    { type: "Component", name: "", collection: "Components" },
    { type: "Component", name: "", collection: "Components" },
    { type: "Component", name: "", collection: "Components" },
    { type: "Component", name: "", collection: "Components" },
    { type: "CoordinateSystem", name: "", collection: "CoordinateSystems" },
  ],
  failed: [],
});

const passingResult = (request) => {
  const selections = selectionReport(request);
  const missileGroups = ["missile_nose_tip", "missile_nose", "missile_body", "missile_fin"];
  return {
    status: "PASS",
    bodyCount: 3,
    modelBodyCount: 1,
    regionBodyCount: 2,
    faceCount: missileGroups.reduce((total, name) => total + selections.groups[name].faces, 0),
    measurements: {
      extentsM: { ...request.geometry.expectedExtents },
      surfaceAreaM2: missileGroups.reduce((total, name) => total + selections.groups[name].areaM2, 0),
    },
    regions: regionResult(request),
    finPositions: finPositions(request),
    namedSelections: selections,
    physics: physicsReport(),
    diagnostics: {
      revolveFaces: request.geometry.expectedAreas.faces.map((face) => ({ area: face.area })),
      bodyVolumeAfterRevolve: request.geometry.expectedVolume,
      bodyCenterOfMassAfterRevolve: { x: request.geometry.bodyCenterOfMass, y: 0, z: 0 },
      bodyNames: ["Missile", "<unreadable>", "BOI"],
      componentNames: ["Enclosure"],
      namedObjects: { Missile: true, BOI: true, Enclosure: true },
      deletedComponents: [
        { type: "Component", name: "", bodies: 0 },
        { type: "Component", name: "", bodies: 0 },
      ],
    },
  };
};

const freshStat = () => ({ mtimeMs: Date.now() });

function grade(request, result, options = {}) {
  const stat = options.stat === undefined ? freshStat() : options.stat;
  return evaluateAcceptance({
    request,
    result,
    modelPath: "model.scdoc",
    modelStat: options.modelStat === undefined ? stat : options.modelStat,
    resultStat: stat,
    startedAt: Date.now() - 1000,
    regionLabels: { enclosure: "外壳", boi: "加密区 BOI" },
  });
}

test("the acceptance gate passes a correct run", () => {
  const request = buildModelRequest({ explicit: { fin_section: "naca" } });
  const graded = grade(request, passingResult(request));
  assert.equal(graded.ok, true, JSON.stringify(graded.checks.filter((check) => !check.ok)));
  const names = graded.checks.map((check) => check.name);
  for (const expected of ["status=PASS", "合并后实体数为 1", "旋转体面数与剖面段数一致", "每个剖面段对应的面面积 ≈ 解析值", ".scdoc 由本次运行新生成"]) {
    assert.ok(names.includes(expected), `missing check: ${expected}`);
  }
  assert.ok(names.some((name) => name.includes("弹尖倒圆面面积")), "a blunt nose must be proven by its cap area");
  assert.ok(names.some((name) => name.includes("交接处倒圆面面积")), "a shoulder fillet must be proven by its area");
  for (const region of ["外壳", "加密区 BOI"]) {
    assert.ok(names.some((name) => name.startsWith(`${region} 尺寸`)), `${region} extents must be checked`);
    assert.ok(names.some((name) => name.startsWith(`${region} 体积`)), `${region} volume must be checked`);
    assert.ok(names.some((name) => name.startsWith(`${region} 轴向中位`)), `${region} must be checked for which end it was shortened at`);
  }
  assert.ok(names.includes("单一 .scdoc 内实体数正确（导弹 + 各区域）"), "the document body count must be checked");
  // Every face of the missile must land in a named selection, and the gate has to prove it.
  for (const selection of ["missile_nose_tip", "missile_nose", "missile_body", "missile_fin", "inlet", "wall", "outlet"]) {
    assert.ok(names.includes(`命名选择 '${selection}' 已创建`), `the '${selection}' named selection must be checked`);
  }
  assert.ok(names.includes("导弹所有面都在命名选择里（面数）"), "the selection face count must be checked");
  assert.ok(names.includes("导弹所有面都在命名选择里（面积，无遗漏）"), "the selection coverage must be checked by area");
  assert.ok(names.includes("物理抑制：保留的对象只有 Missile / BOI / Enclosure"), "the physics keep set must be checked");
  assert.ok(names.includes("物理抑制：结构里其余对象全部抑制成功"), "the suppression of everything else must be checked");
  assert.ok(names.includes("阵列残留的空组件已删除（删前确认里面没有 body）"), "the empty component cleanup must be checked");
  assert.ok(names.includes("删除后结构里只剩 Enclosure 组件"), "the remaining components must be checked");
  // The fins sliding along a constant cylinder changes nothing else this gate measures.
  for (const fin of ["front", "rear"]) {
    assert.ok(names.some((name) => name.startsWith(`舵面 ${fin} 可见前缘距弹尖`)), `the ${fin} leading-edge station must be checked`);
  }
  assert.ok(names.some((name) => name.startsWith("旋转体轴向质心")), "the nose tip datum must be checked");
  // The enclosure must come from the official command, not from a hand-built revolve.
  assert.ok(names.some((name) => name.includes("准备 > 外壳")), "the Enclosure command must be proven");
  assert.ok(names.includes("外壳组件名为 Enclosure"), "the enclosure component name must be checked");
  // The missile and the BOI have to end up centred and then nudged by model_offset, and that is
  // graded as a property of the pair rather than of either region alone.
  assert.ok(names.some((name) => name.startsWith("定位规则：ensemble 居中站位")), "the placement rule must be restated and checked");
  assert.ok(names.some((name) => name.startsWith("实测：BOI 相对外壳的轴向中位差")), "the placement must be measured on the built bodies");
  assert.equal(graded.checks.every((check) => typeof check.name === "string" && check.name.length > 0), true);
});

test("the gate rejects an enclosure that was not produced by the Enclosure command", () => {
  const request = buildModelRequest({ explicit: { fin_section: "naca" } });
  const byRevolve = passingResult(request);
  byRevolve.regions.enclosure.componentCount = 0;
  assert.equal(grade(request, byRevolve).ok, false);

  // The name is checked by comparison as well as by read-back.
  const unnamed = passingResult(request);
  unnamed.diagnostics.namedObjects = { Missile: true, BOI: true, Enclosure: false };
  assert.equal(grade(request, unnamed).ok, false);

  // An ASCII name that the structure tree does not actually carry is a failure even though the
  // comparison said yes: it means the rename never reached the object that was compared.
  const misnamed = passingResult(request);
  misnamed.diagnostics.componentNames = ["Component1"];
  assert.equal(grade(request, misnamed).ok, false);
});

test("a non-ASCII enclosure name is graded by comparison, not by read-back", () => {
  // The host cannot decode a non-ASCII name back into a Python string: it raises UnicodeDecodeError,
  // which is why every read-back degrades to <unreadable>. The name is still settable, and the
  // String.Equals comparison in the script is what proves it landed, so an unreadable component list
  // must not fail the run. This is the path the parameter keeps open.
  const request = buildModelRequest({ explicit: { fin_section: "naca", enclosure_name: "外壳", boi_name: "加密区 BOI" } });
  assert.equal(request.ok, true, request.errors.join(" | "));
  const result = passingResult(request);
  result.diagnostics.componentNames = ["<unreadable>"];
  result.diagnostics.namedObjects = { Missile: true, BOI: true, "外壳": true, "加密区 BOI": true };
  const graded = grade(request, result);
  assert.equal(graded.ok, true, JSON.stringify(graded.checks.filter((check) => !check.ok)));
  assert.ok(graded.checks.map((check) => check.name).includes("外壳组件名为 外壳"));

  // ...but a name that does not compare equal is still caught.
  const mismatched = passingResult(request);
  mismatched.diagnostics.namedObjects = { Missile: true, BOI: true, "外壳": false, "加密区 BOI": true };
  assert.equal(grade(request, mismatched).ok, false);
});

test("the gate rejects a region that is missing, wrongly sized, or has the wrong body count", () => {
  const request = buildModelRequest({ explicit: { fin_section: "naca" } });
  const missing = passingResult(request);
  delete missing.regions.boi;
  assert.equal(grade(request, missing).ok, false);

  const wrongSize = passingResult(request);
  wrongSize.regions.enclosure.extentsM.axial = 20;
  assert.equal(grade(request, wrongSize).ok, false);

  const wrongVolume = passingResult(request);
  wrongVolume.regions.boi.volumeM3 *= 1.5;
  assert.equal(grade(request, wrongVolume).ok, false);

  // Regions merged into the model instead of staying separate bodies is a real failure mode.
  const merged = passingResult(request);
  merged.bodyCount = 1;
  assert.equal(grade(request, merged).ok, false);
});

test("the gate rejects a model that drifted off its placement station", () => {
  const request = buildModelRequest({ explicit: { fin_section: "naca" } });
  // Five millimetres of drift: far below the 2e-3 relative tolerance the per-region checks use
  // (16 mm on this midpoint), so only the dedicated placement check can see it.
  const slid = passingResult(request);
  slid.regions.boi.centerOfMassM.x += 5e-3;
  const graded = grade(request, slid);
  assert.equal(graded.ok, false);
  const failed = graded.checks.filter((check) => !check.ok).map((check) => check.name);
  assert.ok(failed.some((name) => name.startsWith("实测：BOI 相对外壳的轴向中位差")), `the placement check must be what fails, got ${failed.join(" | ")}`);
});

test("the gate rejects fins that do not sit at the station the parameters ask for", () => {
  const request = buildModelRequest({ explicit: { fin_section: "naca" } });
  // A fin slid along a constant cylinder changes nothing the size checks can see: only the mass
  // centre catches it, so the gate must reject it.
  const slid = passingResult(request);
  for (const entry of slid.finPositions) entry.centerOfMassM.x += 0.033;
  assert.equal(grade(request, slid).ok, false);

  const missing = passingResult(request);
  missing.finPositions = [];
  assert.equal(grade(request, missing).ok, false);

  // The real defect this guard exists for: burying the fin root along the swept leading edge instead
  // of radially drifts the visible leading edge aft by 3.6 mm / 4.2 mm, which is only a 1.2 mm move
  // of the centroid - well inside a loose relative tolerance.
  const buriedSweep = passingResult(request);
  const offsets = request.geometry.fins.map((fin) => (0.005 / (fin.tip_radius - fin.root_radius)) * (fin.tip_le - fin.le_x));
  buriedSweep.finPositions.forEach((entry, index) => { entry.centerOfMassM.x += offsets[index]; });
  assert.equal(grade(request, buriedSweep).ok, false);

  // A body shifted along the axis has the same length, so only its centre of mass shows it.
  const shiftedBody = passingResult(request);
  shiftedBody.diagnostics.bodyCenterOfMassAfterRevolve.x += 0.033;
  assert.equal(grade(request, shiftedBody).ok, false);
});

test("the gate rejects named selections that drop or double-count a face", () => {
  const request = buildModelRequest({ explicit: { fin_section: "naca" } });

  // Every missile group present but one face left out: the area sum no longer matches the body.
  const dropped = passingResult(request);
  dropped.namedSelections.groups.missile_fin.faces -= 1;
  dropped.faceCount -= 1;
  assert.equal(grade(request, dropped).ok, false);

  // The right face count, but a face counted in the wrong group: only the area sum sees it.
  const wrongGroup = passingResult(request);
  wrongGroup.namedSelections.groups.missile_body.areaM2 -= 0.01;
  assert.equal(grade(request, wrongGroup).ok, false);

  const missingName = passingResult(request);
  delete missingName.namedSelections.groups.inlet;
  assert.equal(grade(request, missingName).ok, false);

  const noSelections = passingResult(request);
  noSelections.namedSelections = null;
  assert.equal(grade(request, noSelections).ok, false);
});

test("the gate rejects a physics keep set that is wrong or a failed suppression", () => {
  const request = buildModelRequest({ explicit: { fin_section: "naca" } });

  // The BOI must not be suppressed: it is one of the three objects that matter.
  const droppedBoi = passingResult(request);
  droppedBoi.physics.kept = droppedBoi.physics.kept.filter((item) => item.name !== "BOI");
  droppedBoi.physics.suppressed.push({ type: "DesignBody", name: "BOI", collection: "Bodies" });
  assert.equal(grade(request, droppedBoi).ok, false);

  // One object the API refused to suppress is a real failure, not a detail.
  const failed = passingResult(request);
  failed.physics.failed.push({ type: "Component", name: "", collection: "Components", error: "nope" });
  assert.equal(grade(request, failed).ok, false);

  const noPhysics = passingResult(request);
  noPhysics.physics = null;
  assert.equal(grade(request, noPhysics).ok, false);
});

test("the gate refuses to delete a component that still holds a body", () => {
  const request = buildModelRequest({ explicit: { fin_section: "naca" } });

  // A non-empty component deleted by mistake would take geometry with it.
  const populated = passingResult(request);
  populated.diagnostics.deletedComponents[0].bodies = 2;
  assert.equal(grade(request, populated).ok, false);

  const failed = passingResult(request);
  failed.diagnostics.deletedComponents[1].deleteError = "nope";
  assert.equal(grade(request, failed).ok, false);

  // Something the cleanup could not inspect is not the same as something that is empty.
  const unreadable = passingResult(request);
  unreadable.diagnostics.deletedComponents[0].bodiesError = "nope";
  assert.equal(grade(request, unreadable).ok, false);

  // A component other than the enclosure means the cleanup missed one.
  const leftover = passingResult(request);
  leftover.diagnostics.componentNames = ["Enclosure", "Component1"];
  assert.equal(grade(request, leftover).ok, false);
});

test("the gate rejects a wrong body count, status, or a missing artifact", () => {
  const request = buildModelRequest({ explicit: { fin_section: "naca" } });
  const wrongBody = passingResult(request);
  wrongBody.bodyCount = 2;
  assert.equal(grade(request, wrongBody).ok, false);

  const failed = passingResult(request);
  failed.status = "FAIL";
  assert.equal(grade(request, failed).ok, false);

  const noModel = passingResult(request);
  assert.equal(grade(request, noModel, { modelStat: null }).ok, false);
  assert.equal(grade(request, null).ok, false);
});

test("the gate rejects dimensions that drifted from the parameters", () => {
  const request = buildModelRequest({ explicit: { fin_section: "naca" } });
  const shortAxial = passingResult(request);
  shortAxial.measurements.extentsM.axial = 3.6;
  assert.equal(grade(request, shortAxial).ok, false);

  const wrongSpan = passingResult(request);
  wrongSpan.measurements.extentsM.lateral = 0.63;
  wrongSpan.measurements.extentsM.vertical = 0.63;
  assert.equal(grade(request, wrongSpan).ok, false);
});

test("the extent expectation follows the cruciform phase and count", () => {
  // A rotated or higher-count cruciform has a smaller bounding box than D + 2·max(B); the gate must
  // expect the real box, otherwise a correct model would be rejected.
  const rotated = buildModelRequest({ explicit: { fin_section: "naca", fin_phase: "x" } });
  assert.ok(Math.abs(rotated.geometry.expectedExtents.lateral - 0.4544401819384363) < 1e-9);
  assert.equal(grade(rotated, passingResult(rotated)).ok, true);
  const naive = passingResult(rotated);
  naive.measurements.extentsM.lateral = rotated.geometry.tip_to_tip;
  naive.measurements.extentsM.vertical = rotated.geometry.tip_to_tip;
  assert.equal(grade(rotated, naive).ok, false);

  const six = buildModelRequest({ explicit: { fin_section: "naca", cruciform_count: 6 } });
  assert.ok(Math.abs(six.geometry.expectedExtents.lateral - 0.64) < 1e-9);
  assert.ok(Math.abs(six.geometry.expectedExtents.vertical - 0.5555939927143574) < 1e-9);
  assert.equal(grade(six, passingResult(six)).ok, true);
});

test("the gate rejects a missing or wrong-sized rounded face", () => {
  const request = buildModelRequest({ explicit: { fin_section: "naca" } });
  const noTip = passingResult(request);
  noTip.diagnostics.revolveFaces = noTip.diagnostics.revolveFaces.slice(1);
  const graded = grade(request, noTip);
  assert.equal(graded.ok, false);
  const tipCheck = graded.checks.find((check) => check.name.includes("弹尖倒圆面面积"));
  assert.equal(tipCheck.ok, false);

  const wrongRadius = passingResult(request);
  wrongRadius.diagnostics.revolveFaces[0] = { area: request.geometry.expectedAreas.faces[0].area * 2 };
  assert.equal(grade(request, wrongRadius).ok, false);

  const wrongCount = passingResult(request);
  wrongCount.diagnostics.revolveFaces = wrongCount.diagnostics.revolveFaces.slice(1);
  assert.match(grade(request, wrongCount).checks.find((check) => check.name.includes("面数与剖面段数")).detail, /faces=4 expected=5/);
});

test("the gate rejects a stale result.json", () => {
  const request = buildModelRequest({ explicit: { fin_section: "naca" } });
  const staleDate = Date.now() - 600000;
  const graded = grade(request, passingResult(request), { stat: { mtimeMs: staleDate } });
  assert.equal(graded.ok, false);
});

test("a sharp nose is graded against the untrimmed cone", () => {
  const request = buildModelRequest({ explicit: { fin_section: "naca", k_nose: 0, fillet_location: "cone_cylinder" } });
  assert.equal(request.geometry.body.fillet.enabled, false);
  assert.equal(request.geometry.body.tip.enabled, false);
  assert.deepEqual(request.geometry.expectedAreas.faces.map((face) => face.kind), ["cone", "cylinder", "base"]);
  const graded = grade(request, passingResult(request));
  assert.equal(graded.ok, true, JSON.stringify(graded.checks.filter((check) => !check.ok)));
  assert.equal(graded.checks.some((check) => check.name.includes("倒圆面面积")), false);
});

test("the nose treatment follows fillet_location", () => {
  const tipOnly = buildModelRequest({ explicit: { fin_section: "naca", fillet_location: "nose_tip" } });
  assert.equal(tipOnly.geometry.body.tip.enabled, true);
  assert.equal(tipOnly.geometry.body.fillet.enabled, false);
  assert.deepEqual(tipOnly.geometry.expectedAreas.faces.map((face) => face.kind), ["tip", "cone", "cylinder", "base"]);
  assert.equal(grade(tipOnly, passingResult(tipOnly)).ok, true);

  const shoulderOnly = buildModelRequest({ explicit: { fin_section: "naca", fillet_location: "cone_cylinder" } });
  assert.equal(shoulderOnly.geometry.body.tip.enabled, false);
  assert.equal(shoulderOnly.geometry.body.fillet.enabled, true);
  assert.deepEqual(shoulderOnly.geometry.expectedAreas.faces.map((face) => face.kind), ["cone", "shoulder", "cylinder", "base"]);

  const both = buildModelRequest({ explicit: { fin_section: "naca" } });
  assert.deepEqual(both.geometry.expectedAreas.faces.map((face) => face.kind), ["tip", "cone", "shoulder", "cylinder", "base"]);
});

test("the acceptance gate never trusts the exit code", () => {
  const request = buildModelRequest({ explicit: { fin_section: "naca" } });
  const graded = grade(request, null);
  assert.equal(graded.ok, false);
  assert.equal(graded.checks.some((check) => /exit|code/i.test(check.name)), false);
});

test("SpaceClaim is located from AWP_ROOT<version>, not from a hard-coded path", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "scdm-runner-awp-"));
  const scdm = path.join(root, "v241", "scdm");
  await mkdir(scdm, { recursive: true });
  const executable = path.join(scdm, "SpaceClaim.exe");
  await writeFile(executable, "");
  const env = { AWP_ROOT241: path.join(root, "v241"), LOCALAPPDATA: path.join(root, "cfg") };
  assert.equal(await resolveSpaceClaimExecutable(env), executable);
  await assert.rejects(() => resolveSpaceClaimExecutable({ LOCALAPPDATA: path.join(root, "cfg") }), /SpaceClaim\.exe not found/);
});

test("the API version comes from the product layout, not from the highest API folder", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "scdm-runner-version-"));
  const scdm = path.join(root, "v241", "scdm");
  await mkdir(path.join(scdm, "SpaceClaim.Api.V242"), { recursive: true });
  const executable = path.join(scdm, "SpaceClaim.exe");
  await writeFile(executable, "");
  // An install can ship API folders for several versions; the running product is v241.
  assert.equal(await apiVersionForExecutable(executable, { LOCALAPPDATA: path.join(root, "cfg") }), "V241");
});

test("generate-only mode writes the request without needing an installation", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "scdm-runner-gen-"));
  const out = await buildMissile({ execute: false, outputDir: path.join(root, "runs"), env: { LOCALAPPDATA: path.join(root, "cfg"), TEMP: root } });
  assert.equal(out.ok, true);
  assert.equal(out.status, "script-generated");
  assert.match(out.executableError, /SpaceClaim\.exe not found/);
  const document = JSON.parse(await readFile(out.paramsPath, "utf8"));
  assert.equal(document.params.k_nose, 0.005);
  assert.equal(document.sources.thickness_percent, "default");
  assert.equal(document.params.le_radius_index, 6);
  assert.equal(document.params.max_thickness_tenths, 3);
  assert.ok(document.geometry.expectedAreas.faces.some((face) => face.kind === "tip"));
  assert.ok(document.geometry.expectedAreas.faces.some((face) => face.kind === "shoulder"));
  assert.equal(document.output.model, out.modelPath);
  const script = await readFile(out.scriptPath, "utf8");
  assert.equal(script.includes("SCDM_MISSILE_PARAMS"), true);
});

test("generate-only evidence is never labelled runtime-verified", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "scdm-runner-generated-"));
  const out = await buildMissile({ execute: false, outputDir: path.join(root, "runs"), env: { LOCALAPPDATA: path.join(root, "cfg"), TEMP: root } });
  const manifest = JSON.parse(await readFile(out.runManifest, "utf8"));
  assert.equal(manifest.status, "GENERATED");
  assert.equal(manifest.verification.runtimeVerified, false);
  assert.equal(out.ok, true);
});

test("a preset carries only the eleven primary dimensions and defaults fill the rest", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "scdm-runner-preset-"));
  const presetPath = path.join(root, "params.json");
  const eleven = { L_N: 0.49, L: 3.69, D: 0.18, L_W: 1.72, C_RW: 0.23, C_TW: 0.06, B_W: 0.23, L_F: 3.2, C_RF: 0.38, C_TF: 0.19, B_F: 0.22 };
  await writeFile(presetPath, JSON.stringify(eleven));
  const request = buildModelRequest({ explicit: {}, preset: JSON.parse(await readFile(presetPath, "utf8")) });
  assert.equal(request.ok, true);
  for (const field of Object.keys(eleven)) assert.equal(request.sources[field], "preset");
  assert.equal(request.sources.k_nose, "default");
  assert.equal(request.sources.enclosure_name, "default");
  assert.equal(request.params.thickness_percent, 4.462);
  assert.equal(request.sources.thickness_percent, "default");
  assert.equal(request.params.model_offset, -4.0);
});

test("invalid parameters short-circuit before any file is written", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "scdm-runner-invalid-"));
  const out = await buildMissile({ execute: false, explicit: { L_W: 5 }, outputDir: path.join(root, "runs"), env: { LOCALAPPDATA: path.join(root, "cfg"), TEMP: root } });
  assert.equal(out.ok, false);
  assert.equal(out.status, "invalid-parameters");
  assert.match(out.errors.join("\n"), /前舵超出弹尾/);
  await assert.rejects(() => readFile(path.join(out.runDir, "params.json"), "utf8"));
});

test("the fin tip carries no planform arc and the junction bands still gate", () => {
  const request = buildModelRequest({ explicit: { fin_section: "hexagon" } });
  // A tip-planform record is no longer produced, so the gate no longer grades one at all.
  const stale = passingResult(request);
  stale.diagnostics.edgeFillets = [
    { label: "tip-planform", kind: "planform-arc", success: true, radiusM: 0.003, requestedM: 0.003, clamped: false },
    ...junctionReports(request),
  ];
  const graded = grade(request, stale);
  assert.equal(graded.checks.some((check) => check.name.includes("舵尖前后缘圆角")), false);
  assert.equal(graded.checks.some((check) => check.name.includes("planform")), false);

  const passed = passingResult(request);
  passed.diagnostics.edgeFillets = junctionReports(request);
  passed.diagnostics.sectionFillets = request.geometry.fins.flatMap((fin) => fin.sections.map((section) => ({
    name: fin.name,
    le: section.le,
    radiusM: section.leadingFilletRadiusM,
    minXM: section.le,
  })));
  passed.diagnostics.sectionAngles = [
    { name: "front", angleRad: request.geometry.fins[0].bodyIntersectionAngleRad },
    { name: "rear", angleRad: 0 },
  ];
  passed.diagnostics.repair = passingRepair(request);
  passed.diagnostics.finalGeometry = passingAudit(request);
  const gradedPass = grade(request, passed);
  assert.equal(gradedPass.ok, true, JSON.stringify(gradedPass.checks.filter((check) => !check.ok)));
  assert.equal(gradedPass.checks.find((check) => check.name === "舵面 front 与弹体交接圆角在 1.5–3 mm 之间").ok, true);
  assert.equal(gradedPass.checks.find((check) => check.name === "舵面 rear 与弹体交接圆角为 3 mm").ok, true);

  const aftOnly = passingResult(request);
  aftOnly.diagnostics.edgeFillets = junctionReports(request).filter((round) => round.station === "rear");
  assert.equal(grade(request, aftOnly).checks.find((check) => check.name === "舵面 front 与弹体交接圆角在 1.5–3 mm 之间").ok, false);

  const modest = passingResult(request);
  const reduced = junctionReports(request);
  reduced[0] = junctionAt(reduced[0], 0.002, true, request.params.cruciform_count);
  modest.diagnostics.edgeFillets = reduced;
  assert.equal(grade(request, modest).checks.find((check) => check.name === "舵面 front 与弹体交接圆角在 1.5–3 mm 之间").ok, true);
  assert.equal(grade(request, modest).checks.find((check) => check.name === "舵面 rear 与弹体交接圆角为 3 mm").ok, true);

  const tiny = passingResult(request);
  const subMillimetre = junctionReports(request);
  subMillimetre[0] = junctionAt(subMillimetre[0], 0.000132, true, request.params.cruciform_count);
  tiny.diagnostics.edgeFillets = subMillimetre;
  assert.equal(grade(request, tiny).checks.find((check) => check.name === "舵面 front 与弹体交接圆角在 1.5–3 mm 之间").ok, false);

  // Just under the 1.5 mm floor is a failure too: the band has to be met, not approached.
  const belowFloor = passingResult(request);
  const under = junctionReports(request);
  under[0] = junctionAt(under[0], 0.0012, true, request.params.cruciform_count);
  belowFloor.diagnostics.edgeFillets = under;
  assert.equal(grade(request, belowFloor).checks.find((check) => check.name === "舵面 front 与弹体交接圆角在 1.5–3 mm 之间").ok, false);

  // A kernel success whose created surfaces do not measure back at that radius proves nothing.
  const unmeasured = passingResult(request);
  const surfaces = junctionReports(request);
  surfaces[0] = { ...surfaces[0], measuredRadiiM: [] };
  unmeasured.diagnostics.edgeFillets = surfaces;
  assert.equal(grade(request, unmeasured).checks.find((check) => check.name === "舵面 front 与弹体交接圆角在 1.5–3 mm 之间").ok, false);

  const aftClamped = passingResult(request);
  const aftReduced = junctionReports(request);
  aftReduced[1] = junctionAt(aftReduced[1], 0.002, true, request.params.cruciform_count);
  aftClamped.diagnostics.edgeFillets = aftReduced;
  assert.equal(grade(request, aftClamped).checks.find((check) => check.name === "舵面 rear 与弹体交接圆角为 3 mm").ok, false);
});

/** One repair report matching what `repair_body` writes for a successful hexagon run. */
function passingRepair(request) {
  const limits = request.geometry.repair;
  const guards = limits.preserveRadiiM;
  const rounds = guards.map(() => 8);
  // With `hex_flat_end` at its new default the forward seam holds the full 3 mm round and no sliver
  // forms, so a passing run reports zero sub-floor non-round faces.
  return {
    before: { faces: 205, edges: 484, volumeM3: 0.09, surfaceM2: 2.7446, smallFaces: 0, roundFaces: rounds },
    after: { faces: 205, edges: 484, volumeM3: 0.09, surfaceM2: 2.7446, smallFaces: 0, roundFaces: rounds },
    inexact: { problemAreasBefore: 20, problemAreasAfter: 0, success: true },
    smallFaces: { facesBefore: 0, facesAfter: 0, fixed: 0 },
    guardRadiiM: guards,
    roundsPreserved: true,
    smallFacesAttempted: true,
    delta: { faces: 0, edges: 0, volumeRel: 1.8e-8, surfaceRel: -1.5e-6 },
    limits: { volume: limits.maxVolumeChange, surface: limits.maxSurfaceChange },
    volumeWithinLimit: true,
    surfaceWithinLimit: true,
  };
}

/** The audit record taken on the body that is saved. */
function passingAudit(request) {
  const guards = request.geometry.repair.preserveRadiiM;
  const rounds = guards.map(() => 8);
  return {
    roundFacesBefore: rounds,
    roundFacesAfter: rounds,
    roundsPreserved: true,
    smallFacesBefore: 0,
    smallFacesAfter: 0,
    smallFacesRetried: 0,
    problemAreasBefore: 0,
    problemAreasAfter: 0,
    problemAreasFinal: 0,
  };
}

function junctionReports(request) {
  return request.geometry.rootJunctionFillet.stations.map((station) => ({
    label: "root-junction",
    station: station.name,
    success: true,
    radiusM: 0.003,
    clamped: false,
    edges: request.params.cruciform_count * 4,
    measuredRadiiM: Array.from({ length: request.params.cruciform_count }, () => 0.003),
    xMin: station.le + (station.te - station.le) * 0.1,
    xMax: station.te - (station.te - station.le) * 0.1,
  }));
}

/** One junction record with its used radius and the measured blend cylinders kept consistent. */
function junctionAt(report, radiusM, clamped, count) {
  return { ...report, radiusM, clamped, measuredRadiiM: Array.from({ length: count }, () => radiusM) };
}

test("a passed run keeps the parameter file, the script, the model and its text records", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "scdm-runner-keep-"));
  for (const name of ["params.json", "model.py", "missile.scdoc", "result.json", "diagnostic.txt", "run.json", "request.json", "preview.png", "error.txt"]) {
    await writeFile(path.join(root, name), "x");
  }
  await retainModelOutputs(root, "missile.scdoc");
  assert.deepEqual((await readdir(root)).sort(), ["diagnostic.txt", "missile.scdoc", "model.py", "params.json", "result.json", "run.json"]);
});

test("hexagon leading-edge radius and the forward-fin angle are graded from the model", () => {
  const request = buildModelRequest({ explicit: { fin_section: "hexagon" } });
  const result = passingResult(request);
  result.diagnostics.sectionFillets = request.geometry.fins.flatMap((fin) => fin.sections.map((section) => ({
    name: fin.name,
    le: section.le,
    radiusM: section.leadingFilletRadiusM,
    minXM: section.le,
  })));
  result.diagnostics.sectionAngles = [
    { name: "front", angleRad: request.geometry.fins[0].bodyIntersectionAngleRad },
    { name: "rear", angleRad: 0 },
  ];
  const graded = grade(request, result);
  const leading = graded.checks.filter((check) => check.name.includes("前缘圆角"));
  assert.ok(leading.length >= 4);
  assert.equal(leading.every((check) => check.ok), true, JSON.stringify(leading));
  assert.ok(leading.some((check) => check.name.includes("0.0018")));
  assert.equal(graded.checks.find((check) => check.name.startsWith("前舵保持径向")).ok, true);
  assert.equal(graded.checks.find((check) => check.name === "后舵保持径向").ok, true);

  const shifted = structuredClone(result);
  shifted.diagnostics.sectionFillets[0].minXM += 0.01;
  assert.equal(grade(request, shifted).checks.find((check) => check.name.includes("前缘圆角")).ok, false);
  const wrongRadius = structuredClone(result);
  wrongRadius.diagnostics.sectionFillets[0].radiusM = 0.001;
  assert.equal(grade(request, wrongRadius).checks.find((check) => check.name.includes("0.0018")).ok, false);

  const flush = structuredClone(result);
  flush.diagnostics.sectionAngles[0].angleRad = 0.3;
  assert.equal(grade(request, flush).checks.find((check) => check.name.startsWith("前舵保持径向")).ok, false);
});

test("the default run root stays outside the repository", () => {
  assert.equal(defaultRunRoot({ SCDM_MISSILE_OUTPUT: "D:/models" }), "D:/models");
  assert.match(defaultRunRoot({}), /ansys-scdmdoc-mcp/);
  assert.equal(defaultRunRoot({}).startsWith(process.cwd()), false);
});
