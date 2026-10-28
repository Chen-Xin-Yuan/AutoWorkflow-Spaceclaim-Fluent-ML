import assert from "node:assert/strict";
import test from "node:test";
import {
  AIRFOIL_SAMPLES_PER_SIDE,
  HEX_FILLET_LE_RADIUS_M,
  HEX_FILLET_RADIUS_M,
  HEX_FILLET_RADIUS_TOLERANCE,
  HEX_FILLET_SEGMENT_COUNT,
  HEX_FILLET_SKETCH_MIN_ARC_M,
  HEX_FILLET_SKETCH_MIN_SAGITTA_M,
  HEX_ROOT_JUNCTION_FORWARD_MIN_RADIUS_M,
  HEX_ROOT_JUNCTION_PROBE_RADII_M,
  HEX_ROOT_JUNCTION_RADIUS_M,
  HEX_ROOT_JUNCTION_SEAM_TOLERANCE_M,
  HEX_TIP_CORNER_CUT_CLEARANCE_FACTOR,
  HEX_TIP_CORNER_RADIUS_M,
  bodyProfile,
  buildFinAirfoil,
  buildHexagonAirfoil,
  hexagonBevelAngle,
  hexagonSketchScale,
  planformTipCornerFillets,
  deriveGeometry,
  deriveRegions,
  finPhaseDegrees,
  finPlanform,
  nacaHalfThicknessRatio,
  nacaReferenceCoefficients,
  noseFilletLimits,
  polygonCentroid,
  polygonMoments,
  profilePolygon,
  profileProperties,
  regionProfile,
  revolvedFaceAreas,
  ruledLoftSolid,
  solveNoseFillet,
  solveNoseTipFillet,
  solidOfRevolutionCenterOfMass,
  taperedLoftCentroid,
  tipLeadingEdgeOffset,
} from "../lib/missile-geometry.mjs";
import { buildModelRequest } from "../lib/missile-params.mjs";

const baseline = { L: 3.69, L_N: 0.49, D: 0.18, k_nose: 0.005 };

test("the nose fillet is tangent to both the cone and the cylinder", () => {
  const fillet = solveNoseFillet(baseline);
  const R = baseline.D / 2;
  assert.equal(fillet.enabled, true);
  assert.ok(Math.abs(fillet.r_f - 0.01845) < 1e-12);
  // Both radii are exactly r_f, which is what makes the arc tangent to both surfaces.
  assert.ok(Math.abs(Math.hypot(fillet.t1[0] - fillet.cx, fillet.t1[1] - fillet.cy) - fillet.r_f) < 1e-12);
  assert.ok(Math.abs(Math.hypot(fillet.t2[0] - fillet.cx, fillet.t2[1] - fillet.cy) - fillet.r_f) < 1e-12);
  // Centre sits one radius below the cylinder line.
  assert.ok(Math.abs(fillet.cy - (R - fillet.r_f)) < 1e-12);
  assert.ok(Math.abs(fillet.t2[1] - R) < 1e-12);
  assert.ok(Math.abs(fillet.cx - 0.4916803333980655) < 1e-9);
  assert.ok(Math.abs(fillet.cy - 0.07155) < 1e-12);
  assert.ok(Math.abs(fillet.t - 0.0016806) < 1e-6);
});

test("the arc midpoint lies on the minor arc so the generator can pin the sweep", () => {
  const fillet = solveNoseFillet(baseline);
  const midDistance = Math.hypot(fillet.mid[0] - fillet.cx, fillet.mid[1] - fillet.cy);
  assert.ok(Math.abs(midDistance - fillet.r_f) < 1e-12);
  const toStart = Math.hypot(fillet.mid[0] - fillet.t1[0], fillet.mid[1] - fillet.t1[1]);
  const toEnd = Math.hypot(fillet.mid[0] - fillet.t2[0], fillet.mid[1] - fillet.t2[1]);
  // The chord across the minor arc is short; the complementary arc would put the midpoint far away.
  assert.ok(toStart < 0.005 && toEnd < 0.005);
  const complementary = Math.hypot(fillet.cx + fillet.r_f * 0.0908, fillet.cy - fillet.r_f * 0.9959);
  assert.ok(complementary > 0.05);
});

test("k_nose and k_tip independently switch the two rounded features off", () => {
  const sharp = bodyProfile({ ...baseline, k_nose: 0, k_tip: 0 });
  assert.equal(sharp.fillet.enabled, false);
  assert.equal(sharp.tip.enabled, false);
  assert.equal(sharp.segments.length, 4);
  assert.equal(sharp.segments.some((segment) => segment.type === "arc"), false);

  const tipOnly = bodyProfile({ ...baseline, k_nose: 0, k_tip: 0.002 });
  assert.equal(tipOnly.fillet.enabled, false);
  assert.equal(tipOnly.tip.enabled, true);
  assert.equal(tipOnly.segments.filter((segment) => segment.type === "arc").length, 1);

  const shoulderOnly = bodyProfile({ ...baseline, k_nose: 0.005, k_tip: 0, fillet_location: "cone_cylinder" });
  assert.equal(shoulderOnly.tip.enabled, false);
  assert.equal(shoulderOnly.fillet.enabled, true);
  assert.equal(shoulderOnly.shift, 0);
});

test("the tip cap is tangent to the cone and cannot swallow the nose", () => {
  const tip = solveNoseTipFillet({ ...baseline, k_tip: 0.002 });
  const R = baseline.D / 2;
  const alpha = Math.atan2(R, baseline.L_N);
  assert.ok(Math.abs(tip.r_t - 0.00738) < 1e-12);
  // A sphere of radius r_t centred at d on the axis touches the cone when d·sin(alpha) = r_t.
  assert.ok(Math.abs(tip.d * Math.sin(alpha) - tip.r_t) < 1e-12);
  assert.ok(Math.abs(Math.hypot(tip.tangent[0] - tip.d, tip.tangent[1]) - tip.r_t) < 1e-12);
  // The tangency point stays ahead of the shoulder, so a positive cone band survives.
  assert.ok(tip.tangent[0] < baseline.L_N);
  assert.ok(Math.abs(tip.offset - 0.033472) < 1e-6);
  assert.ok(tip.maxRadius > 0.091 && tip.maxRadius < 0.092);
});

test("blunting the tip keeps the overall length and moves the shoulder aft", () => {
  const shoulders = {};
  for (const enabled of [0, 0.002]) {
    const profile = bodyProfile({ ...baseline, k_nose: 0, k_tip: enabled, fillet_location: "nose_tip" });
    // The cone ends at the shoulder; the profile still starts at x = 0 and ends at x = L.
    shoulders[enabled] = profile.segments.find((segment) => segment.kind === "cone").to[0];
    assert.equal(profile.segments[0].from[0], 0);
    assert.equal(profile.segments.at(-2).to[0], baseline.L);
  }
  assert.ok(Math.abs(shoulders[0] - baseline.L_N) < 1e-12, "a sharp nose puts the shoulder at L_N");
  assert.ok(Math.abs(shoulders[0.002] - (baseline.L_N - 0.033472)) < 1e-5, "blunting moves the shoulder aft");
});

