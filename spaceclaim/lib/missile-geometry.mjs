/**
 * Analytic geometry for the missile body and fins.
 *
 * Everything here is pure arithmetic in metres, so the geometry can be unit-tested without
 * launching SpaceClaim. The generated SCDM script consumes these numbers and only issues API
 * calls; it contains no geometry constants of its own.
 */

export function bodyRadius(D) {
  return D / 2;
}

/**
 * Tangent fillet between the nose cone and the cylindrical section.
 *
 * Cone line runs from the apex (0, 0) to (L_N, R); the cylinder line is r = R.
 * The fillet centre sits at r = R - r_f, and its distance to the cone line is exactly r_f,
 * which makes the arc tangent to both surfaces.
 */
export function solveNoseFillet({ L, L_N, D, k_nose }) {
  const R = bodyRadius(D);
  const S = Math.hypot(L_N, R);
  const alpha = Math.atan2(R, L_N);
  const r_f = L * k_nose;
  if (!(r_f > 0)) {
    return { enabled: false, r_f: 0, alpha, alphaDeg: (alpha * 180) / Math.PI, S, cx: L_N, cy: R, t: 0, t1: [L_N, R], t2: [L_N, R], sweep: "none" };
  }
  const cx = (r_f * S + L_N * (R - r_f)) / R;
  const cy = R - r_f;
  const t = r_f * Math.tan(alpha / 2);
  const nx = R / S;
  const ny = -L_N / S;
  const t1 = [cx - r_f * nx, cy - r_f * ny];
  const t2 = [cx, R];
  const v1 = [t1[0] - cx, t1[1] - cy];
  const v2 = [t2[0] - cx, t2[1] - cy];
  const cross = v1[0] * v2[1] - v1[1] * v2[0];
  const bisectorLength = Math.hypot(v1[0] + v2[0], v1[1] + v2[1]);
  const bisector = [(v1[0] + v2[0]) / bisectorLength, (v1[1] + v2[1]) / bisectorLength];
  const mid = [cx + r_f * bisector[0], cy + r_f * bisector[1]];
  return {
    enabled: true,
    r_f,
    alpha,
    alphaDeg: (alpha * 180) / Math.PI,
    S,
    cx,
    cy,
    t,
    t1,
    t2,
    mid,
    sweep: cross < 0 ? "cw" : "ccw",
  };
}

/** Hard geometric limits for the nose fillet. The binding one is r_f < R. */
export function noseFilletLimits({ L, L_N, D }) {
  const R = bodyRadius(D);
  const S = Math.hypot(L_N, R);
  return { R, S, maxK: R / L, maxRadius: R, maxTangent: Math.min(S, L - L_N) };
}

/**
 * Tangent sphere that blunts the nose tip.
 *
 * The sphere centre sits on the axis at distance d from the theoretical apex; a sphere of radius
 * r_t is tangent to the cone when d·sin(alpha) = r_t, so the ground tip is truncated by
 * `offset = d - r_t`. The cone itself is left untouched: the profile is shifted aft by that offset
 * so the overall length stays L, which is what the bounding-box check expects.
 */
export function solveNoseTipFillet({ L, L_N, D, k_tip }) {
  const R = bodyRadius(D);
  const S = Math.hypot(L_N, R);
  const alpha = Math.atan2(R, L_N);
  const r_t = L * (k_tip ?? 0);
  // The tangency point must stay ahead of the shoulder, otherwise the blunt cap swallows the cone.
  const maxRadius = (L_N * Math.sin(alpha)) / Math.cos(alpha) ** 2;
  if (!(r_t > 0)) {
    return { enabled: false, r_t: 0, alpha, alphaDeg: (alpha * 180) / Math.PI, d: 0, offset: 0, front: [0, 0], tangent: [0, 0], mid: [0, 0], sweep: "none", maxRadius, maxK: maxRadius / L };
  }
  const d = r_t / Math.sin(alpha);
  const front = [d - r_t, 0];
  const tangent = [d * Math.cos(alpha) ** 2, d * Math.cos(alpha) * Math.sin(alpha)];
  const midAngle = (Math.PI + (Math.PI / 2 + alpha)) / 2;
  const mid = [d + r_t * Math.cos(midAngle), r_t * Math.sin(midAngle)];
  return {
    enabled: true,
    r_t,
    alpha,
    alphaDeg: (alpha * 180) / Math.PI,
    d,
    offset: d - r_t,
    front,
    tangent,
    mid,
    sweep: "cw",
    maxRadius,
    maxK: maxRadius / L,
  };
}

