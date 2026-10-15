import assert from "node:assert/strict";
import test from "node:test";
import { ALL_PARAMS, GEOMETRY_FIELDS, defaultParams } from "../lib/missile-schema.mjs";
import { MIN_TIP_CHORD, buildModelRequest, coerceParams, mergeParams, optimisationPool, validateMissileParams } from "../lib/missile-params.mjs";

const baseline = () => defaultParams();

function errorsFor(overrides) {
  return buildModelRequest({ explicit: overrides }).errors;
}

test("every geometry parameter can be overridden on its own", () => {
  for (const field of GEOMETRY_FIELDS) {
    const override = 0.123;
    const merged = mergeParams({ [field]: override });
    assert.equal(merged.values[field], override, `${field} did not accept an override`);
    assert.equal(merged.sources[field], "explicit", `${field} must be recorded as explicit`);
    const request = buildModelRequest({ explicit: { [field]: override } });
    if (field === "t_fin" || field === "k_nose" || field === "D" || field === "L" || field === "L_N") continue;
    assert.equal(request.sources[field], "explicit");
  }
});

test("every convention parameter can be overridden on its own", () => {
  const overrides = { fin_phase: "x", root_overlap: 0.008, le_mode: "trailing_aligned", fillet_location: "cone_cylinder", cruciform_count: 6, scdm_api_version: "V241", output_dir: "C:/tmp", file_name: "x.scdoc" };
  for (const param of ALL_PARAMS) {
    if (!(param.field in overrides)) continue;
    const merged = mergeParams({ [param.field]: overrides[param.field] });
    assert.equal(merged.values[param.field], overrides[param.field], `${param.field} did not accept an override`);
  }
});

test("explicit values win over the preset, which wins over the default", () => {
  const merged = mergeParams({ L: 4.0 }, { L: 5.0, D: 0.2 });
  assert.equal(merged.values.L, 4.0);
  assert.equal(merged.sources.L, "explicit");
  assert.equal(merged.values.D, 0.2);
  assert.equal(merged.sources.D, "preset");
  assert.equal(merged.values.L_N, 0.49);
  assert.equal(merged.sources.L_N, "default");
});

test("a preset that omits fields falls back to defaults per field", () => {
  const merged = mergeParams({}, { C_TW: 0.05 });
  assert.equal(merged.values.C_TW, 0.05);
  assert.equal(merged.sources.C_TW, "preset");
  assert.equal(merged.values.C_TF, 0.19);
  assert.equal(merged.sources.C_TF, "default");
});

test("unknown fields are rejected instead of being ignored", () => {
  const merged = mergeParams({ L_span: 1 });
  assert.deepEqual(merged.unknown, ["L_span"]);
  const request = buildModelRequest({ explicit: { L_span: 1 } });
  assert.equal(request.ok, false);
  assert.match(request.errors.join("\n"), /未知参数 'L_span'/);
});

test("numeric strings are coerced and junk is rejected", () => {
  const coerced = coerceParams({ ...baseline(), L: "3.7", cruciform_count: "4" });
  assert.equal(coerced.values.L, 3.7);
  assert.equal(coerced.values.cruciform_count, 4);
  assert.deepEqual(coerced.errors, []);
  const bad = coerceParams({ ...baseline(), L: "abc" });
  assert.match(bad.errors.join("\n"), /L 必须是数值/);
  const nonInteger = coerceParams({ ...baseline(), cruciform_count: 4.5 });
  assert.match(nonInteger.errors.join("\n"), /cruciform_count 必须是整数/);
});

test("the baseline parameter set validates cleanly", () => {
  const request = buildModelRequest({});
  assert.equal(request.ok, true);
  assert.deepEqual(request.errors, []);
  assert.deepEqual(request.warnings, []);
  assert.deepEqual(request.corrections, []);
  assert.ok(request.geometry);
});