test("the fillet radius has a hard ceiling at the body radius", () => {
  const limits = noseFilletLimits(baseline);
  assert.ok(Math.abs(limits.maxK - 0.09 / 3.69) < 1e-12);
  assert.ok(limits.maxK > 0.0243 && limits.maxK < 0.0244);
});

test("the profile area matches the geometry proved by the SpaceClaim run", () => {
  const profile = bodyProfile({ ...baseline, fillet_location: "cone_cylinder" });
  const { area } = profileProperties(profile.segments);
  // A complementary (major) arc would give 0.3089805 instead, which is how the bug was caught.
  assert.ok(Math.abs(area - 0.3100499147) < 1e-8, `profile area was ${area}`);
});

test("the analytic face areas match the values measured in SpaceClaim", () => {
  const byKind = (params) => Object.fromEntries(revolvedFaceAreas(bodyProfile(params).segments).map((face) => [face.kind, face.area]));
  const shoulderOnly = byKind({ ...baseline, fillet_location: "cone_cylinder" });
  assert.ok(Math.abs(shoulderOnly.shoulder - 0.0018930508) < 1e-9, `shoulder ${shoulderOnly.shoulder}`);
  assert.ok(Math.abs(shoulderOnly.cone - 0.1399132083) < 1e-9, `cone ${shoulderOnly.cone}`);
  assert.ok(Math.abs(shoulderOnly.cylinder - 1.8086071623) < 1e-9, `cylinder ${shoulderOnly.cylinder}`);
  assert.ok(Math.abs(shoulderOnly.base - 0.0254469005) < 1e-9, `base ${shoulderOnly.base}`);
  const sharp = byKind({ ...baseline, k_nose: 0, k_tip: 0 });
  assert.ok(Math.abs(sharp.cone - 0.1408618120) < 1e-9, `sharp cone ${sharp.cone}`);
});

test("the blunt cap adds one face and keeps every other area analytic", () => {
  const faces = revolvedFaceAreas(bodyProfile({ ...baseline, k_tip: 0.002 }).segments);
  assert.deepEqual(faces.map((face) => face.kind), ["tip", "cone", "shoulder", "cylinder", "base"]);
  const tip = faces.find((face) => face.kind === "tip");
  // Spherical zone: 2·pi·r_t·h with h the axial height of the cap.
  const height = bodyProfile({ ...baseline, k_tip: 0.002 }).segments[0].to[0];
  assert.ok(Math.abs(tip.area - 2 * Math.PI * 0.00738 * height) < 1e-12, `cap ${tip.area}`);
  assert.ok(tip.area > 0.0002 && tip.area < 0.0004, `cap ${tip.area}`);
});

test("the analytic revolve volume matches the measured value", () => {
  const geometry = deriveGeometry(buildModelRequest({ explicit: { fillet_location: "cone_cylinder" } }).params);
  assert.ok(Math.abs(geometry.expectedVolume - 0.0855863605) < 1e-9, `volume ${geometry.expectedVolume}`);
});

test("the sharp body falls back to the full cone area and sharp volume", () => {
  const params = buildModelRequest({ explicit: { k_nose: 0, k_tip: 0 } }).params;
  const geometry = deriveGeometry(params);
  const cone = geometry.expectedAreas.faces.find((face) => face.kind === "cone");
  // Matches the value measured on the real install for the sharp cone face.
  assert.ok(Math.abs(cone.area - 0.1408618120) < 1e-9, `cone ${cone.area}`);
  assert.deepEqual(geometry.expectedAreas.faces.map((face) => face.kind), ["cone", "cylinder", "base"]);
  assert.ok(Math.abs(geometry.expectedVolume - 0.0855864099) < 1e-8, `volume ${geometry.expectedVolume}`);
});

test("the fin span is measured from the body surface, not from the embedded root", () => {
  const geometry = deriveGeometry(buildModelRequest({}).params);
  const R = 0.09;
  assert.ok(Math.abs(geometry.tip_to_tip - 0.64) < 1e-12, `tip_to_tip ${geometry.tip_to_tip}`);
  assert.ok(Math.abs(geometry.fins[0].root_radius - (R - 0.005)) < 1e-12);
  assert.ok(Math.abs(geometry.fins[0].tip_radius - (R + 0.23)) < 1e-12);
  assert.ok(Math.abs(geometry.fins[1].tip_radius - (R + 0.22)) < 1e-12);
});

test("expected extents match the measured boxes for rotated and higher-count cruciforms", () => {
  const baselineExtents = deriveGeometry(buildModelRequest({}).params).expectedExtents;
  assert.ok(Math.abs(baselineExtents.axial - 3.69) < 1e-12);
  assert.ok(Math.abs(baselineExtents.lateral - 0.64) < 1e-12);
  assert.ok(Math.abs(baselineExtents.vertical - 0.64) < 1e-12);

  // Analytic envelope of the NACA polyline. The + cruciform is still set by the fin tips;
  // the rotated and six-fin boxes include the airfoil thickness.
  const rotated = deriveGeometry(buildModelRequest({ explicit: { fin_phase: "x" } }).params).expectedExtents;
  assert.ok(rotated.lateral < baselineExtents.lateral, "an x cruciform fits in a smaller square than the + cruciform");
  assert.ok(Math.abs(rotated.lateral - rotated.vertical) < 1e-12);
  assert.ok(Math.abs(rotated.lateral - 0.4544486655) < 1e-9, `rotated lateral ${rotated.lateral}`);

  const six = deriveGeometry(buildModelRequest({ explicit: { cruciform_count: 6 } }).params).expectedExtents;
  assert.ok(Math.abs(six.lateral - 0.64) < 1e-12);
  assert.ok(six.vertical < six.lateral);
  assert.ok(Math.abs(six.vertical - 0.5555999915) < 1e-9, `six-fin vertical ${six.vertical}`);
});

test("fin planforms keep the trailing edges aligned", () => {
  const geometry = deriveGeometry(buildModelRequest({}).params);
  const [front, rear] = geometry.fins;
  // The model is placed by the centring rule, so the absolute stations carry that shift; the
  // planform's own shape is read off relative to the nose tip.
  const relative = (fin) => fin.points.map((point) => Number((point[0] - geometry.nose_tip_x).toFixed(6)));
  assert.deepEqual(relative(front), [1.716304, 1.95, 1.95, 1.89]);
  assert.deepEqual(relative(rear), [3.195682, 3.58, 3.58, 3.39]);
  for (const fin of [front, rear]) {
    assert.equal(fin.points[1][0], fin.points[2][0], "trailing edges must share an axial station");
  }
  assert.ok(Math.abs(tipLeadingEdgeOffset({ root_chord: 0.23, tip_chord: 0.06, le_mode: "trailing_aligned" }) - 0.17) < 1e-12);
  assert.throws(() => tipLeadingEdgeOffset({ root_chord: 0.23, tip_chord: 0.06, le_mode: "nope" }), /Unsupported le_mode/);
});