/** Length, sweep and centroid of a profile arc, in the (x, r) plane. */
export function arcGeometry(segment) {
  const [cx, cy] = segment.center;
  const radius = Math.hypot(segment.from[0] - cx, segment.from[1] - cy);
  const start = Math.atan2(segment.from[1] - cy, segment.from[0] - cx);
  const end = Math.atan2(segment.to[1] - cy, segment.to[0] - cx);
  let delta = end - start;
  while (delta <= -Math.PI) delta += 2 * Math.PI;
  while (delta > Math.PI) delta -= 2 * Math.PI;
  if (segment.sweep === "cw" && delta > 0) delta -= 2 * Math.PI;
  if (segment.sweep === "ccw" && delta < 0) delta += 2 * Math.PI;
  const half = delta / 2;
  const offset = Math.abs(half) < 1e-12 ? 0 : (radius * Math.sin(half)) / half;
  const bisector = start + half;
  const centroid = [cx + offset * Math.cos(bisector), cy + offset * Math.sin(bisector)];
  return { radius, delta, length: radius * Math.abs(delta), centroid, centroidRadius: centroid[1] };
}

/** Sample the closed profile as a polygon so its area, centroid and revolved volume can be checked. */
export function profilePolygon(segments, steps = 512) {
  const points = [];
  for (const segment of segments) {
    if (segment.type === "line") {
      points.push(segment.from);
      continue;
    }
    const [cx, cy] = segment.center;
    const { radius, delta } = arcGeometry(segment);
    const start = Math.atan2(segment.from[1] - cy, segment.from[0] - cx);
    for (let index = 0; index < steps; index += 1) {
      const angle = start + delta * (index / steps);
      points.push([cx + radius * Math.cos(angle), cy + radius * Math.sin(angle)]);
    }
  }
  return points;
}

export function profileProperties(segments) {
  const points = profilePolygon(segments);
  let twiceArea = 0;
  let momentTerm = 0;
  for (let index = 0; index < points.length; index += 1) {
    const a = points[index];
    const b = points[(index + 1) % points.length];
    const cross = a[0] * b[1] - b[0] * a[1];
    twiceArea += cross;
    momentTerm += (a[1] + b[1]) * cross;
  }
  const area = Math.abs(twiceArea / 2);
  const centroidRadius = Math.abs(momentTerm / (6 * (twiceArea / 2)));
  return { area, centroidRadius };
}

/** Solid of revolution volume of the body profile (Pappus's theorem). */
export function expectedSolidVolume(segments) {
  const { area, centroidRadius } = profileProperties(segments);
  return 2 * Math.PI * centroidRadius * area;
}

function line(from, to) {
  return { type: "line", from, to };
}

/**
 * Area of the surface produced by revolving the fillet arc (Pappus's theorem).
 * This is the acceptance fingerprint of the fillet radius: it changes measurably with r_f.
 */
export function filletTorusArea(fillet) {
  if (!fillet.enabled) return 0;
  const arcLength = fillet.r_f * fillet.alpha;
  const half = fillet.alpha / 2;
  const centroidOffset = fillet.r_f * Math.sin(half) / half;
  const v1 = [fillet.t1[0] - fillet.cx, fillet.t1[1] - fillet.cy];
  const v2 = [fillet.t2[0] - fillet.cx, fillet.t2[1] - fillet.cy];
  const norm = Math.hypot(v1[0] + v2[0], v1[1] + v2[1]);
  const bisector = [(v1[0] + v2[0]) / norm, (v1[1] + v2[1]) / norm];
  const centroidRadius = fillet.cy + centroidOffset * bisector[1];
  return 2 * Math.PI * centroidRadius * arcLength;
}