test("hard errors reject geometrically impossible sets", () => {
  const cases = [
    [{ L_N: 0 }, /L_N 必须大于 0/],
    [{ L_N: 4.0 }, /必须小于弹身总长度/],
    [{ D: 0 }, /D 必须大于 0/],
    [{ C_RW: 0 }, /C_RW 必须大于 0/],
    [{ B_W: 0 }, /B_W 必须大于 0/],
    [{ C_TW: -0.01 }, /C_TW 不能为负/],
    [{ t_fin: 0 }, /t_fin 必须大于 0/],
    [{ L_W: 0.3 }, /前舵轴向位置 L_W/],
    [{ C_RW: 2.0 }, /前舵超出弹尾/],
    [{ L_F: 3.4 }, /后舵超出弹尾/],
    [{ L_W: 3.0 }, /前后舵重叠/],
    [{ k_nose: -0.001 }, /k_nose 不能为负/],
    [{ k_nose: 0.03 }, /必须小于机身半径/],
    [{ fin_phase: "y" }, /fin_phase 'y' 暂不支持/],
    [{ le_mode: "leading_aligned" }, /le_mode 'leading_aligned' 暂不支持/],
    [{ root_overlap: 0 }, /root_overlap 必须大于 0/],
    [{ root_overlap: 0.25 }, /必须小于最小展长/],
    [{ cruciform_count: 3 }, /cruciform_count 必须是大于等于 2 的偶数/],
  ];
  for (const [overrides, pattern] of cases) {
    const request = buildModelRequest({ explicit: overrides });
    assert.equal(request.ok, false, `${JSON.stringify(overrides)} should be rejected`);
    assert.match(request.errors.join("\n"), pattern, `${JSON.stringify(overrides)} produced: ${request.errors.join(" | ")}`);
  }
});

test("an unimplemented enum value never falls back silently", () => {
  const request = buildModelRequest({ explicit: { le_mode: "symmetric" } });
  assert.equal(request.ok, false);
  assert.equal(request.geometry, null);
  assert.match(request.errors.join("\n"), /不会静默回退/);
});

test("a zero tip chord is corrected to the documented floor", () => {
  const request = buildModelRequest({ explicit: { C_TW: 0 } });
  assert.equal(request.ok, true);
  assert.equal(request.params.C_TW, MIN_TIP_CHORD);
  assert.equal(request.geometry.fins[0].points[3][1] > 0, true);
  assert.equal(request.corrections.length, 1);
  assert.match(request.corrections[0].reason, /退化梢边/);
});

test("advisory warnings do not block the build", () => {
  const outOfRange = buildModelRequest({ explicit: { L_W: 2.5 } });
  assert.equal(outOfRange.ok, true);
  assert.match(outOfRange.warnings.join("\n"), /L_W = 2.5 超出优化区间/);

  const bluntTip = buildModelRequest({ explicit: { C_TW: 0.3 } });
  assert.equal(bluntTip.ok, true);
  assert.match(bluntTip.warnings.join("\n"), /前舵梢弦大于根弦/);

  const tinyFillet = buildModelRequest({ explicit: { k_nose: 0.0002 } });
  assert.equal(tinyFillet.ok, true);
  assert.match(tinyFillet.warnings.join("\n"), /小于 1 mm/);

  const bigFillet = buildModelRequest({ explicit: { k_nose: 0.015 } });
  assert.equal(bigFillet.ok, true);
  assert.match(bigFillet.warnings.join("\n"), /超过机身半径的一半/);
});

test("k_nose = 0 removes only the shoulder fillet", () => {
  const request = buildModelRequest({ explicit: { k_nose: 0 } });
  assert.equal(request.ok, true);
  assert.equal(request.geometry.body.fillet.enabled, false);
  // The blunt tip stays unless it is switched off as well.
  assert.equal(request.geometry.body.tip.enabled, true);
  assert.deepEqual(request.geometry.expectedAreas.faces.map((face) => face.kind), ["tip", "cone", "cylinder", "base"]);
});

test("both rounded features can be switched off together", () => {
  const request = buildModelRequest({ explicit: { k_nose: 0, k_tip: 0 } });
  assert.equal(request.ok, true);
  assert.equal(request.geometry.body.segments.length, 4);
  assert.equal(request.geometry.body.segments.some((segment) => segment.type === "arc"), false);
  assert.deepEqual(request.geometry.expectedAreas.faces.map((face) => face.kind), ["cone", "cylinder", "base"]);
});

test("the blunt tip follows k_tip and is bounded by the cone", () => {
  const blunt = buildModelRequest({ explicit: { k_tip: 0.008 } });
  assert.equal(blunt.ok, true);
  assert.equal(blunt.geometry.body.tip.enabled, true);
  assert.ok(Math.abs(blunt.geometry.body.tip.r_t - 0.02952) < 1e-12);
  assert.ok(Math.abs(blunt.geometry.expectedExtents.axial - 3.69) < 1e-12, "the overall length must not change");

  const tooBig = buildModelRequest({ explicit: { k_tip: 0.03 } });
  assert.equal(tooBig.ok, false);
  assert.match(tooBig.errors.join("\n"), /弹尖倒圆半径 r_t .* 过大/);

  const negative = buildModelRequest({ explicit: { k_tip: -0.001 } });
  assert.equal(negative.ok, false);
  assert.match(negative.errors.join("\n"), /k_tip 不能为负/);
});