test("the visible leading edge and root chord are the ones the parameters name", () => {
  // The fin is sunk `root_overlap` into the body to keep the boolean union clean. That embed has to
  // extend the design trapezoid's edges inward, or the visible leading edge drifts aft by the sweep
  // over the buried depth - which is exactly what a ruler on the finished model reads.
  const request = buildModelRequest({});
  const [front, rear] = request.geometry.fins;
  // Walk the buried-root-to-tip leading edge and see where it crosses the body surface.
  const leadingEdgeAt = (fin, radius) => {
    const fraction = (radius - fin.root_radius) / (fin.tip_radius - fin.root_radius);
    return fin.buried_le + fraction * (fin.tip_le - fin.buried_le);
  };
  for (const [fin, station, chord] of [[front, 1.72, 0.23], [rear, 3.2, 0.38]]) {
    // The station is a difference of two absolute stations, so it carries the placement offset's
    // last-bit rounding; the tolerance is there for that, not for any modelling slack.
    assert.ok(Math.abs(fin.leFromNoseTip - station) < 1e-12, "the leading edge must meet the body at the design station");
    assert.ok(Math.abs(fin.leAtSurface - (station + request.geometry.nose_tip_x)) < 1e-12);
    assert.ok(Math.abs(fin.rootChordAtSurface - chord) < 1e-12);
    assert.ok(Math.abs(leadingEdgeAt(fin, fin.surface_radius) - station - request.geometry.nose_tip_x) < 1e-12, `${fin.name}: the leading edge must cross r = R at ${station}`);
    assert.ok(Math.abs(fin.te - station - request.geometry.nose_tip_x - chord) < 1e-12, "the visible root chord must be the design chord");
  }
  // The buried root chord is longer, by exactly the sweep consumed over the buried depth.
  assert.ok(Math.abs(Math.abs(front.points[0][0] - front.te) - 0.233696) < 1e-6);
  assert.ok(Math.abs(Math.abs(rear.points[0][0] - rear.te) - 0.384318) < 1e-6);
  // No edge of the planform may lie on the body surface: that face is coincident with the cylinder
  // and makes Combine.Merge fail to fuse.
  for (const fin of [front, rear]) {
    assert.equal(fin.points.some((point) => point[1] === fin.surface_radius), false);
  }
});

test("fin stations are absolute distances from the nose tip, blunting or not", () => {
  const blunt = buildModelRequest({});
  const sharp = buildModelRequest({ explicit: { fillet_location: "cone_cylinder" } });
  assert.equal(sharp.geometry.body.tip.enabled, false);
  assert.ok(sharp.geometry.tip_offset === 0);
  // Blunting shortens the cone and pulls the shoulder toward the tip...
  assert.ok(Math.abs(blunt.geometry.tip_offset - 0.033472) < 1e-6);
  assert.ok(blunt.geometry.nose_junction_x < sharp.geometry.nose_junction_x);
  // ...and the placement rule places the whole model, but L_W / L_F stay the distance from the nose
  // tip.
  for (const request of [blunt, sharp]) {
    const [front, rear] = request.geometry.fins;
    assert.ok(Math.abs(front.leFromNoseTip - 1.72) < 1e-12, "the front fin must sit exactly L_W behind the nose tip");
    assert.ok(Math.abs(rear.leFromNoseTip - 3.2) < 1e-12, "the rear fin must sit exactly L_F behind the nose tip");
    assert.ok(Math.abs(front.leStation - (1.72 + request.geometry.nose_tip_x)) < 1e-12);
    assert.equal(request.geometry.expectedExtents.axial, 3.69, "the total length must stay L");
  }
  // The shoulder moving is the price of holding both the total length and the tip station; it must
  // not leak into the fin station.
  assert.ok(Math.abs(blunt.geometry.fins[0].leStation - sharp.geometry.fins[0].leStation) < 1e-12);
  assert.ok(Math.abs(blunt.geometry.fins[0].leFromNoseJunction - 1.261792) < 1e-5);
});

test("the placement rule centres the missile and the BOI, and model_offset nudges the pair", () => {
  const base = buildModelRequest({});
  const L = base.params.L;
  // The block to centre is the union of the missile and the BOI, and the BOI encloses the missile,
  // so the union's midpoint is the BOI's: the rule has to sit the BOI's forward margin further
  // forward than the plain missile-centred station.
  assert.equal(base.placement.mode, "ensemble");
  assert.ok(Math.abs(base.placement.rule - (6.38 - 1.0)) < 1e-12);
  // The default nudge is 4 m toward the nose (negative = -X), which is on top of the rule.
  assert.equal(base.placement.requested, -4.0);
  assert.ok(Math.abs(base.placement.offset - (base.placement.rule - 4.0)) < 1e-12);
  assert.equal(base.geometry.nose_tip_x, base.placement.offset);
  assert.ok(Math.abs(base.geometry.nose_tip_x - 1.38) < 1e-12);
  // The block therefore sits exactly model_offset ahead of the enclosure's centre.
  const enclosureMid = (base.regions.enclosure.xFront + base.regions.enclosure.xBack) / 2;
  const boiMid = (base.regions.boi.xFront + base.regions.boi.xBack) / 2;
  assert.ok(Math.abs(boiMid - enclosureMid - base.placement.requested) < 1e-12, "the missile + BOI block must sit model_offset ahead of centre");
  // With the nudge taken back out, it is dead centre: that is what makes the rule a centring rule.
  assert.ok(Math.abs(boiMid - base.placement.requested - enclosureMid) < 1e-12);
  // The missile on its own is then one BOI half-margin forward of the block - that is the BOI's own
  // asymmetry (0.5 m forward, 2.5 m aft), not a placement error.
  const missileMid = base.placement.offset + L / 2;
  assert.ok(Math.abs((enclosureMid - missileMid) - (1.0 - base.placement.requested)) < 1e-12);
  // Both objects travel together while the boundary stays where it was.
  assert.equal(base.regions.enclosure.xFront, -3.69);
  assert.equal(base.regions.enclosure.xBack, 3.69 + 16.45);
  assert.equal(base.regions.boi.xFront, base.placement.offset - 0.5);
  assert.equal(base.regions.boi.xBack, base.placement.offset + L + 2.5);
  // The enclosure cushions compensate, so the same absolute boundary comes out of the command.
  assert.equal(base.regions.enclosure.cushion.axisLow, 3.69 + base.placement.offset);
  assert.equal(base.regions.enclosure.cushion.axisHigh, 16.45 - base.placement.offset);

  // The nudge is a nudge, not a mode: zero gives the exact centre back.
  const centred = buildModelRequest({ explicit: { model_offset: 0 } });
  const centredMid = (centred.regions.boi.xFront + centred.regions.boi.xBack) / 2;
  assert.ok(Math.abs(centred.geometry.nose_tip_x - 5.38) < 1e-12);
  assert.ok(Math.abs(centredMid - enclosureMid) < 1e-12, "with no nudge the block must sit dead centre");
  // ...and the sign follows +X, so a positive value moves the block toward the tail.
  const aft = buildModelRequest({ explicit: { model_offset: 1.2 } });
  assert.ok(Math.abs(aft.geometry.nose_tip_x - 6.58) < 1e-12);
  assert.ok(Math.abs((aft.regions.boi.xFront + aft.regions.boi.xBack) / 2 - enclosureMid - 1.2) < 1e-12);

  // Centring the missile alone is the other reading of "in the middle", and it is one parameter away.
  const missile = buildModelRequest({ explicit: { model_placement: "missile", model_offset: 0 } });
  const missileEnclosureMid = (missile.regions.enclosure.xFront + missile.regions.enclosure.xBack) / 2;
  assert.ok(Math.abs(missile.geometry.nose_tip_x + L / 2 - missileEnclosureMid) < 1e-12, "the missile body must sit dead centre");
  // Centring the missile pushes the BOI 1 m aft of centre, because the BOI reaches further behind
  // the tail (2.5 m) than ahead of the nose (0.5 m). That is the difference between the two modes.
  const missileBoiMid = (missile.regions.boi.xFront + missile.regions.boi.xBack) / 2;
  assert.ok(Math.abs((missileBoiMid - missileEnclosureMid) - 1.0) < 1e-12);
  // A nudge still applies on top of that mode.
  const missileNudged = buildModelRequest({ explicit: { model_placement: "missile", model_offset: -0.5 } });
  assert.ok(Math.abs(missileNudged.geometry.nose_tip_x - (6.38 - 0.5)) < 1e-12);

  // `offset` mode drops the rule: the station is the nudge itself, measured from the model's own
  // frame origin.
  const pinned = buildModelRequest({ explicit: { model_placement: "offset", model_offset: 0 } });
  assert.equal(pinned.placement.rule, 0);
  assert.equal(pinned.geometry.nose_tip_x, 0);
  assert.equal(pinned.regions.boi.xFront, -0.5);
  assert.ok(Math.abs(pinned.regions.enclosure.xFront - base.regions.enclosure.xFront) < 1e-12, "the boundary must not move with the model");
  assert.equal(pinned.regions.enclosure.cushion.axisLow, 3.69);
  // Everything rigid travels together: same lengths, shifted stations.
  assert.equal(pinned.geometry.expectedExtents.axial, base.geometry.expectedExtents.axial);
  assert.ok(Math.abs(base.geometry.bodyCenterOfMass - pinned.geometry.bodyCenterOfMass - base.placement.offset) < 1e-9);
  assert.ok(Math.abs(base.geometry.fins[0].leAtSurface - pinned.geometry.fins[0].leAtSurface - base.placement.offset) < 1e-9);
});