/**
 * Areas of the faces the profile produces when revolved, one per non-axis segment (Pappus's
 * theorem). This is general: it works for sharp, blunt, filleted or combined noses without a
 * separate formula per case.
 */
export function revolvedFaceAreas(segments) {
  // Adjacent collinear segments at the same radius merge into one revolved face (for example the
  // two pieces of the cylinder that meet at the shoulder station).
  const merged = [];
  for (const segment of segments) {
    const previous = merged[merged.length - 1];
    const mergeable = segment.type === "line" && previous?.type === "line"
      && previous.from[1] === previous.to[1] && segment.from[1] === segment.to[1]
      && previous.from[1] === segment.from[1];
    if (mergeable) merged[merged.length - 1] = { ...previous, to: segment.to };
    else merged.push({ ...segment });
  }
  const faces = [];
  for (const segment of merged) {
    if (segment.type === "line") {
      const { from, to } = segment;
      if (from[1] === 0 && to[1] === 0) continue;
      faces.push({ kind: segment.kind ?? "line", area: 2 * Math.PI * ((from[1] + to[1]) / 2) * Math.hypot(to[0] - from[0], to[1] - from[1]) });
    } else {
      const geometry = arcGeometry(segment);
      faces.push({ kind: segment.kind ?? "arc", area: 2 * Math.PI * geometry.centroidRadius * geometry.length });
    }
  }
  return faces;
}

/**
 * Closed revolving profile in the (x, r) half plane, from the nose tip around the body and back
 * along the axis. The nose can be sharp, blunted by a tangent sphere, filleted at the cone/cylinder
 * shoulder, or both; the profile is shifted so a blunt tip starts at x = 0 and the length stays L.
 *
 * `model_offset` then places the whole model further along +X, resolved from `model_placement` (see
 * `resolveModelPlacement`). Everything downstream - the analysis, the fins, the centre of mass - is
 * expressed in this same absolute frame, so the axis position is modelled rather than patched in
 * afterwards.
 */
export function bodyProfile(params) {
  const { L, L_N, D } = params;
  const location = params.fillet_location ?? "both";
  const R = bodyRadius(D);
  const wantsJunction = location === "cone_cylinder" || location === "both";
  const wantsTip = location === "nose_tip" || location === "both";
  const fillet = wantsJunction ? solveNoseFillet(params) : { ...solveNoseFillet({ ...params, k_nose: 0 }), requested: false };
  const tip = wantsTip ? solveNoseTipFillet(params) : { ...solveNoseTipFillet({ ...params, k_tip: 0 }), requested: false };
  const offset = resolveModelPlacement(params).offset;
  // Blunting shifts the nose toward the tip so the blunt face lands on the datum; the offset then
  // moves the whole model, base included.
  const blunting = tip.offset ? -tip.offset : 0;
  const shift = blunting + offset;
  const base = L + offset;
  const at = ([x, r]) => [x + shift, r];
  const segments = [];

  if (tip.enabled) {
    segments.push({
      type: "arc",
      kind: "tip",
      center: at([tip.d, 0]),
      from: at(tip.front),
      mid: at(tip.mid),
      to: at(tip.tangent),
      sweep: tip.sweep,
      sense: tip.sweep === "cw",
      radius: tip.r_t,
    });
  }
  const noseStart = tip.enabled ? at(tip.tangent) : [offset, 0];
  if (fillet.enabled) {
    segments.push({ ...line(noseStart, at(fillet.t1)), kind: "cone" });
    segments.push({
      type: "arc",
      kind: "shoulder",
      center: at([fillet.cx, fillet.cy]),
      from: at(fillet.t1),
      mid: at(fillet.mid),
      to: at(fillet.t2),
      sweep: fillet.sweep,
      sense: fillet.sweep === "cw",
      radius: fillet.r_f,
    });
    // The tangent point already sits on the cylinder line, so run straight to the base.
    segments.push({ ...line(at(fillet.t2), [base, R]), kind: "cylinder" });
  } else {
    segments.push({ ...line(noseStart, at([L_N, R])), kind: "cone" });
    segments.push({ ...line(at([L_N, R]), [base, R]), kind: "cylinder" });
  }
  segments.push({ ...line([base, R], [base, 0]), kind: "base" });
  segments.push({ ...line([base, 0], segments[0].from), kind: "axis" });
  return { R, segments, fillet, tip, shift, offset };
}