test("fillet_location selects which rounded features are built", () => {
  const tipOnly = buildModelRequest({ explicit: { fillet_location: "nose_tip" } });
  assert.equal(tipOnly.geometry.body.tip.enabled, true);
  assert.equal(tipOnly.geometry.body.fillet.enabled, false);

  const shoulderOnly = buildModelRequest({ explicit: { fillet_location: "cone_cylinder" } });
  assert.equal(shoulderOnly.geometry.body.tip.enabled, false);
  assert.equal(shoulderOnly.geometry.body.fillet.enabled, true);

  const both = buildModelRequest({});
  assert.equal(both.geometry.body.tip.enabled, true);
  assert.equal(both.geometry.body.fillet.enabled, true);

  const rejected = buildModelRequest({ explicit: { fillet_location: "nose_tip_only" } });
  assert.equal(rejected.ok, false);
  assert.match(rejected.errors.join("\n"), /fillet_location 'nose_tip_only' 暂不支持/);
});

test("a large tip blunting is reported as a warning", () => {
  const request = buildModelRequest({ explicit: { k_tip: 0.02 } });
  assert.equal(request.ok, true);
  assert.match(request.warnings.join("\n"), /弹尖钝化截掉了/);
});

test("simulation regions default to the agreed enclosure and BOI sizes", () => {
  const request = buildModelRequest({});
  const { enclosure, boi } = request.regions;
  assert.equal(enclosure.radius, 2.7);
  assert.equal(enclosure.front, 3.69);
  assert.equal(enclosure.back, 16.45);
  assert.ok(Math.abs(enclosure.length - 23.83) < 1e-9);
  assert.ok(Math.abs(enclosure.expectedExtents.axial - 23.83) < 1e-9);
  assert.ok(Math.abs(enclosure.expectedExtents.lateral - 5.4) < 1e-9);
  assert.ok(Math.abs(enclosure.expectedVolume - Math.PI * 2.7 ** 2 * 23.83) < 1e-6);
  assert.equal(boi.radius, 0.45);
  assert.equal(boi.front, 0.5);
  assert.equal(boi.back, 2.5);
  assert.ok(Math.abs(boi.length - 6.69) < 1e-9);
  assert.ok(Math.abs(boi.expectedVolume - Math.PI * 0.45 ** 2 * 6.69) < 1e-6);
  // The enclosure is the frame the model is placed in, so its stations do not move with the model;
  // the BOI travels with the missile. Placement centres the missile + BOI block and then applies the
  // nudge, so the model moves and the boundary stays put.
  assert.ok(Math.abs(enclosure.xFront - -3.69) < 1e-9);
  assert.ok(Math.abs(enclosure.xBack - 20.14) < 1e-9);
  assert.equal(request.params.model_offset, -4.0, "the parameter stays what the schema says");
  assert.equal(request.placement.mode, "ensemble");
  assert.ok(Math.abs(request.placement.rule - 5.38) < 1e-12);
  assert.equal(request.placement.requested, -4.0);
  assert.ok(Math.abs(request.placement.offset - 1.38) < 1e-9, "the station is the rule plus the nudge");
  assert.ok(Math.abs(boi.xFront - (1.38 - 0.5)) < 1e-9);
  assert.ok(Math.abs(boi.xBack - (1.38 + 3.69 + 2.5)) < 1e-9);
  // The enclosure goes through the Prepare > Enclosure command, which takes cushions, while the BOI
  // stays a revolved solid. The cushions are measured from where the model now sits.
  assert.equal(enclosure.mode, "enclosure-command");
  assert.equal(boi.mode, "revolve");
  assert.ok(Math.abs(enclosure.cushion.axisLow - (3.69 + 1.38)) < 1e-9);
  assert.ok(Math.abs(enclosure.cushion.axisHigh - (16.45 - 1.38)) < 1e-9);
  assert.ok(Math.abs(enclosure.cushion.radius - (2.7 - enclosure.envelopeRadius)) < 1e-12);
  assert.ok(enclosure.cushion.radius > 0, "the cushion must be a growth, not a shrink");
  // The resolved station is the one the geometry was built at, so the analytic centres of mass and
  // the measured ones stay in one absolute frame.
  assert.equal(request.geometry.nose_tip_x, request.placement.offset);
  assert.equal(request.geometry.model_offset, request.placement.offset);
});