test("the body centre of mass pins the model to its absolute axial placement", () => {
  const request = buildModelRequest({});
  const geometry = request.geometry;
  const com = geometry.bodyCenterOfMass;
  const noseFirst = com - geometry.nose_tip_x;
  assert.ok(Number.isFinite(noseFirst) && noseFirst > 1.9 && noseFirst < 2.1, `com ${noseFirst} is the nose-first centre of mass`);
  assert.ok(Math.abs(noseFirst - 1.9896708050876761) < 1e-9, "the placement offset must move the centre of mass by exactly itself");
  // Pappus: the axial moment must be the centroid radius times the area, and the volume 2 pi times it.
  const properties = profileProperties(geometry.body.segments);
  const moments = polygonMoments(profilePolygon(geometry.body.segments));
  assert.ok(Math.abs(Math.abs(moments.momentY) - properties.centroidRadius * properties.area) < 1e-12);
  assert.ok(Math.abs(2 * Math.PI * Math.abs(moments.momentY) - geometry.expectedVolume) < 1e-12);
  assert.ok(Math.abs(Math.abs(moments.momentXY) / Math.abs(moments.momentY) - com) < 1e-12);
  // A square's mixed moment is a quarter of its area, which is what the closed form has to produce.
  assert.ok(Math.abs(polygonMoments([[0, 0], [1, 0], [1, 1], [0, 1]]).momentXY - 0.25) < 1e-12);
  // Moving the whole profile by a shift moves the centre of mass by exactly that shift, which is
  // what makes this reading able to detect a wrong tip datum.
  const shifted = geometry.body.segments.map((segment) => ({
    ...segment,
    from: [segment.from[0] + 0.5, segment.from[1]],
    to: [segment.to[0] + 0.5, segment.to[1]],
    ...(segment.center ? { center: [segment.center[0] + 0.5, segment.center[1]] } : {}),
  }));
  assert.ok(Math.abs(solidOfRevolutionCenterOfMass(shifted) - (com + 0.5)) < 1e-9);
});

test("the seed fin centroid is the NACA solid centroid the gate measures", () => {
  const geometry = deriveGeometry(buildModelRequest({}).params);
  for (const fin of geometry.fins) {
    const planform = polygonCentroid(fin.points);
    assert.ok(fin.centroid[0] > fin.buried_le && fin.centroid[0] < fin.te, `${fin.name} centroid must sit inside the chord`);
    assert.ok(fin.centroid[1] > fin.root_radius && fin.centroid[1] < fin.tip_radius, `${fin.name} centroid must sit on the span`);
    assert.equal(fin.centroid[2], 0);
    // Thickness grows with chord, so the solid sits inboard of the flat-plate centroid.
    assert.ok(fin.centroid[1] < planform[1], `${fin.name} solid centroid ${fin.centroid[1]} planform ${planform[1]}`);
    const root = fin.sections[0];
    const tip = fin.sections[1];
    assert.equal(root.points.length, tip.points.length);
    assert.ok(Math.abs(root.points[0][0] - fin.buried_le) < 1e-12);
    assert.ok(Math.abs(root.points[0][1]) < 1e-12);
    assert.ok(Math.abs(tip.points[0][0] - fin.tip_le) < 1e-12);
    const trailing = root.points.filter((point) => Math.abs(point[0] - fin.te) < 1e-12);
    assert.equal(trailing.length, 2);
    assert.ok(trailing[0][1] > 0 && trailing[1][1] < 0);
  }
  // A square rotated into the first quadrant has its centroid at the corner average.
  assert.deepEqual(polygonCentroid([[0, 0], [2, 0], [2, 2], [0, 2]]).map((value) => Number(value.toFixed(12))), [1, 1]);
});

test("the geometry layer can compute the reserved modes, while the schema gates them off", () => {
  // The plan keeps these as reserved values: the maths is total so promoting one is a one-line
  // schema change, but validation refuses them until they are implemented and verified.
  const leading = finPlanform({ le_x: 1.72, root_chord: 0.23, tip_chord: 0.06, span: 0.23, root_radius: 0.085, body_radius: 0.09, le_mode: "leading_aligned" });
  // A radially aligned leading edge is vertical, so burying the root does not sweep it.
  assert.equal(leading.buried_le, 1.72);
  assert.equal(leading.tip_le, 1.72);
  assert.equal(tipLeadingEdgeOffset({ root_chord: 0.23, tip_chord: 0.06, le_mode: "symmetric" }), 0.085);
  assert.throws(() => finPlanform({ le_x: 1.72, root_chord: 0.23, tip_chord: 0.06, span: 0.23, root_radius: 0.085, body_radius: 0.09, le_mode: "nope" }), /Unsupported/);
  assert.equal(buildModelRequest({ explicit: { le_mode: "leading_aligned" } }).ok, false);
  assert.equal(finPhaseDegrees("+"), 0);
  assert.equal(finPhaseDegrees("x"), 45);
  assert.throws(() => finPhaseDegrees("y"), /Unsupported fin_phase/);
});