/** Trailing-edge-aligned trapezoid: root and tip trailing edges share the same axial station. */
export function tipLeadingEdgeOffset({ root_chord, tip_chord, le_mode }) {
  if (le_mode === "trailing_aligned") return root_chord - tip_chord;
  if (le_mode === "leading_aligned") return 0;
  if (le_mode === "symmetric") return (root_chord - tip_chord) / 2;
  throw new Error(`Unsupported le_mode '${le_mode}'.`);
}

/**
 * One fin planform in the (x, r) plane, four points in order:
 * buried root leading edge -> buried root trailing edge -> tip trailing edge -> tip leading edge.
 *
 * `le_x` is the **design station at the body surface**: the station where the leading edge meets the
 * body, which is what `L_W` / `L_F` name, and `root_chord` is the chord there.
 *
 * The fin is then sunk `root_overlap` into the body so the boolean union is clean. That embed must
 * extend the design trapezoid's edges inward - the trailing edge radially, the leading edge along
 * its own sweep - and never introduce an edge lying *on* the cylinder: a fin face coincident with
 * the body surface makes `Combine.Merge` fail to fuse (observed: 3 bodies instead of 1). Continuing
 * the leading edge inward from the buried root instead (the first version) drags the visible leading
 * edge aft by the sweep over the buried depth, which is what a ruler on the finished model reads as
 * 1723 mm / 3204 mm instead of 1720 mm / 3200 mm.
 */
export function finPlanform({ le_x, root_chord, tip_chord, span, root_radius, body_radius, le_mode = "trailing_aligned" }) {
  const te = le_x + root_chord;
  const sweep = tipLeadingEdgeOffset({ root_chord, tip_chord, le_mode });
  const tip_le = le_x + sweep;
  const tip_radius = body_radius + span;
  const buried_le = le_x - ((body_radius - root_radius) / span) * sweep;
  return {
    points: [
      [buried_le, root_radius],
      [te, root_radius],
      [te, tip_radius],
      [tip_le, tip_radius],
    ],
    tip_radius,
    root_radius,
    surface_radius: body_radius,
    le_at_surface: le_x,
    buried_le,
    te,
    tip_le,
    root_chord_at_surface: root_chord,
  };
}

/**
 * Planform corners of one fin instance in 3D, for every combination of cruciform phase and count.
 * `angle` is measured from +Y towards +Z; the plate thickness runs along the in-plane normal.
 */
function finInstanceCorners(fin, angleDeg) {
  const angle = (angleDeg * Math.PI) / 180;
  const radial = [Math.cos(angle), Math.sin(angle)];
  const normal = [-Math.sin(angle), Math.cos(angle)];
  const half = fin.thickness / 2;
  const corners = [];
  for (const [x, r] of fin.points) {
    for (const sign of [1, -1]) {
      corners.push([x, r * radial[0] + sign * half * normal[0], r * radial[1] + sign * half * normal[1]]);
    }
  }
  return corners;
}

/**
 * True bounding box of the finished model: the body plus every fin instance.
 * The simple `D + 2·max(B)` formula only holds for a four-fin "+" cruciform, so the expectation
 * is computed from the real fin corner positions instead.
 */
export function expectedExtents(geometry) {
  const { L, D, fins, cruciform_count, fin_phase_deg } = geometry;
  let maxY = bodyRadius(D);
  let maxZ = bodyRadius(D);
  for (const fin of fins) {
    for (let index = 0; index < cruciform_count; index += 1) {
      const angle = fin_phase_deg + (360 * index) / cruciform_count;
      for (const [, y, z] of finInstanceCorners(fin, angle)) {
        maxY = Math.max(maxY, Math.abs(y));
        maxZ = Math.max(maxZ, Math.abs(z));
      }
    }
  }
  return { axial: L, lateral: 2 * maxY, vertical: 2 * maxZ };
}