test("every region parameter can be overridden", () => {
  const overrides = { enclosure_shape: "cylinder", enclosure_radius: 3.0, enclosure_front: 4.0, enclosure_back: 20.0, boi_radius: 0.6, boi_front: 0.8, boi_back: 3.0 };
  const merged = mergeParams(overrides);
  for (const [field, value] of Object.entries(overrides)) {
    assert.equal(merged.values[field], value, `${field} did not accept an override`);
    assert.equal(merged.sources[field], "explicit");
  }
  const request = buildModelRequest({ explicit: overrides });
  assert.equal(request.ok, true, request.errors.join(" | "));
  assert.ok(Math.abs(request.regions.enclosure.length - (4.0 + 3.69 + 20.0)) < 1e-9);
});

test("regions must enclose the model and stay nested", () => {
  const cases = [
    [{ enclosure_radius: 0.3 }, /外壳半径 .* 必须大于模型最大半径/],
    [{ boi_radius: 0.3 }, /BOI 半径 .* 必须大于模型最大半径/],
    [{ boi_radius: 2.8 }, /BOI 半径 .* 必须小于外壳半径/],
    // In the centring modes the BOI is part of the block that gets placed, so an oversized margin
    // shows up as the whole block no longer fitting rather than as the BOI poking through one end.
    [{ model_placement: "offset", boi_front: 5 }, /BOI 前伸 .* 超出外壳前端面/],
    [{ model_placement: "offset", boi_back: 30 }, /BOI 后伸 .* 超出外壳后端面/],
    [{ model_offset: 0, boi_front: 25 }, /外壳后伸 .* 必须大于导弹的轴向站位/],
    [{ model_offset: 0, boi_back: 30 }, /外壳前伸 .* 加导弹的轴向站位 .* 必须大于 0/],
    // The manual station only applies in `offset` mode; the centring modes are checked through the
    // station they resolve to, which is why these two have to say so explicitly.
    [{ model_placement: "offset", model_offset: 20 }, /外壳后伸 .* 必须大于导弹的轴向站位/],
    [{ model_placement: "offset", model_offset: -5 }, /外壳前伸 .* 加导弹的轴向站位 .* 必须大于 0/],
    // A mode that would leave the model outside the boundary is rejected even though nothing is
    // wrong with the mode itself.
    [{ enclosure_front: 0, enclosure_back: 0.4 }, /外壳前伸长度必须大于 0|外壳后伸 .* 必须大于导弹的轴向站位/],
    [{ model_placement: "nope" }, /model_placement 'nope' 暂不支持/],
    [{ enclosure_front: 0 }, /外壳前伸长度必须大于 0/],
    [{ enclosure_shape: "sphere" }, /enclosure_shape 'sphere' 暂不支持/],
  ];
  for (const [overrides, pattern] of cases) {
    const request = buildModelRequest({ explicit: overrides });
    assert.equal(request.ok, false, `${JSON.stringify(overrides)} should be rejected`);
    assert.match(request.errors.join("\n"), pattern);
  }
});

test("an enclosure radius inside the model's bounding-box corner radius is rejected", () => {
  // The Enclosure command grows from the bounding box, so a radius that does not clear the corner
  // would be handed over as a negative cushion and produce a boundary inside the model.
  const request = buildModelRequest({ explicit: { enclosure_radius: 0.4, boi_radius: 0.35 } });
  assert.equal(request.ok, false);
  assert.match(request.errors.join("\n"), /必须大于模型包围盒的角点半径/);
  assert.equal(request.regions, null);
  assert.equal(request.geometry, null);
  // Just above the corner radius it builds again.
  const ok = buildModelRequest({ explicit: { enclosure_radius: 0.5, boi_radius: 0.35 } });
  assert.equal(ok.ok, true, ok.errors.join(" | "));
  assert.ok(ok.regions.enclosure.cushion.radius > 0);
});

test("tight regions warn without blocking", () => {
  const tightBoi = buildModelRequest({ explicit: { boi_radius: 0.34 } });
  assert.equal(tightBoi.ok, true);
  assert.match(tightBoi.warnings.join("\n"), /加密区贴得很紧/);
  const tightEnclosure = buildModelRequest({ explicit: { enclosure_radius: 1.0 } });
  assert.equal(tightEnclosure.ok, true);
  assert.match(tightEnclosure.warnings.join("\n"), /远场边界可能影响解/);
});

test("k_nose below the geometric ceiling still builds", () => {
  const request = buildModelRequest({ explicit: { k_nose: 0.024 } });
  assert.equal(request.ok, true);
  assert.equal(request.geometry.body.fillet.enabled, true);
});

test("the optimisation pool is exposed as data for later sweeps", () => {
  assert.ok(optimisationPool().includes("k_nose"));
  assert.ok(optimisationPool().includes("L_W"));
  assert.ok(!optimisationPool().includes("t_fin"));
});