test("the BOI profile revolves into a three-face cylinder", () => {
  const params = buildModelRequest({}).params;
  const geometry = deriveGeometry(params);
  const { boi } = deriveRegions(params, geometry);
  assert.equal(boi.mode, "revolve");
  const faces = revolvedFaceAreas(regionProfile(boi));
  assert.deepEqual(faces.map((face) => face.kind), ["front-cap", "cylinder", "back-cap"]);
  assert.ok(Math.abs(faces.find((face) => face.kind === "cylinder").area - 2 * Math.PI * boi.radius * boi.length) < 1e-9);
  const cap = Math.PI * boi.radius ** 2;
  assert.ok(Math.abs(faces.find((face) => face.kind === "front-cap").area - cap) < 1e-9);
  assert.ok(Math.abs(faces.find((face) => face.kind === "back-cap").area - cap) < 1e-9);
});

test("the enclosure is handed to the Enclosure command as a cushion around the bounding box", () => {
  const params = buildModelRequest({}).params;
  const geometry = deriveGeometry(params);
  const { enclosure } = deriveRegions(params, geometry);
  assert.equal(enclosure.mode, "enclosure-command");
  // The command adds the radial cushion to the box centre -> corner distance, so the absolute radius
  // the user asked for comes back out only after subtracting that envelope.
  const envelope = Math.hypot(geometry.expectedExtents.lateral / 2, geometry.expectedExtents.vertical / 2);
  assert.ok(Math.abs(enclosure.envelopeRadius - envelope) < 1e-12);
  assert.ok(Math.abs(enclosure.envelopeRadius - 0.45254834) < 1e-8);
  assert.ok(Math.abs(enclosure.cushion.radius - (enclosure.radius - envelope)) < 1e-12);
  assert.ok(Math.abs(enclosure.cushion.radius - 2.24745166) < 1e-8);
  // Axial cushions are plain offsets from the box ends, measured from where the model actually sits,
  // so the placement station has to be added back on the nose side and taken off on the tail side.
  assert.equal(enclosure.cushion.axisLow, enclosure.front + geometry.nose_tip_x);
  assert.equal(enclosure.cushion.axisHigh, enclosure.back - geometry.nose_tip_x);
  assert.ok(enclosure.cushion.radius > 0);
});

test("the enclosure cushion follows each cruciform's real bounding box", () => {
  // The cushion has to be derived from the real box, not from D + 2·max(B): a phase-rotated or
  // higher-count cruciform has a different box, so the same absolute radius needs another cushion.
  for (const overrides of [{}, { fin_phase: "x" }, { cruciform_count: 6 }]) {
    const request = buildModelRequest({ explicit: overrides });
    assert.equal(request.ok, true, request.errors.join(" | "));
    const { enclosure } = request.regions;
    const envelope = Math.hypot(request.geometry.expectedExtents.lateral / 2, request.geometry.expectedExtents.vertical / 2);
    assert.ok(Math.abs(enclosure.envelopeRadius - envelope) < 1e-12, JSON.stringify(overrides));
    assert.ok(Math.abs(enclosure.cushion.radius + enclosure.envelopeRadius - enclosure.radius) < 1e-12);
    assert.ok(enclosure.cushion.radius > 0);
  }
  // A phase-rotated cruciform is narrower than the aligned one, so its cushion is larger.
  const aligned = buildModelRequest({});
  const rotated = buildModelRequest({ explicit: { fin_phase: "x" } });
  assert.ok(rotated.regions.enclosure.envelopeRadius < aligned.regions.enclosure.envelopeRadius);
  assert.ok(rotated.regions.enclosure.cushion.radius > aligned.regions.enclosure.cushion.radius);
});

test("regions wrap the model with the agreed margins", () => {
  const params = buildModelRequest({}).params;
  const geometry = deriveGeometry(params);
  const { enclosure, boi, tipToTip } = deriveRegions(params, geometry);
  const noseTip = geometry.nose_tip_x;
  const tail = noseTip + params.L;
  for (const region of [enclosure, boi]) {
    assert.ok(region.radius > tipToTip / 2, `${region.name} must clear the fin tips`);
    assert.ok(region.xFront < noseTip, `${region.name} must extend ahead of the nose`);
    assert.ok(region.xBack > tail, `${region.name} must extend behind the tail`);
  }
  assert.ok(enclosure.radius > boi.radius, "the enclosure must contain the BOI");
  assert.ok(enclosure.length > boi.length);
  // The model moved aft inside a boundary that stayed put: the nose gap grew and the tail gap shrank.
  assert.ok(Math.abs((noseTip - enclosure.xFront) - (params.enclosure_front + noseTip)) < 1e-12);
  assert.ok(Math.abs((enclosure.xBack - tail) - (params.enclosure_back - noseTip)) < 1e-12);
});

test("default geometry uses an aligned cruciform with four fins per station", () => {
  const geometry = deriveGeometry(buildModelRequest({}).params);
  assert.equal(geometry.cruciform_count, 4);
  assert.equal(geometry.fin_phase_deg, 0);
  assert.deepEqual(geometry.fins.map((fin) => fin.name), ["front", "rear"]);
  assert.equal(geometry.airfoil.kind, "naca");
  assert.equal(geometry.airfoil.thickness_percent, 4.48);
  assert.equal(geometry.airfoil.le_radius_index, 6);
  assert.equal(geometry.airfoil.max_thickness_tenths, 3);
  assert.equal(geometry.fins[0].sections.length, 2);
  assert.equal(geometry.fins[1].sections.length, 2);
});

test("the default NACA section matches the shared thickness equation", () => {
  const coefficients = nacaReferenceCoefficients(6, 3);
  const ratio = 4.48 / 100;
  assert.ok(Math.abs(nacaHalfThicknessRatio(0.3, coefficients, ratio) - ratio / 2) < 1e-12);
  assert.ok(Math.abs(nacaHalfThicknessRatio(0, coefficients, ratio)) < 1e-15);
  assert.ok(Math.abs(nacaHalfThicknessRatio(1, coefficients, ratio) - 0.002 * ratio / 0.2) < 1e-12);
  const sharp = nacaReferenceCoefficients(0, 3);
  assert.equal(sharp.a0, 0);
  assert.ok(Math.abs(nacaHalfThicknessRatio(0, sharp, ratio)) < 1e-15);
});

function samePoint(a, b) {
  return Math.abs(a[0] - b[0]) < 1e-12 && Math.abs(a[1] - b[1]) < 1e-12;
}

function loopHasEdge(loop, start, end) {
  for (let index = 0; index < loop.length; index += 1) {
    const next = loop[(index + 1) % loop.length];
    if ((samePoint(loop[index], start) && samePoint(next, end)) || (samePoint(loop[index], end) && samePoint(next, start))) return true;
  }
  return false;
}

function assertContinuousArc(arc, radius) {
  assert.equal(arc.type, "arc");
  assert.equal(arc.sense, arc.sweep === "cw");
  assert.ok(Math.abs(arc.radius - radius) < 1e-12);
  assert.ok(Math.abs(arc.delta) > 1e-5 && Math.abs(arc.delta) < Math.PI);
  for (const point of [arc.from, arc.to, arc.via]) {
    assert.ok(Math.abs(Math.hypot(point[0] - arc.center[0], point[1] - arc.center[1]) - radius) < 1e-9);
  }
}