/**
 * Axial station of the model inside the enclosure.
 *
 * The enclosure is the frame and is built at `[-enclosure_front, L + enclosure_back]` whatever the
 * model does, so placing the model means choosing where its nose tip goes. Everything here is
 * one-dimensional and measured in the model's own frame, where the nose tip is 0 and the model's
 * midpoint therefore sits at L/2; the enclosure's midpoint is a fixed distance
 * `(enclosure_back - enclosure_front) / 2` further along.
 *
 * `model_placement` picks the rule that centres the block:
 *
 * - `ensemble` (default): the union of the missile and the BOI is centred. The BOI always encloses
 *   the missile (its margins are both non-negative), so the union *is* the BOI, and its midpoint is
 *   `(boi_back - boi_front) / 2` aft of the missile's - which is exactly what has to be subtracted.
 * - `missile`: only the missile body is centred, leaving the BOI's asymmetric margins (it reaches
 *   further aft than forward) hanging out at the tail.
 * - `offset`: no rule at all, so the station is whatever `model_offset` says.
 *
 * `model_offset` is then added on top in **every** mode: a signed nudge along +X, so a negative
 * value moves the model toward the nose ("a bit further forward") without giving up the centring.
 *
 * The resolution is pure arithmetic on the parameters and happens wherever the profile is built;
 * `buildModelRequest` reports the same call so the artifacts state where the model went. Everything
 * downstream - the profile, the fin stations, the centres of mass - works with the plain number, so
 * the analytic values and the measured ones stay in one absolute frame.
 */
export function resolveModelPlacement(params) {
  // Callers that do not speak this vocabulary (direct geometry use, unit fixtures) get the literal
  // manual offset instead of a silent centring they never asked for.
  const mode = params.model_placement ?? "offset";
  const manual = params.model_offset ?? 0;
  const mirror = ((params.enclosure_back ?? 0) - (params.enclosure_front ?? 0)) / 2;
  const boiSkew = ((params.boi_back ?? 0) - (params.boi_front ?? 0)) / 2;
  const rules = {
    ensemble: mirror - boiSkew,
    missile: mirror,
    offset: 0,
  };
  const known = Object.prototype.hasOwnProperty.call(rules, mode);
  // An unrecognised mode is rejected by validation; treating it as `offset` keeps the derivation
  // total so validation can report the parameter instead of throwing.
  const rule = known ? rules[mode] : 0;
  return { mode, known, rule, requested: manual, offset: rule + manual };
}

/**
 * Simulation regions around the finished model.
 *
 * The enclosure is the frame everything else is placed in, so its own station does not move with
 * `model_offset`; the BOI travels with the missile. That is exactly the request "put the missile and
 * the BOI in the middle of the enclosure": both model objects move together, while the far-field
 * boundary itself stays where it was.
 *
 * The enclosure is produced by the official Prepare > Enclosure command, so it is not a hand-built
 * solid: the command sizes its result as "bounding box + cushion". The axial cushions are plain
 * offsets from the box ends, while the radial cushion is added to the distance from the box centre
 * to its corner in the plane normal to the axis. The parameters stay absolute sizes; this function
 * converts them into the cushions the command expects.
 *
 * The BOI has no equivalent command, so it stays a solid of revolution built from a rectangular
 * profile and reuses the verified revolve workflow.
 */