test("a ruled loft of similar sections matches the closed-form centroid", () => {
  const root = [[1, 0], [1.4, 0], [1.2, 0.2]];
  const tip = [[1.1, 0], [1.3, 0], [1.2, 0.1]];
  const solid = ruledLoftSolid(
    { radius: 0.1, points: root },
    { radius: 0.4, points: tip },
  );
  const closed = taperedLoftCentroid({ le0: 1, c0: 0.4, r0: 0.1, le1: 1.1, c1: 0.2, r1: 0.4, areaRatio: 0.25, xiBar: 0.5 });
  assert.ok(Math.abs(solid.volume - closed.volume) < 1e-12);
  assert.ok(Math.abs(solid.centroid[0] - closed.centroid[0]) < 1e-12);
  assert.ok(Math.abs(solid.centroid[1] - closed.centroid[1]) < 1e-12);
  assert.equal(solid.centroid[2], 0);
  const plate = ruledLoftSolid(
    { radius: 2, points: [[0, 0], [1, 0], [1, 1], [0, 1]] },
    { radius: 5, points: [[0, 0], [1, 0], [1, 1], [0, 1]] },
  );
  assert.ok(Math.abs(plate.volume - 3) < 1e-12);
  assert.ok(Math.abs(plate.centroid[0] - 0.5) < 1e-12);
  assert.ok(Math.abs(plate.centroid[1] - 3.5) < 1e-12);
});

test("a hexagonal section keeps a 1 mm circular fillet without moving the chord extremes", () => {
  const chord = 0.23;
  const thicknessPercent = 4.48;
  const hex = buildHexagonAirfoil({ thickness_percent: thicknessPercent, hex_flat_start: 0.2, hex_flat_end: 0.8, chord });
  const half = thicknessPercent / 100 / 2 * chord;
  const band = HEX_FILLET_RADIUS_M * HEX_FILLET_RADIUS_TOLERANCE;
  assert.equal(hex.fillet.clamped, false);
  assert.equal(hex.fillet.clampNote, "");
  assert.ok(Math.abs(hex.fillet.radius - HEX_FILLET_RADIUS_M) < 1e-15);
  assert.ok(Math.abs(hex.fillet.radius - HEX_FILLET_RADIUS_M) <= band);
  assert.equal(hex.fillet.unit, "m");
  assert.equal(hex.fillet.leadingClamped, false);
  assert.ok(Math.abs(hex.fillet.leadingRadius - HEX_FILLET_LE_RADIUS_M) < 1e-15);
  assert.equal(hex.segments.length, HEX_FILLET_SEGMENT_COUNT);
  assert.equal(hex.segments.filter((segment) => segment.type === "arc").length, 6);
  assertContinuousArc(hex.fillet.leading, HEX_FILLET_LE_RADIUS_M);
  assertContinuousArc(hex.fillet.trailing, hex.fillet.radius);
  for (const shoulder of hex.fillet.shoulders) assertContinuousArc(shoulder, hex.fillet.radius);
  const xs = hex.loop.map((point) => point[0]);
  const ys = hex.loop.map((point) => point[1]);
  assert.ok(Math.abs(Math.min(...xs)) < 1e-12, "leading-edge station stays at the sharp point");
  assert.ok(Math.abs(Math.max(...xs) - chord) < 1e-12, "trailing-edge station stays at the sharp point");
  assert.ok(Math.abs(Math.max(...xs) - Math.min(...xs) - chord) < 1e-12);
  assert.ok(Math.abs(Math.max(...ys) - half) < 1e-12, "maximum thickness is unchanged");
  assert.ok(Math.abs(Math.min(...ys) + half) < 1e-12);
  assert.ok(Math.abs(hex.properties.cy) < 1e-12);
  assert.ok(samePoint(hex.fillet.leading.extreme, [0, 0]));
  assert.ok(samePoint(hex.fillet.trailing.extreme, [chord, 0]));
  for (const junction of hex.fillet.junctions) {
    const radial = [junction.at[0] - junction.center[0], junction.at[1] - junction.center[1]];
    const edge = [junction.toward[0] - junction.at[0], junction.toward[1] - junction.at[1]];
    const scale = Math.hypot(radial[0], radial[1]) * Math.hypot(edge[0], edge[1]);
    assert.ok(Math.abs(radial[0] * edge[0] + radial[1] * edge[1]) / scale < 1e-9);
    assert.ok(loopHasEdge(hex.loop, junction.at, junction.toward));
  }
  const flat = hex.loop.filter((point) => Math.abs(point[1] - half) < 1e-12).map((point) => point[0]).sort((a, b) => a - b);
  assert.ok(flat.length >= 2);
  assert.ok(flat[0] > 0.2 * chord && flat[0] < 0.2 * chord + 0.002);
  assert.ok(flat[flat.length - 1] < 0.8 * chord && flat[flat.length - 1] > 0.8 * chord - 0.002);

  const thinChord = 0.03;
  const thin = buildHexagonAirfoil({ thickness_percent: thicknessPercent, hex_flat_start: 0.2, hex_flat_end: 0.8, chord: thinChord });
  const thinHalf = thicknessPercent / 100 / 2 * thinChord;
  assert.equal(thin.fillet.clamped, true);
  assert.ok(thin.fillet.clampNote.length > 0);
  assert.ok(thin.fillet.radius < HEX_FILLET_RADIUS_M);
  assert.ok(thin.fillet.radius > 0);
  assert.ok(thin.fillet.radius < thinHalf);
  assert.ok(thin.fillet.leadingRadius <= thin.fillet.radius + 1e-12);
  assert.ok(hexagonSketchScale(thin.segments) <= 1000);
  const thinX = thin.loop.map((point) => point[0]);
  const thinY = thin.loop.map((point) => point[1]);
  assert.ok(Math.abs(Math.min(...thinX)) < 1e-12);
  assert.ok(Math.abs(Math.max(...thinX) - thinChord) < 1e-12);
  assert.ok(Math.abs(Math.max(...thinY) - thinHalf) < 1e-12);

  const request = buildModelRequest({ explicit: { fin_section: "hexagon" } });
  assert.equal(request.ok, true, request.errors.join(" | "));
  assert.equal(request.geometry.airfoil.kind, "hexagon");
  assert.equal(request.geometry.airfoil.edges, HEX_FILLET_SEGMENT_COUNT);
  assert.equal(request.geometry.airfoil.hex_fillet_radius_m, HEX_FILLET_RADIUS_M);
  assert.equal(request.geometry.airfoil.hex_fillet_le_radius_m, HEX_FILLET_LE_RADIUS_M);
  assert.equal(request.geometry.airfoil.samplesPerSide, null);
  assert.equal(request.geometry.expectedFinFaceCount, (HEX_FILLET_SEGMENT_COUNT + 1) * request.params.cruciform_count * 2);
  for (const fin of request.geometry.fins) {
    assert.notEqual(fin.sections[0], fin.sections[1]);
    assert.equal(fin.sections[0].segments.length, fin.sections[1].segments.length);
    assert.equal(fin.sections[0].segments.filter((segment) => segment.type === "arc").length, 6);
    for (const section of fin.sections) {
      const sectionX = section.points.map((point) => point[0]);
      const tip = section === fin.sections[1];
      assert.ok(Math.abs(section.filletRadiusM - HEX_FILLET_RADIUS_M) < 1e-15);
      if (fin.name === "front" && tip) {
        assert.equal(section.leadingFilletClamped, true);
        assert.ok(section.leadingFilletRadiusM < HEX_FILLET_LE_RADIUS_M);
        assert.ok(section.leadingFilletRadiusM > HEX_FILLET_RADIUS_M);
      } else {
        assert.equal(section.leadingFilletClamped, false);
        assert.ok(Math.abs(section.leadingFilletRadiusM - HEX_FILLET_LE_RADIUS_M) < 1e-15);
      }
      assert.ok(Math.abs(Math.min(...sectionX) - section.le) < 1e-12);
      assert.ok(Math.abs(Math.max(...sectionX) - (section.le + section.chord)) < 1e-12);
    }
    assert.ok(Math.abs(fin.leFromNoseTip - request.params[fin.name === "front" ? "L_W" : "L_F"]) < 1e-12);
  }

  const moved = buildModelRequest({ explicit: { fin_section: "hexagon", hex_flat_start: 0.15, hex_flat_end: 0.7 } });
  const tip = moved.geometry.fins[0].sections[1];
  const tipFlat = tip.points.filter((point) => point[1] > 0 && Math.abs(point[1] - tip.chord * 4.48 / 100 / 2) < 1e-12)
    .map((point) => point[0]).sort((a, b) => a - b);
  assert.ok(tipFlat[0] > tip.le + 0.15 * tip.chord);
  assert.ok(tipFlat[tipFlat.length - 1] < tip.le + 0.7 * tip.chord);
  const sharpNose = buildModelRequest({ explicit: { fin_section: "hexagon", le_radius_index: 0 } });
  const roundNose = buildModelRequest({ explicit: { fin_section: "hexagon", le_radius_index: 9 } });
  assert.deepEqual(sharpNose.geometry.fins[0].sections[0].points, roundNose.geometry.fins[0].sections[0].points);
  assert.ok(request.geometry.fins[0].volume > buildModelRequest({}).geometry.fins[0].volume);
  const naca = buildFinAirfoil({ thickness_percent: 4.48, le_radius_index: 6, max_thickness_tenths: 3 });
  assert.equal(naca.kind, "naca");
  assert.equal(naca.loop.length, 2 * AIRFOIL_SAMPLES_PER_SIDE + 1);
  assert.equal(naca.filletRadiusRequested, undefined);
  const bevel = hexagonBevelAngle(request.params.thickness_percent, request.params.hex_flat_start);
  const front = request.geometry.fins.find((fin) => fin.name === "front");
  const rear = request.geometry.fins.find((fin) => fin.name === "rear");
  assert.ok(Math.abs(front.bodyIntersectionAngleRad - bevel) < 1e-12);
  assert.ok(Math.abs(front.sectionFrame.normal[1] - Math.cos(bevel)) < 1e-12);
  assert.ok(Math.abs(front.sectionFrame.normal[2] - Math.sin(bevel)) < 1e-12);
  assert.equal(rear.sectionFrame, undefined);
  assert.equal(rear.bodyIntersectionAngleRad, undefined);
  assert.ok(Math.abs(front.leFromNoseTip - request.params.L_W) < 1e-12);
  assert.ok(Math.abs(rear.leFromNoseTip - request.params.L_F) < 1e-12);
  const nacaGeometry = buildModelRequest({});
  assert.equal(nacaGeometry.geometry.fins[0].sectionFrame, undefined);
  assert.ok(Math.abs(request.geometry.expectedExtents.lateral - request.geometry.expectedExtents.vertical) < 1e-12);
  assert.ok(request.geometry.expectedExtents.lateral < 0.64);
  assert.ok(request.geometry.expectedExtents.lateral > 0.63);
  assert.ok(Math.abs(naca.loop[0][0]) < 1e-15);
  assert.ok(Math.abs(naca.loop[0][1]) < 1e-15);
});

test("hexagon loft profiles are two distinct chains of tangent arcs", () => {
  const request = buildModelRequest({ explicit: { fin_section: "hexagon" } });
  assert.equal(request.ok, true, request.errors.join(" | "));
  for (const fin of request.geometry.fins) {
    const [root, tip] = fin.sections;
    assert.notEqual(root, tip);
    assert.equal(root.segments.length, HEX_FILLET_SEGMENT_COUNT);
    assert.equal(tip.segments.length, HEX_FILLET_SEGMENT_COUNT);
    assert.notDeepEqual(root.segments[0].from, tip.segments[0].from);
    if (fin.name === "front") assert.ok(tip.sketchScale > 1);
    for (const section of fin.sections) {
      const arcs = section.segments.filter((segment) => segment.type === "arc");
      assert.equal(arcs.length, 6);
      assert.equal(section.sketchScale, hexagonSketchScale(section.segments));
      assert.ok(section.sketchScale >= 1);
      for (const arc of arcs) {
        const enlarged = section.sketchScale;
        const length = Math.abs(arc.delta) * arc.radius * enlarged;
        const sagitta = arc.radius * (1 - Math.cos(Math.abs(arc.delta) / 2)) * enlarged;
        assert.ok(length + 1e-12 >= HEX_FILLET_SKETCH_MIN_ARC_M);
        assert.ok(sagitta + 1e-15 >= HEX_FILLET_SKETCH_MIN_SAGITTA_M);
      }
      for (let index = 0; index < section.segments.length; index += 1) {
        const segment = section.segments[index];
        const next = section.segments[(index + 1) % section.segments.length];
        assert.ok(samePoint(segment.to, next.from), `${fin.name} profile does not close`);
        if (segment.type !== "arc") continue;
        assert.equal(segment.sense, segment.sweep === "cw");
        assert.ok(Math.abs(segment.delta) < Math.PI);
        const radial = [segment.to[0] - segment.center[0], segment.to[1] - segment.center[1]];
        const edge = [next.from[0] - next.to[0], next.from[1] - next.to[1]];
        const scale = Math.hypot(radial[0], radial[1]) * Math.hypot(edge[0], edge[1]);
        if (next.type === "line" && scale > 0) {
          assert.ok(Math.abs(radial[0] * edge[0] + radial[1] * edge[1]) / scale < 1e-8);
        }
      }
    }
  }
  const thin = buildHexagonAirfoil({
    thickness_percent: 4.48,
    hex_flat_start: 0.2,
    hex_flat_end: 0.8,
    chord: 0.03,
  });
  assert.throws(() => hexagonSketchScale([{ type: "arc", radius: 1e-9, delta: 1e-6 }]));
  assert.equal(thin.segments.filter((segment) => segment.type === "arc").length, 6);
  for (const segment of thin.segments.filter((item) => item.type === "arc")) {
    assert.ok(Math.abs(segment.radius - thin.fillet.radius) < 1e-9);
    assert.ok(Math.abs(segment.delta) < Math.PI);
  }
});