export function deriveRegions(params, geometry) {
  const { L, D, cruciform_count, fin_phase_deg, fins } = geometry;
  const offset = geometry.model_offset ?? 0;
  const regions = {
    enclosure: {
      name: "enclosure",
      shape: params.enclosure_shape ?? "cylinder",
      radius: params.enclosure_radius,
      front: params.enclosure_front,
      back: params.enclosure_back,
      // The identification of the enclosure lives on the component the command creates, so the name
      // travels with the region rather than being written into the script.
      bodyName: params.enclosure_name || "Enclosure",
      shift: 0,
    },
    boi: {
      name: "boi",
      shape: "cylinder",
      radius: params.boi_radius,
      front: params.boi_front,
      back: params.boi_back,
      bodyName: params.boi_name || "BOI",
      shift: offset,
    },
  };
  for (const region of Object.values(regions)) {
    region.xFront = region.shift - region.front;
    region.xBack = region.shift + L + region.back;
    region.length = region.xBack - region.xFront;
    region.expectedExtents = { axial: region.length, lateral: 2 * region.radius, vertical: 2 * region.radius };
    region.expectedVolume = Math.PI * region.radius ** 2 * region.length;
  }
  // Radius the Enclosure command derives from the model's bounding box before the cushion is added.
  const envelopeRadius = Math.hypot(geometry.expectedExtents.lateral / 2, geometry.expectedExtents.vertical / 2);
  regions.enclosure.mode = "enclosure-command";
  regions.enclosure.envelopeRadius = envelopeRadius;
  // The model now sits `offset` further aft, so the cushions that reproduce the same absolute
  // enclosure grow at the tail end and shrink at the nose end by that amount.
  regions.enclosure.cushion = {
    axisLow: regions.enclosure.front + offset,
    axisHigh: regions.enclosure.back - offset,
    radius: regions.enclosure.radius - envelopeRadius,
  };
  regions.boi.mode = "revolve";
  const tipToTip = geometry.tip_to_tip;
  return { ...regions, tipToTip, modelLength: L, modelDiameter: D, cruciform_count, fin_phase_deg, fins };
}

/** Rectangular profile that revolves into a solid cylinder along the X axis. */
export function regionProfile(region) {
  return [
    { type: "line", kind: "front-cap", from: [region.xFront, 0], to: [region.xFront, region.radius] },
    { type: "line", kind: "cylinder", from: [region.xFront, region.radius], to: [region.xBack, region.radius] },
    { type: "line", kind: "back-cap", from: [region.xBack, region.radius], to: [region.xBack, 0] },
    { type: "line", kind: "axis", from: [region.xBack, 0], to: [region.xFront, 0] },
  ];
}

export function finPhaseDegrees(fin_phase) {
  if (fin_phase === "+") return 0;
  if (fin_phase === "x") return 45;
  throw new Error(`Unsupported fin_phase '${fin_phase}'.`);
}

/** Area centroid of a simple polygon in the (x, r) plane. */
export function polygonCentroid(points) {
  let twiceArea = 0;
  let cx = 0;
  let cy = 0;
  for (let index = 0; index < points.length; index += 1) {
    const [x0, y0] = points[index];
    const [x1, y1] = points[(index + 1) % points.length];
    const cross = x0 * y1 - x1 * y0;
    twiceArea += cross;
    cx += (x0 + x1) * cross;
    cy += (y0 + y1) * cross;
  }
  const area = twiceArea / 2;
  return [cx / (6 * area), cy / (6 * area)];
}

/**
 * Area and first moments of a simple polygon, as a signed triangle fan from the origin.
 * Signs cancel in every ratio these feed, so the winding direction does not matter.
 */
export function polygonMoments(points) {
  let area = 0;
  let momentX = 0;
  let momentY = 0;
  let momentXY = 0;
  for (let index = 0; index < points.length; index += 1) {
    const [x1, y1] = points[index];
    const [x2, y2] = points[(index + 1) % points.length];
    // Signed triangle (origin, P1, P2). For a source vertex at the origin the monomial integrals
    // over a triangle reduce to these closed forms.
    const signed = (x1 * y2 - x2 * y1) / 2;
    area += signed;
    momentX += (signed / 3) * (x1 + x2);
    momentY += (signed / 3) * (y1 + y2);
    momentXY += (signed / 12) * ((x1 + x2) * (y1 + y2) + x1 * y1 + x2 * y2);
  }
  return { area, momentX, momentY, momentXY };
}

/**
 * Axial centre of mass of the body of revolution.
 *
 * Each profile element sweeps `dV = 2π·r·dA`, so the axial first moment is `2π·∫∫ x·r dA` and the
 * volume is `2π·∫∫ r dA`; the ratio is what gets measured back out of SpaceClaim. This is the only
 * reading that pins the whole body - nose tip included - to an absolute axial station.
 */
export function solidOfRevolutionCenterOfMass(segments) {
  const { momentXY, momentY } = polygonMoments(profilePolygon(segments));
  return momentXY / momentY;
}

/**
 * Full derived geometry for one parameter set: body profile plus both fin planforms.
 * All lengths are metres.
 *
 * Everything here is in one absolute frame. The nose tip sits at the axial station
 * `resolveModelPlacement` derives from `model_placement` (the enclosure-centring rule), which
 * `bodyProfile` has already applied; the whole model is placed there rather than translated
 * afterwards, so `L_W` / `L_F` stay what they name: the root-leading-edge distance from the nose
 * tip, i.e. `leFromNoseTip`. Blunting shortens the nose cone and moves the shoulder toward the tip;
 * that is a consequence of holding both the total length and the tip station, not something to
 * compensate for.
 */
export function deriveGeometry(params) {
  const { L, D, root_overlap, le_mode, fin_phase } = params;
  const R = bodyRadius(D);
  const body = bodyProfile(params);
  const offset = body.offset;
  const root_radius = R - root_overlap;
  const front = finPlanform({ le_x: params.L_W + offset, root_chord: params.C_RW, tip_chord: params.C_TW, span: params.B_W, root_radius, body_radius: R, le_mode });
  const rear = finPlanform({ le_x: params.L_F + offset, root_chord: params.C_RF, tip_chord: params.C_TF, span: params.B_F, root_radius, body_radius: R, le_mode });
  const faces = revolvedFaceAreas(body.segments);
  // Station where the cone hands over to the cylinder, after the blunting shift.
  const cylinder = body.segments.find((segment) => segment.kind === "cylinder");
  const result = {
    units: "m",
    R,
    L,
    D,
    body,
    expectedAreas: { faces, total: faces.reduce((sum, face) => sum + face.area, 0) },
    expectedVolume: expectedSolidVolume(body.segments),
    bodyCenterOfMass: solidOfRevolutionCenterOfMass(body.segments),
    fins: [
      { name: "front", thickness: params.t_fin, ...front },
      { name: "rear", thickness: params.t_fin, ...rear },
    ],
    cruciform_count: params.cruciform_count,
    fin_phase_deg: finPhaseDegrees(fin_phase),
    tip_to_tip: D + 2 * Math.max(params.B_W, params.B_F),
    // Each fin instance meets the body in five faces: the two plate sides (trimmed by the cylinder),
    // the leading-edge face, the trailing-edge face and the tip chord. The buried root face is
    // absorbed by the union. The acceptance gate checks the named selection against this.
    expectedFinFaceCount: 5 * params.cruciform_count * 2,
    model_offset: offset,
    nose_tip_x: offset,
    tip_offset: body.tip.offset,
    nose_junction_x: cylinder ? cylinder.from[0] : params.L_N + offset,
  };
  for (const fin of result.fins) {
    // The seed fin is a prism along the plate normal, so its centre of mass sits at the planform
    // polygon's centroid. This is what proves the station the model was actually built at.
    fin.centroid = polygonCentroid(fin.points);
    // The station that is actually visible - and measurable - on the finished model: where the
    // leading edge meets the body surface. It is what `L_W` / `L_F` are defined to be.
    fin.leAtSurface = fin.le_at_surface;
    fin.leStation = fin.leAtSurface;
    fin.rootChordAtSurface = fin.root_chord_at_surface;
    fin.leFromNoseTip = fin.leAtSurface - result.nose_tip_x;
    fin.leFromNoseJunction = fin.leAtSurface - result.nose_junction_x;
  }
  result.expectedExtents = expectedExtents(result);
  return result;
}