test("hexagon tip corners and the root junction are 3 mm tangent rounds", () => {
  const request = buildModelRequest({ explicit: { fin_section: "hexagon" } });
  assert.equal(request.ok, true, request.errors.join(" | "));
  assert.equal(request.geometry.rootJunctionFillet.radius, HEX_ROOT_JUNCTION_RADIUS_M);
  assert.equal(request.geometry.rootJunctionFillet.forwardMinRadius, HEX_ROOT_JUNCTION_FORWARD_MIN_RADIUS_M);
  assert.deepEqual(request.geometry.rootJunctionFillet.probeRadii, HEX_ROOT_JUNCTION_PROBE_RADII_M);
  assert.equal(request.geometry.rootJunctionFillet.seamToleranceM, HEX_ROOT_JUNCTION_SEAM_TOLERANCE_M);
  assert.equal(request.geometry.rootJunctionFillet.unit, "m");
  // The requested forward range is 1.5–3 mm: the floor is what the kernel accepts on the smallest
  // hexagon fin, and every probe radius sits inside the band and strictly descends.
  assert.equal(HEX_ROOT_JUNCTION_FORWARD_MIN_RADIUS_M, 0.0015);
  assert.equal(HEX_ROOT_JUNCTION_RADIUS_M, 0.003);
  const probes = request.geometry.rootJunctionFillet.probeRadii;
  assert.equal(probes[0], HEX_ROOT_JUNCTION_RADIUS_M);
  assert.equal(probes[probes.length - 1], HEX_ROOT_JUNCTION_FORWARD_MIN_RADIUS_M);
  assert.ok(probes.every((radius, index) => radius >= HEX_ROOT_JUNCTION_FORWARD_MIN_RADIUS_M && (index === 0 || radius < probes[index - 1])));
  assert.ok(HEX_ROOT_JUNCTION_SEAM_TOLERANCE_M > 0 && HEX_ROOT_JUNCTION_SEAM_TOLERANCE_M < HEX_ROOT_JUNCTION_FORWARD_MIN_RADIUS_M);
  const stations = request.geometry.rootJunctionFillet.stations;
  assert.deepEqual(stations.map((station) => station.name), ["front", "rear"]);
  assert.equal(stations[0].allowReducedRadius, true);
  assert.equal(stations[1].allowReducedRadius, false);
  assert.ok(stations[0].xMax < stations[1].xMin);
  assert.ok(stations[0].te - stations[0].le > 0);
  assert.equal(request.geometry.airfoil.hex_tip_corner_radius_m, HEX_TIP_CORNER_RADIUS_M);
  const naca = buildModelRequest({});
  assert.equal(naca.geometry.rootJunctionFillet, undefined);
  assert.equal(naca.geometry.fins[0].tipCornerFillet, undefined);
  for (const fin of request.geometry.fins) {
    const fillet = fin.tipCornerFillet;
    assert.equal(fillet.leAtSurface, fin.le_at_surface);
    assert.equal(fillet.rootChord, fin.root_chord_at_surface);
    assert.ok(Math.abs(fillet.tipChord - (fin.te - fin.tip_le)) < 1e-12);
    assert.equal(fillet.tipRadius, fin.tip_radius);
    assert.equal(fillet.cutClearanceM, HEX_TIP_CORNER_RADIUS_M * HEX_TIP_CORNER_CUT_CLEARANCE_FACTOR);
    const [buriedLe, rootTe, tipTe, tipLe] = fin.points;
    const onSegment = (point, start, end) => {
      const dx = end[0] - start[0];
      const dy = end[1] - start[1];
      const length = Math.hypot(dx, dy);
      const cross = Math.abs((point[0] - start[0]) * dy - (point[1] - start[1]) * dx);
      const along = ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / (length * length);
      return cross / length < 1e-8 && along > 1e-6 && along < 1 - 1e-6;
    };
    const normalTo = (corner, foot, start, end) => {
      const radial = [foot[0] - corner.center[0], foot[1] - corner.center[1]];
      const edge = [end[0] - start[0], end[1] - start[1]];
      const scale = Math.hypot(radial[0], radial[1]) * Math.hypot(edge[0], edge[1]);
      return Math.abs(radial[0] * edge[0] + radial[1] * edge[1]) / scale < 1e-8;
    };
    const expectTangent = (corner, first, second) => {
      const feet = [corner.from, corner.to];
      const onFirst = feet.filter((foot) => onSegment(foot, first[0], first[1]));
      const onSecond = feet.filter((foot) => onSegment(foot, second[0], second[1]));
      assert.equal(onFirst.length, 1, `${fin.name} ${corner.name} is not tangent to both edges`);
      assert.equal(onSecond.length, 1, `${fin.name} ${corner.name} is not tangent to both edges`);
      assert.ok(normalTo(corner, onFirst[0], first[0], first[1]));
      assert.ok(normalTo(corner, onSecond[0], second[0], second[1]));
    };
    expectTangent(fillet.leading, [buriedLe, tipLe], [tipLe, tipTe]);
    expectTangent(fillet.trailing, [tipLe, tipTe], [tipTe, rootTe]);
    for (const corner of [fillet.leading, fillet.trailing]) {
      assert.equal(corner.clamped, false);
      assert.ok(corner.radius > 0.0025, "a sub-millimetre tip radius is not the 3 mm planform arc");
      assert.ok(Math.abs(corner.radius - HEX_TIP_CORNER_RADIUS_M) < 1e-12);
      assert.equal(corner.type, "arc");
      assert.ok(Math.abs(corner.delta) < Math.PI);
      for (const point of [corner.from, corner.to, corner.via]) {
        assert.ok(Math.abs(Math.hypot(point[0] - corner.center[0], point[1] - corner.center[1]) - corner.radius) < 1e-9);
      }
      const arcs = corner.removal.filter((segment) => segment?.type === "arc");
      const lines = corner.removal.filter((segment) => segment?.type === "line");
      assert.equal(arcs.length, 1);
      assert.equal(lines.length, 2);
      assert.ok(Math.abs(arcs[0].radius - HEX_TIP_CORNER_RADIUS_M) < 1e-12);
      assert.equal(arcs[0].sense, corner.sense);
    }
    assert.ok(fillet.straightTipM > 0);
    assert.ok(fillet.straightTipM < fillet.tipChord);
    assert.ok(fillet.tipChord - fillet.straightTipM > 0.004, `straight tip ${fillet.straightTipM} would still be a sharp or sub-millimetre corner`);
    const sharpLeading = [fin.tip_le, fin.tip_radius];
    const sharpTrailing = [fin.te, fin.tip_radius];
    assert.ok(Math.hypot(fillet.leading.center[0] - sharpLeading[0], fillet.leading.center[1] - sharpLeading[1]) > fillet.leading.radius);
    assert.ok(Math.hypot(fillet.trailing.center[0] - sharpTrailing[0], fillet.trailing.center[1] - sharpTrailing[1]) > fillet.trailing.radius);
  }
  const direct = planformTipCornerFillets(request.geometry.fins[0]);
  assert.equal(direct.leading.name, "tip-leading");
  assert.equal(direct.trailing.name, "tip-trailing");
  assert.throws(
    () => planformTipCornerFillets({
      points: [[0, 0.085], [0.01, 0.085], [0.01, 0.086], [0.0095, 0.086]],
      le_at_surface: 0,
      root_chord_at_surface: 0.01,
      te: 0.01,
      tip_le: 0.0095,
      tip_radius: 0.086,
    }),
    /拐角夹角 .+°，相邻边长/,
  );
});
