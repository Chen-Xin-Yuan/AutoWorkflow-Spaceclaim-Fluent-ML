/**
 * Analytic geometry for the missile body and fins.
 *
 * Everything here is pure arithmetic in metres, so the geometry can be unit-tested without
 * launching SpaceClaim. The generated SCDM script consumes these numbers and only issues API
 * calls; it contains no geometry constants of its own.
 *
 * 输入：调用方提供的参数/剖面；输出：内存中的派生结果，不读写文件。
 * 常用参数及默认值统一在 missile-schema.mjs 文件前部。
 * `fin_section=naca` 时，翼型方程与 DATCOM/scripts/naca_section.py 的对称 NACA 四位数一致；
 * 下面的参考厚度、前缘系数和后缘斜率是该方程的常数，不是可调设计参数。
 * AIRFOIL_SAMPLES_PER_SIDE 只决定 NACA 放样折线的弦向分辨率。
 * `fin_section=hexagon` 时，剖面是对称六边形，厚度和等厚段起止来自参数集。
 * 肩部和后缘是 1 mm 相切圆弧，前缘是 1.8 mm 相切圆弧，都不随弦长缩放；NACA 剖面不用这些半径。
 * 前舵再绕弦向轴转过六边形斜边夹角，使根截面不再平行于弹体切面；后舵保持径向。
 */

/** Chordwise samples on each side of the airfoil polyline. Leading-edge spacing is cosine-clustered. */
export const AIRFOIL_SAMPLES_PER_SIDE = 32;

/**
 * Absolute circular fillet on each corner of a hexagonal fin section.
 * `HEX_FILLET_RADIUS_M` is metres (1 mm) for the four shoulder arcs and the trailing-edge arc.
 * `HEX_FILLET_LE_RADIUS_M` is metres (1.8 mm) for the leading-edge arc only. Both are absolute
 * lengths, not chord fractions. The leading-edge extreme stays on the sharp leading-edge station.
 * `HEX_FILLET_RADIUS_TOLERANCE` is the relative acceptance band (±10%) when a radius is not clamped.
 * A shorter local chord, a smaller half-thickness, or a shorter flat can force a smaller tangent
 * radius; that clamp is reported on the section.
 * Each of the six corners is one circular arc, tangent to the neighbouring straight edges.
 * The sketch draws that arc with `SketchArc.CreateSweepArc`; it is not a chord or a polyline.
 * `HEX_FILLET_SEGMENT_COUNT` is the fixed closed profile: six arcs and six straight edges.
 * `HEX_FILLET_ANALYSIS_SAMPLES` is only the interior sample count used for centroid, volume and
 * bounding-box checks. Those samples are not sent to the sketch.
 * SpaceClaim drops a sweep arc that is too short or too flat: a 0.031 mm shoulder with 0.12 µm
 * sagitta created no curve, while a 0.088 mm arc with 0.96 µm sagitta was kept. The sketch is
 * enlarged until every arc meets the two floors below, then the sheet is scaled back to the true
 * radius and station. The floors are construction limits, not design parameters.
 */
export const HEX_FILLET_RADIUS_M = 0.001;
export const HEX_FILLET_LE_RADIUS_M = 0.0018;
export const HEX_FILLET_RADIUS_TOLERANCE = 0.1;
export const HEX_FILLET_SEGMENT_COUNT = 12;
export const HEX_FILLET_ANALYSIS_SAMPLES = 8;
export const HEX_FILLET_SKETCH_MIN_ARC_M = 0.0002;
export const HEX_FILLET_SKETCH_MIN_SAGITTA_M = 5e-6;
export const HEX_FILLET_SKETCH_SCALE_LIMIT = 1000;
/**
 * Rounds at the hexagonal fin only. They are metres, not chord fractions and not schema fields.
 * The fin tip keeps its sharp planform corners: the tip chord meets the leading and trailing edges
 * at the design planform vertices, with no planform arc and no arc cut there.
 * `HEX_ROOT_JUNCTION_RADIUS_M` is the ConstantRound requested where each fin meets the fuselage.
 * The aft junction must keep that radius. The canted forward junction tries it first, then the
 * values in `HEX_ROOT_JUNCTION_PROBE_RADII_M`, and stops at the largest radius SpaceClaim accepts.
 * `HEX_ROOT_JUNCTION_FORWARD_MIN_RADIUS_M` is the floor of that probe: 0.0015 m. Measured on the
 * default model in V241, the complete forward seam refuses 2 mm and accepts 1.5 mm, while the aft
 * seam accepts 3 mm; the smallest hexagon fin's 1 mm shoulder arcs set that limit. A smaller
 * forward round is not substituted, and 3 mm on the aft junction is never reduced. The ±10% band in
 * `HEX_FILLET_RADIUS_TOLERANCE` still gates an unreduced radius.
 * `HEX_ROOT_JUNCTION_SEAM_TOLERANCE_M` is how far from the exact cylinder radius an edge midpoint
 * may sit and still count as part of that seam. It is a selection tolerance, not a design
 * parameter.
 * The rounded set is the whole seam loop, every edge of the fin-to-body intersection including the
 * 0.08 mm shoulder-arc segments. A subset that drops those segments leaves free ends at tangent
 * transitions and the kernel refuses every radius tried above 0.2 mm, so the loop is never cut
 * down to its long edges.
 * The 1 mm section fillets along the span stay at `HEX_FILLET_RADIUS_M`. NACA fins do not use
 * these radii.
 */
/**
 * Where the hexagon's constant-thickness flat ends, re-exported from the schema so the CLI default
 * and the geometry agree by construction.
 *
 * This is the knob that decides whether the fin root can hold the 3 mm junction round. The flat end
 * sets the aft shoulder arc, and that arc is the shortest part of the fin-to-body seam: at the old
 * default of 0.80 the forward seam refuses 3 mm, the round is clamped to its 1.5 mm floor, and the
 * un-rounded band leaves 24 quad slivers (0.18–0.98 mm², aspect ratio ~119) on the merged body.
 * Those slivers cannot be deleted: `FixSpecific` reports success without removing them, a
 * one-at-a-time `DeleteFaces` adds a face and three edges per attempt, and `RepairAction.None`
 * removes them but leaves the body non-manifold (volume measurement throws).
 *
 * Measured on the default model in V241, sweeping only this knob:
 *   0.800 → 24 slivers, forward round clamped to 1.5 mm
 *   0.810 → 8 slivers
 *   0.816 → 8 slivers
 *   0.822 → 8 slivers   (the last failing value)
 *   0.8221 → 0 slivers, forward round accepted at the full 3 mm
 *   0.823 → 0 slivers   (the schema default, one step past the knee so it is not knife-edge)
 *   0.830 → 0 slivers
 * The accepted value moves the root flat end by 5.4 mm on a 0.234 m root chord (2.3%), leaves the
 * tip section untouched, and shifts the solid by +0.048% volume and −0.045% wetted area. It also
 * restores the forward junction round, which is a gain rather than a cost.
 *
 * Lowering this again is what reintroduces the slivers; the acceptance gate on the saved body is
 * what would catch it. `root_overlap`, `hex_flat_start`, `C_RW`, `C_TW`, `B_W` and
 * `thickness_percent` were swept over the same band and none of them clears the slivers.
 */
export const HEX_ROOT_JUNCTION_RADIUS_M = 0.003;
export const HEX_ROOT_JUNCTION_FORWARD_MIN_RADIUS_M = 0.0015;
export const HEX_ROOT_JUNCTION_PROBE_RADII_M = [0.003, 0.0025, 0.002, 0.0015];
export const HEX_ROOT_JUNCTION_SEAM_TOLERANCE_M = 0.001;
/**
 * Repair that runs after the merge and the junction rounds, before acceptance.
 *
 * Both steps use documented SpaceClaim repair commands; nothing here deletes a face the kernel
 * can still heal.
 *
 * `REPAIR_INEXACT_EDGES` runs `FixInexactEdges` over the merged missile. On the default hexagon
 * model it reports 28 problem areas and clears all of them, leaving face and edge counts untouched
 * and moving the volume by 2e-7 relative, so it is unconditional and always accepted.
 *
 * `REPAIR_SMALL_FACE_AREA_M2` is the face-area floor; a face below it is a repair candidate.
 * `REPAIR_SMALL_FACE_WIDTH_M` is an optional width floor for the same command. Keep it at 0: the
 * nose-tip fillet band and the shoulder fillet are narrow but legitimate, and a 1 mm width floor
 * merges them away, which leaves 4 body faces instead of 5 and breaks the analytic face
 * classification downstream.
 * The sliver faces at the fin root are 0.18–0.98 mm². They are repaired one face at a time
 * (`FixSmallFaces.FixSpecific`) rather than through the whole-body `FindAndFix`, which cascades:
 * on the default model a single bulk call merged 124 faces and moved the wetted area by 1.4%.
 * One-at-a-time never fully clears them, so the run reports how many were repaired instead of
 * claiming the model is clean. `REPAIR_SMALL_FACE_TARGET` is that share; measured on the default
 * model the per-face pass repairs 44 of 140 candidate faces.
 * `REPAIR_SMALL_FACE_MAX_RADIUS_M` keeps the pass below the fin tip. Healing a sliver out at the
 * tip pushes the tip outward, which is a real geometry change; the default 0.12 m covers the fin
 * root (0.0905 m on the default model) and stops well short of the tip (0.32 m).
 * `REPAIR_MAX_VOLUME_CHANGE` bounds how far the repair may move the volume; a larger change means
 * the repair ate real geometry, so the run fails instead of shipping a deformed model. The default
 * hexagon model moves by 2.5e-3 relative through the per-face pass, so the budget is 5e-3.
 * `REPAIR_MAX_SURFACE_CHANGE` does the same for the wetted area, at the same 5e-3.
 */
export const REPAIR_INEXACT_EDGES = true;
export const REPAIR_DEGENERATE_AREA_M2 = 1e-12;
export const REPAIR_SMALL_FACE_AREA_M2 = 1e-6;
export const REPAIR_SMALL_FACE_WIDTH_M = 0;
export const REPAIR_SMALL_FACE_MAX_RADIUS_M = 0.12;
export const REPAIR_MAX_VOLUME_CHANGE = 5e-3;
export const REPAIR_MAX_SURFACE_CHANGE = 5e-3;

const NACA_REFERENCE_THICKNESS = 0.20;
const NACA_REFERENCE_HALF_THICKNESS = 0.10;
const NACA_REFERENCE_TE_HALF = 0.002;
const NACA_REFERENCE_A0 = 0.2969;
const NACA_TE_SLOPE_BY_TENTHS = { 2: 0.200, 3: 0.234, 4: 0.315, 5: 0.465, 6: 0.700 };

function solveLinear(matrix, rhs) {
  const size = rhs.length;
  const rows = matrix.map((row, index) => [...row, rhs[index]]);
  for (let col = 0; col < size; col += 1) {
    let pivot = col;
    for (let row = col + 1; row < size; row += 1) {
      if (Math.abs(rows[row][col]) > Math.abs(rows[pivot][col])) pivot = row;
    }
    if (Math.abs(rows[pivot][col]) < 1e-14) {
      throw new Error("NACA 厚度方程无解，请调整前缘半径指数或最大厚度位置。");
    }
    [rows[col], rows[pivot]] = [rows[pivot], rows[col]];
    const scale = rows[col][col];
    for (let colJ = col; colJ < size + 1; colJ += 1) rows[col][colJ] /= scale;
    for (let row = 0; row < size; row += 1) {
      if (row === col) continue;
      const factor = rows[row][col];
      for (let colJ = col; colJ < size + 1; colJ += 1) rows[row][colJ] -= factor * rows[col][colJ];
    }
  }
  return rows.map((row) => row[size]);
}

/** Coefficients of the 20% reference thickness form. The real section is scaled by thickness/0.20. */
export function nacaReferenceCoefficients(leRadiusIndex, maxThicknessTenths) {
  const xm = maxThicknessTenths / 10;
  const a0 = NACA_REFERENCE_A0 * (leRadiusIndex / 6);
  const d0 = NACA_REFERENCE_TE_HALF;
  const d1 = NACA_TE_SLOPE_BY_TENTHS[maxThicknessTenths];
  if (d1 === undefined) throw new Error(`max_thickness_tenths '${maxThicknessTenths}' 没有对应的后缘斜率。`);
  const z = 1 - xm;
  const [d2, d3] = solveLinear(
    [[z * z, z * z * z], [2 * z, 3 * z * z]],
    [NACA_REFERENCE_HALF_THICKNESS - d0 - d1 * z, -d1],
  );
  const curvature = 2 * d2 + 6 * d3 * z;
  const root = Math.sqrt(xm);
  const [a1, a2, a3] = solveLinear(
    [[xm, xm * xm, xm ** 3], [1, 2 * xm, 3 * xm * xm], [0, 2, 6 * xm]],
    [
      NACA_REFERENCE_HALF_THICKNESS - a0 * root,
      -0.5 * a0 / root,
      curvature + 0.25 * a0 * xm ** -1.5,
    ],
  );
  return { a0, a1, a2, a3, d0, d1, d2, d3, xm };
}

/** Half-thickness y/c. x is 0 at the leading edge and 1 at the trailing edge. */
export function nacaHalfThicknessRatio(x, coefficients, thicknessRatio) {
  let y;
  if (x <= coefficients.xm) {
    const xf = Math.max(x, 0);
    y = coefficients.a0 * Math.sqrt(xf) + coefficients.a1 * xf + coefficients.a2 * xf ** 2 + coefficients.a3 * xf ** 3;
  } else {
    const z = 1 - x;
    y = coefficients.d0 + coefficients.d1 * z + coefficients.d2 * z ** 2 + coefficients.d3 * z ** 3;
  }
  return Math.max(y, 0) * (thicknessRatio / NACA_REFERENCE_THICKNESS);
}

function polygonAreaCentroid(points) {
  let crossSum = 0;
  let momentX = 0;
  let momentY = 0;
  for (let index = 0; index < points.length; index += 1) {
    const [x1, y1] = points[index];
    const [x2, y2] = points[(index + 1) % points.length];
    const cross = x1 * y2 - x2 * y1;
    crossSum += cross;
    momentX += (x1 + x2) * cross;
    momentY += (y1 + y2) * cross;
  }
  return { area: crossSum / 2, cx: momentX / (3 * crossSum), cy: momentY / (3 * crossSum) };
}

/**
 * Closed nondimensional airfoil, LE → upper surface → TE → lower surface → LE.
 * Coordinates are (x/c, y/c). The loop is not closed by repeating the first point.
 */
export function buildNacaAirfoil({ thickness_percent, le_radius_index, max_thickness_tenths }, samples = AIRFOIL_SAMPLES_PER_SIDE) {
  const thicknessRatio = thickness_percent / 100;
  const coefficients = nacaReferenceCoefficients(le_radius_index, max_thickness_tenths);
  const loop = [];
  for (let index = 0; index <= samples; index += 1) {
    const xi = 0.5 * (1 - Math.cos((Math.PI * index) / samples));
    loop.push([xi, nacaHalfThicknessRatio(xi, coefficients, thicknessRatio)]);
  }
  for (let index = samples; index >= 1; index -= 1) {
    const xi = 0.5 * (1 - Math.cos((Math.PI * index) / samples));
    loop.push([xi, -nacaHalfThicknessRatio(xi, coefficients, thicknessRatio)]);
  }
  return { coefficients, thicknessRatio, loop, properties: polygonAreaCentroid(loop) };
}

function normalize2(vector) {
  const length = Math.hypot(vector[0], vector[1]);
  if (!(length > 0)) return null;
  return [vector[0] / length, vector[1] / length];
}

/** Interior is to the right of a clockwise hexagonal boundary. */
function inwardNormal(direction) {
  return [direction[1], -direction[0]];
}

function lineIntersection(origin, direction, otherOrigin, otherDirection) {
  const det = direction[0] * otherDirection[1] - direction[1] * otherDirection[0];
  if (Math.abs(det) < 1e-14) return null;
  const dx = otherOrigin[0] - origin[0];
  const dy = otherOrigin[1] - origin[1];
  const scale = (dx * otherDirection[1] - dy * otherDirection[0]) / det;
  return [origin[0] + scale * direction[0], origin[1] + scale * direction[1]];
}

/** Signed distance, positive on the inward (right) side of a unit direction. */
function inwardDistance(point, origin, direction) {
  const dx = point[0] - origin[0];
  const dy = point[1] - origin[1];
  return direction[1] * dx - direction[0] * dy;
}

function segmentParameter(point, start, end) {
  const dx = end[0] - start[0];
  const dy = end[1] - start[1];
  const length2 = dx * dx + dy * dy;
  if (!(length2 > 0)) return null;
  return ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / length2;
}

/**
 * Centre of the tangent fillet on a clockwise corner. The centre is inset by `radius` from both edges.
 * Returns null when the edges are parallel or the tangent feet fall outside the finite edges.
 */
function clockwiseFillet(prev, vertex, next, radius) {
  const incoming = normalize2([vertex[0] - prev[0], vertex[1] - prev[1]]);
  const outgoing = normalize2([next[0] - vertex[0], next[1] - vertex[1]]);
  if (!incoming || !outgoing) return null;
  const normalIn = inwardNormal(incoming);
  const normalOut = inwardNormal(outgoing);
  const center = lineIntersection(
    [prev[0] + radius * normalIn[0], prev[1] + radius * normalIn[1]],
    incoming,
    [vertex[0] + radius * normalOut[0], vertex[1] + radius * normalOut[1]],
    outgoing,
  );
  if (!center) return null;
  const footIn = [center[0] - radius * normalIn[0], center[1] - radius * normalIn[1]];
  const footOut = [center[0] - radius * normalOut[0], center[1] - radius * normalOut[1]];
  const onIncoming = segmentParameter(footIn, prev, vertex);
  const onOutgoing = segmentParameter(footOut, vertex, next);
  if (onIncoming === null || onOutgoing === null) return null;
  if (onIncoming <= 1e-8 || onIncoming >= 1 - 1e-8 || onOutgoing <= 1e-8 || onOutgoing >= 1 - 1e-8) return null;
  if (Math.abs(inwardDistance(center, prev, incoming) - radius) > 1e-8) return null;
  if (Math.abs(inwardDistance(center, vertex, outgoing) - radius) > 1e-8) return null;
  return { center, footIn, footOut };
}

/**
 * External tangent on the upper side of two circles. Equal radii keep the previous normal
 * (perpendicular to the line of centres). Unequal radii rotate that normal by asin((r1-r2)/d).
 */
function upperExternalTangent(first, second, firstRadius, secondRadius = firstRadius) {
  const dx = second[0] - first[0];
  const dy = second[1] - first[1];
  const length = Math.hypot(dx, dy);
  if (!(length > Math.abs(firstRadius - secondRadius))) return null;
  const phi = Math.atan2(dy, dx);
  const alpha = Math.asin((firstRadius - secondRadius) / length);
  const candidates = [phi + Math.PI / 2 - alpha, phi - Math.PI / 2 + alpha];
  let best = null;
  for (const angle of candidates) {
    const normal = [Math.cos(angle), Math.sin(angle)];
    if (!(normal[1] > 0)) continue;
    const start = [first[0] + firstRadius * normal[0], first[1] + firstRadius * normal[1]];
    const end = [second[0] + secondRadius * normal[0], second[1] + secondRadius * normal[1]];
    if (!best || start[1] + end[1] > best.first[1] + best.second[1]) {
      best = { first: start, second: end, normal };
    }
  }
  return best;
}

/** Sharp bevel angle of the hexagon, from the chord plane. Depends only on thickness and flat start. */
export function hexagonBevelAngle(thicknessPercent, flatStart) {
  const slope = (thicknessPercent / 100) / (2 * flatStart);
  if (!(slope > 0)) throw new Error("六边形斜边夹角需要正的厚度和等厚段起点。");
  return Math.atan(slope);
}

function distance2D(a, b) {
  return Math.hypot(a[0] - b[0], a[1] - b[1]);
}

/** Signed minor sweep from `from` to `to`, positive counterclockwise, in (-π, π]. */
function signedMinorAngle(center, from, to) {
  const v1 = [from[0] - center[0], from[1] - center[1]];
  const v2 = [to[0] - center[0], to[1] - center[1]];
  return Math.atan2(v1[0] * v2[1] - v1[1] * v2[0], v1[0] * v2[0] + v1[1] * v2[1]);
}

function onMinorArc(center, from, to, via, radius) {
  if (Math.abs(distance2D(via, center) - radius) > 1e-8) return false;
  const total = signedMinorAngle(center, from, to);
  const part = signedMinorAngle(center, from, via);
  if (!(Math.abs(total) > 1e-8 && Math.abs(total) < Math.PI - 1e-8)) return false;
  if (total > 0) return part > -1e-8 && part < total + 1e-8;
  return part < 1e-8 && part > total - 1e-8;
}

/**
 * One circular arc, the minor sweep from `from` to `to`.
 * `sense` matches the body profile: true asks SketchArc.CreateSweepArc for the clockwise arc.
 * On the fin plane that flag is inferred from the body sketch until a hexagonal run confirms it.
 */
function arcSegment(center, from, to, radius, via) {
  const delta = signedMinorAngle(center, from, to);
  if (!(Math.abs(delta) > 1e-5 && Math.abs(delta) < Math.PI - 1e-8)) return null;
  const start = Math.atan2(from[1] - center[1], from[0] - center[0]);
  const midAngle = start + delta / 2;
  const midpoint = [center[0] + radius * Math.cos(midAngle), center[1] + radius * Math.sin(midAngle)];
  const crown = via ?? midpoint;
  if (!onMinorArc(center, from, to, crown, radius)) return null;
  const sweep = delta < 0 ? "cw" : "ccw";
  return {
    type: "arc",
    center,
    from,
    to,
    via: crown,
    radius,
    sweep,
    sense: sweep === "cw",
    delta,
  };
}

function lineSegment(from, to) {
  if (!(distance2D(from, to) > 1e-9)) return null;
  return { type: "line", from, to };
}

function arcSagitta(arc) {
  return arc.radius * (1 - Math.cos(Math.abs(arc.delta) / 2));
}

/**
 * Uniform sketch enlargement that keeps every fillet arc above the length and sagitta
 * SpaceClaim will actually create. The sheet is scaled back afterwards, so the radius stays true.
 */
export function hexagonSketchScale(segments) {
  let scale = 1;
  for (const segment of segments) {
    if (segment?.type !== "arc") continue;
    const length = Math.abs(segment.delta) * segment.radius;
    const sagitta = arcSagitta(segment);
    if (length > 0) scale = Math.max(scale, HEX_FILLET_SKETCH_MIN_ARC_M / length);
    if (sagitta > 0) scale = Math.max(scale, HEX_FILLET_SKETCH_MIN_SAGITTA_M / sagitta);
  }
  if (!(scale >= 1) || scale > HEX_FILLET_SKETCH_SCALE_LIMIT) {
    throw new Error(`六边形圆角草图比例 ${scale} 超出 1 到 ${HEX_FILLET_SKETCH_SCALE_LIMIT}，圆弧无法按真实半径保留。`);
  }
  return scale;
}

function chainCloses(segments) {
  if (segments.length < 3) return false;
  for (let index = 0; index < segments.length; index += 1) {
    const next = segments[(index + 1) % segments.length];
    if (distance2D(segments[index].to, next.from) > 1e-8) return false;
  }
  return true;
}

/** Analysis polygon. Each arc crown is inserted in sweep order so the extremes stay on the boundary. */
function analysisLoop(segments) {
  const points = [];
  for (const segment of segments) {
    points.push(segment.from);
    if (segment.type !== "arc") continue;
    const start = Math.atan2(segment.from[1] - segment.center[1], segment.from[0] - segment.center[0]);
    const crownTurn = signedMinorAngle(segment.center, segment.from, segment.via);
    const crownFraction = crownTurn / segment.delta;
    let crownUsed = false;
    for (let step = 1; step <= HEX_FILLET_ANALYSIS_SAMPLES; step += 1) {
      const fraction = step / (HEX_FILLET_ANALYSIS_SAMPLES + 1);
      if (!crownUsed && fraction >= crownFraction) {
        points.push(segment.via);
        crownUsed = true;
      }
      const angle = start + segment.delta * fraction;
      points.push([
        segment.center[0] + segment.radius * Math.cos(angle),
        segment.center[1] + segment.radius * Math.sin(angle),
      ]);
    }
    if (!crownUsed) points.push(segment.via);
  }
  return points;
}

/**
 * One filleted hexagonal section in local metres, leading edge at x = 0.
 * Returns null when `radius` cannot sit as a tangent arc on this chord.
 *
 * Leading and trailing circles keep their extreme point on the sharp station (x = 0 and x = chord)
 * and meet the straight flanks in a tangent. Shoulder circles are the ordinary tangent fillets of
 * the sharp corner, so they stay tangent to the constant-thickness flat and the thickness does not grow.
 * The flank between a nose circle and a shoulder circle is their common external tangent.
 */
function tryFilletedHexagon({ chord, half, flatStart, flatEnd, radius, leadingRadius = radius }) {
  if (!(radius > 0) || !(leadingRadius > 0)) return null;
  if (!(radius < half * (1 - 1e-9)) || !(leadingRadius < half * (1 - 1e-9))) return null;
  if (!(2 * radius < chord * (1 - 1e-9)) || !(2 * leadingRadius < chord * (1 - 1e-9))) return null;
  const x1 = flatStart * chord;
  const x2 = flatEnd * chord;
  const sharp = [[0, 0], [x1, half], [x2, half], [chord, 0]];
  const forward = clockwiseFillet(sharp[0], sharp[1], sharp[2], radius);
  const aft = clockwiseFillet(sharp[1], sharp[2], sharp[3], radius);
  if (!forward || !aft) return null;
  if (Math.abs(forward.center[1] - (half - radius)) > 1e-8 || Math.abs(aft.center[1] - (half - radius)) > 1e-8) return null;
  const forwardCenter = [forward.center[0], half - radius];
  const aftCenter = [aft.center[0], half - radius];
  const forwardFlat = [forwardCenter[0], half];
  const aftFlat = [aftCenter[0], half];
  if (!(forwardFlat[0] < aftFlat[0] - 1e-8)) return null;
  const leadingCenter = [leadingRadius, 0];
  const trailingCenter = [chord - radius, 0];
  const forwardTangent = upperExternalTangent(leadingCenter, forwardCenter, leadingRadius, radius);
  const aftTangent = upperExternalTangent(aftCenter, trailingCenter, radius, radius);
  if (!forwardTangent || !aftTangent) return null;
  const leadingAngle = Math.atan2(forwardTangent.first[1] - leadingCenter[1], forwardTangent.first[0] - leadingCenter[0]);
  const forwardWedgeAngle = Math.atan2(forwardTangent.second[1] - forwardCenter[1], forwardTangent.second[0] - forwardCenter[0]);
  const aftWedgeAngle = Math.atan2(aftTangent.first[1] - aftCenter[1], aftTangent.first[0] - aftCenter[0]);
  const trailingAngle = Math.atan2(aftTangent.second[1] - trailingCenter[1], aftTangent.second[0] - trailingCenter[0]);
  const rightAngle = Math.PI / 2;
  if (!(leadingAngle > 1e-6 && leadingAngle < Math.PI - 1e-6)) return null;
  if (!(forwardWedgeAngle > rightAngle + 1e-6 && forwardWedgeAngle < Math.PI - 1e-6)) return null;
  if (!(aftWedgeAngle > 1e-6 && aftWedgeAngle < rightAngle - 1e-6)) return null;
  if (!(trailingAngle > 1e-6 && trailingAngle < Math.PI - 1e-6)) return null;
  if (!(Math.PI - leadingAngle > 1e-5 && forwardWedgeAngle - rightAngle > 1e-5)) return null;
  if (!(rightAngle - aftWedgeAngle > 1e-5 && trailingAngle > 1e-5)) return null;
  const mirror = ([x, y]) => [x, -y];
  const leadingExtreme = [0, 0];
  const trailingExtreme = [chord, 0];
  const pushJunction = (list, center, at, toward) => list.push({ center, at, toward });
  const junctions = [];
  pushJunction(junctions, leadingCenter, forwardTangent.first, forwardTangent.second);
  pushJunction(junctions, forwardCenter, forwardTangent.second, forwardTangent.first);
  pushJunction(junctions, forwardCenter, forwardFlat, aftFlat);
  pushJunction(junctions, aftCenter, aftFlat, forwardFlat);
  pushJunction(junctions, aftCenter, aftTangent.first, aftTangent.second);
  pushJunction(junctions, trailingCenter, aftTangent.second, aftTangent.first);
  const upperJunctions = junctions.map((junction) => ({ ...junction }));
  for (const junction of upperJunctions) {
    pushJunction(junctions, mirror(junction.center), mirror(junction.at), mirror(junction.toward));
  }
  const tangent = (junction) => {
    const radial = [junction.at[0] - junction.center[0], junction.at[1] - junction.center[1]];
    const edge = [junction.toward[0] - junction.at[0], junction.toward[1] - junction.at[1]];
    const scale = Math.hypot(radial[0], radial[1]) * Math.hypot(edge[0], edge[1]);
    return scale > 0 && Math.abs(radial[0] * edge[0] + radial[1] * edge[1]) / scale < 1e-8;
  };
  if (!junctions.every(tangent)) return null;
  const upperForward = arcSegment(forwardCenter, forwardTangent.second, forwardFlat, radius);
  const upperAft = arcSegment(aftCenter, aftFlat, aftTangent.first, radius);
  const trailing = arcSegment(trailingCenter, aftTangent.second, mirror(aftTangent.second), radius, trailingExtreme);
  const lowerAft = arcSegment(mirror(aftCenter), mirror(aftTangent.first), mirror(aftFlat), radius);
  const lowerForward = arcSegment(mirror(forwardCenter), mirror(forwardFlat), mirror(forwardTangent.second), radius);
  const leading = arcSegment(leadingCenter, mirror(forwardTangent.first), forwardTangent.first, leadingRadius, leadingExtreme);
  const segments = [
    lineSegment(forwardTangent.first, forwardTangent.second),
    upperForward && { ...upperForward, name: "upper-forward" },
    lineSegment(forwardFlat, aftFlat),
    upperAft && { ...upperAft, name: "upper-aft" },
    lineSegment(aftTangent.first, aftTangent.second),
    trailing && { ...trailing, name: "trailing" },
    lineSegment(mirror(aftTangent.second), mirror(aftTangent.first)),
    lowerAft && { ...lowerAft, name: "lower-aft" },
    lineSegment(mirror(aftFlat), mirror(forwardFlat)),
    lowerForward && { ...lowerForward, name: "lower-forward" },
    lineSegment(mirror(forwardTangent.second), mirror(forwardTangent.first)),
    leading && { ...leading, name: "leading" },
  ];
  if (segments.some((segment) => !segment) || segments.length !== HEX_FILLET_SEGMENT_COUNT || !chainCloses(segments)) return null;
  const loop = analysisLoop(segments);
  if (loop.some((point) => point[1] < -half - 1e-9 || point[1] > half + 1e-9)) return null;
  return {
    segments,
    loop,
    leading: { ...leading, extreme: leadingExtreme },
    trailing: { ...trailing, extreme: trailingExtreme },
    shoulders: [upperForward, upperAft, lowerAft, lowerForward].map((arc, index) => ({
      ...arc,
      name: ["upper-forward", "upper-aft", "lower-aft", "lower-forward"][index],
    })),
    junctions,
  };
}

/**
 * Symmetric hexagonal section with circular corner fillets. Local coordinates are metres:
 * x = 0 at the design leading edge, x = chord at the design trailing edge, y = thickness.
 * The profile is six circular arcs and six straight edges. `loop` is an analysis sample of that
 * profile, including each arc crown; the sketch consumes `segments` instead.
 *
 * `fillet_radius` is the shoulder and trailing-edge radius and defaults to `HEX_FILLET_RADIUS_M`.
 * `leading_radius` is the leading-edge radius and defaults to `HEX_FILLET_LE_RADIUS_M`.
 * When a circle cannot meet the flat and the wedges in a tangent, that radius is reduced to the
 * largest value that still can. The leading-edge extreme stays at x = 0.
 */
function sketchableFillet(built) {
  if (!built) return false;
  try {
    hexagonSketchScale(built.segments);
    return true;
  } catch {
    return false;
  }
}

function largestFit(requested, buildAt) {
  const direct = buildAt(requested);
  if (sketchableFillet(direct)) return { built: direct, used: requested, clamped: false };
  let low = 0;
  let high = requested;
  let best = null;
  let used = 0;
  for (let step = 0; step < 60; step += 1) {
    const mid = (low + high) / 2;
    const candidate = buildAt(mid);
    if (sketchableFillet(candidate)) {
      best = candidate;
      used = mid;
      low = mid;
    } else {
      high = mid;
    }
  }
  return { built: best, used, clamped: true };
}

export function buildHexagonAirfoil({
  thickness_percent,
  hex_flat_start,
  hex_flat_end,
  chord,
  fillet_radius = HEX_FILLET_RADIUS_M,
  leading_radius = HEX_FILLET_LE_RADIUS_M,
}) {
  const thicknessRatio = thickness_percent / 100;
  const half = thicknessRatio * chord / 2;
  const flatStart = hex_flat_start;
  const flatEnd = hex_flat_end;
  if (!(thicknessRatio > 0) || !(flatStart > 0 && flatEnd > flatStart && flatEnd < 1) || !(chord > 0)) {
    throw new Error("六边形翼面需要正厚度、正弦长，以及 0 < hex_flat_start < hex_flat_end < 1。");
  }
  if (!(fillet_radius > 0) || !Number.isFinite(fillet_radius) || !(leading_radius > 0) || !Number.isFinite(leading_radius)) {
    throw new Error("六边形圆角半径必须是正的有限长度（米）。");
  }
  const shoulderFit = largestFit(fillet_radius, (radius) => tryFilletedHexagon({
    chord, half, flatStart, flatEnd, radius, leadingRadius: radius,
  }));
  if (!shoulderFit.built) {
    throw new Error(`六边形圆角无法在弦长 ${chord} m、半厚度 ${half} m 上形成有效相切圆弧。`);
  }
  const leadingFit = largestFit(leading_radius, (leadingRadius) => tryFilletedHexagon({
    chord, half, flatStart, flatEnd, radius: shoulderFit.used, leadingRadius,
  }));
  const built = leadingFit.built ?? shoulderFit.built;
  const leadingUsed = leadingFit.built ? leadingFit.used : shoulderFit.used;
  const leadingClamped = !leadingFit.built || leadingFit.clamped;
  const clamped = shoulderFit.clamped || leadingClamped;
  return {
    thicknessRatio,
    chord,
    halfThickness: half,
    flatStart,
    flatEnd,
    segments: built.segments,
    loop: built.loop,
    properties: polygonAreaCentroid(built.loop),
    fillet: {
      requestedRadius: fillet_radius,
      radius: shoulderFit.used,
      leadingRequestedRadius: leading_radius,
      leadingRadius: leadingUsed,
      leadingClamped,
      clamped,
      clampNote: clamped ? "当地弦长、厚度或等厚段放不下请求的相切圆角，半径已收到仍能相切的最大值。" : "",
      tolerance: HEX_FILLET_RADIUS_TOLERANCE,
      unit: "m",
      leading: { ...built.leading, radius: leadingUsed },
      trailing: { ...built.trailing, radius: shoulderFit.used },
      shoulders: built.shoulders.map((shoulder) => ({ ...shoulder, radius: shoulderFit.used })),
      junctions: built.junctions,
    },
  };
}

/**
 * NACA polyline, or the shared hexagonal fillet description. The hexagonal loop itself depends on
 * the local chord, so it is built per station in `finAirfoilSections`. Missing section keeps NACA.
 */
export function buildFinAirfoil(params, samples = AIRFOIL_SAMPLES_PER_SIDE) {
  const kind = params.fin_section ?? "naca";
  if (kind === "hexagon") {
    const thicknessRatio = params.thickness_percent / 100;
    const flatStart = params.hex_flat_start;
    const flatEnd = params.hex_flat_end;
    if (!(thicknessRatio > 0) || !(flatStart > 0 && flatEnd > flatStart && flatEnd < 1)) {
      throw new Error("六边形翼面需要正厚度，以及 0 < hex_flat_start < hex_flat_end < 1。");
    }
    return {
      kind,
      thickness_percent: params.thickness_percent,
      thicknessRatio,
      flatStart,
      flatEnd,
      filletRadiusRequested: HEX_FILLET_RADIUS_M,
      loopPoints: HEX_FILLET_SEGMENT_COUNT,
    };
  }
  if (kind === "naca") return { kind, ...buildNacaAirfoil(params, samples) };
  throw new Error(`Unsupported fin_section '${kind}'.`);
}

/**
 * Volume centroid of a straight loft between two similar sections.
 * Chord and leading edge vary linearly with radius; section area scales with chord squared.
 */
export function taperedLoftCentroid({ le0, c0, r0, le1, c1, r1, areaRatio, xiBar }) {
  const span = r1 - r0;
  const dc = c1 - c0;
  const dle = le1 - le0;
  const c2 = c0 * c0 + c0 * dc + (dc * dc) / 3;
  const c2t = (c0 * c0) / 2 + (2 * c0 * dc) / 3 + (dc * dc) / 4;
  const c3 = c0 ** 3 + 1.5 * c0 * c0 * dc + c0 * dc * dc + (dc ** 3) / 4;
  const volume = areaRatio * span * c2;
  return {
    volume,
    centroid: [
      (le0 * c2 + dle * c2t + xiBar * c3) / c2,
      (r0 * c2 + span * c2t) / c2,
      0,
    ],
  };
}

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
 * Airfoil polyline of one fin instance. `angle` is measured from +Y towards +Z.
 * Section offset is the signed thickness along the in-plane normal; the planform itself stays in the radial plane.
 */
function rotateAboutAxis(y, z, angle) {
  return [y * Math.cos(angle) - z * Math.sin(angle), y * Math.sin(angle) + z * Math.cos(angle)];
}

function finInstancePoints(fin, angleDeg) {
  const angle = (angleDeg * Math.PI) / 180;
  const frame = fin.sectionFrame;
  const radial = frame
    ? rotateAboutAxis(frame.normal[1], frame.normal[2], angle)
    : [Math.cos(angle), Math.sin(angle)];
  const normal = frame
    ? rotateAboutAxis(frame.thickness[1], frame.thickness[2], angle)
    : [-Math.sin(angle), Math.cos(angle)];
  const points = [];
  for (const section of fin.sections) {
    for (const [x, offset] of section.points) {
      points.push([
        x,
        section.radius * radial[0] + offset * normal[0],
        section.radius * radial[1] + offset * normal[1],
      ]);
    }
  }
  return points;
}

/**
 * Volume and centroid of a ruled loft between two parallel section polygons.
 * Vertices correspond by index and move linearly from `section0` to `section1`.
 * The centroid is [axial, radius, 0]; a nonzero thickness centroid means the section is not symmetric.
 */
export function ruledLoftSolid(section0, section1) {
  const start = section0.points;
  const end = section1.points;
  if (start.length !== end.length || start.length < 3) {
    throw new Error("放样剖面顶点数量不一致。");
  }
  let cross0 = 0;
  let cross1 = 0;
  let cross2 = 0;
  let axial0 = 0;
  let axial1 = 0;
  let axial2 = 0;
  let axial3 = 0;
  let thick0 = 0;
  let thick1 = 0;
  let thick2 = 0;
  let thick3 = 0;
  for (let index = 0; index < start.length; index += 1) {
    const next = (index + 1) % start.length;
    const x0 = start[index][0];
    const y0 = start[index][1];
    const u0 = start[next][0];
    const v0 = start[next][1];
    const x1 = end[index][0] - x0;
    const y1 = end[index][1] - y0;
    const u1 = end[next][0] - u0;
    const v1 = end[next][1] - v0;
    const k0 = x0 * v0 - u0 * y0;
    const k1 = x0 * v1 + x1 * v0 - u0 * y1 - u1 * y0;
    const k2 = x1 * v1 - u1 * y1;
    cross0 += k0;
    cross1 += k1;
    cross2 += k2;
    const px0 = x0 + u0;
    const px1 = x1 + u1;
    const py0 = y0 + v0;
    const py1 = y1 + v1;
    axial0 += px0 * k0;
    axial1 += px0 * k1 + px1 * k0;
    axial2 += px0 * k2 + px1 * k1;
    axial3 += px1 * k2;
    thick0 += py0 * k0;
    thick1 += py0 * k1 + py1 * k0;
    thick2 += py0 * k2 + py1 * k1;
    thick3 += py1 * k2;
  }
  const integratedCross = cross0 + cross1 / 2 + cross2 / 3;
  if (!(Math.abs(integratedCross) > 0)) throw new Error("放样剖面面积为 0。");
  const integratedAxial = axial0 + axial1 / 2 + axial2 / 3 + axial3 / 4;
  const integratedThick = thick0 + thick1 / 2 + thick2 / 3 + thick3 / 4;
  const span = section1.radius - section0.radius;
  const radial0 = section0.radius * cross0;
  const radial1 = section0.radius * cross1 + span * cross0;
  const radial2 = section0.radius * cross2 + span * cross1;
  const radial3 = span * cross2;
  const integratedRadial = radial0 + radial1 / 2 + radial2 / 3 + radial3 / 4;
  return {
    volume: Math.abs(integratedCross / 2) * Math.abs(span),
    centroid: [integratedAxial / (3 * integratedCross), integratedRadial / integratedCross, 0],
    thicknessCentroid: integratedThick / (3 * integratedCross),
  };
}

/**
 * Root and tip sections of one planform.
 * NACA uses one nondimensional section scaled by the local chord.
 * The hexagon fillet is an absolute length, so each station builds its own tangent arcs;
 * the leading and trailing extremes still sit on that station's trapezoid edge.
 */
export function finAirfoilSections(fin, airfoil) {
  if (airfoil.kind === "hexagon") return hexagonAirfoilSections(fin, airfoil);
  const sectionAt = (radius) => {
    const span = fin.tip_radius - fin.root_radius;
    const fraction = (radius - fin.root_radius) / span;
    const le = fin.buried_le + (fin.tip_le - fin.buried_le) * fraction;
    const chord = fin.te - le;
    return {
      radius,
      le,
      chord,
      points: airfoil.loop.map(([xi, eta]) => [le + xi * chord, eta * chord]),
    };
  };
  const sections = [sectionAt(fin.root_radius), sectionAt(fin.tip_radius)];
  const solid = taperedLoftCentroid({
    le0: sections[0].le,
    c0: sections[0].chord,
    r0: sections[0].radius,
    le1: sections[1].le,
    c1: sections[1].chord,
    r1: sections[1].radius,
    areaRatio: Math.abs(airfoil.properties.area),
    xiBar: airfoil.properties.cx,
  });
  return { sections, centroid: solid.centroid, volume: solid.volume };
}

function hexagonAirfoilSections(fin, airfoil) {
  const sectionAt = (radius) => {
    const span = fin.tip_radius - fin.root_radius;
    const fraction = (radius - fin.root_radius) / span;
    const le = fin.buried_le + (fin.tip_le - fin.buried_le) * fraction;
    const chord = fin.te - le;
    const profile = buildHexagonAirfoil({
      thickness_percent: airfoil.thickness_percent,
      hex_flat_start: airfoil.flatStart,
      hex_flat_end: airfoil.flatEnd,
      chord,
      fillet_radius: airfoil.filletRadiusRequested,
    });
    const shift = (point) => [point[0] + le, point[1]];
    return {
      radius,
      le,
      chord,
      points: profile.loop.map(shift),
      segments: profile.segments.map((segment) => ({
        ...segment,
        from: shift(segment.from),
        to: shift(segment.to),
        ...(segment.center ? { center: shift(segment.center) } : {}),
        ...(segment.via ? { via: shift(segment.via) } : {}),
      })),
      filletRadiusM: profile.fillet.radius,
      filletRequestedM: profile.fillet.requestedRadius,
      leadingFilletRadiusM: profile.fillet.leadingRadius,
      leadingFilletRequestedM: profile.fillet.leadingRequestedRadius,
      leadingFilletClamped: profile.fillet.leadingClamped,
      filletClamped: profile.fillet.clamped,
      filletClampNote: profile.fillet.clampNote,
      sketchScale: hexagonSketchScale(profile.segments),
      ...(fin.sectionFrame ? { frame: fin.sectionFrame } : {}),
    };
  };
  const sections = [sectionAt(fin.root_radius), sectionAt(fin.tip_radius)];
  if (sections.some((section) => section.segments.length !== HEX_FILLET_SEGMENT_COUNT)
    || sections[0].segments.length !== sections[1].segments.length) {
    throw new Error("六边形根梢剖面曲线数不一致，不能放样。");
  }
  const solid = ruledLoftSolid(sections[0], sections[1]);
  if (Math.abs(solid.thicknessCentroid) > 1e-8) {
    throw new Error("六边形放样厚度方向质心不为 0，剖面上下不对称。");
  }
  return { sections, centroid: solid.centroid, volume: solid.volume };
}

/**
 * True bounding box of the finished model: the body plus every fin airfoil vertex.
 * The simple `D + 2·max(B)` formula only holds for a four-fin "+" cruciform, so the expectation
 * is computed from the lofted section points instead.
 */
export function expectedExtents(geometry) {
  const { L, D, fins, cruciform_count, fin_phase_deg } = geometry;
  let maxY = bodyRadius(D);
  let maxZ = bodyRadius(D);
  for (const fin of fins) {
    for (let index = 0; index < cruciform_count; index += 1) {
      const angle = fin_phase_deg + (360 * index) / cruciform_count;
      for (const [, y, z] of finInstancePoints(fin, angle)) {
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
  const airfoil = buildFinAirfoil(params);
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
      { name: "front", ...front },
      { name: "rear", ...rear },
    ],
    ...(airfoil.kind === "hexagon" ? {
      repair: {
        inexactEdges: REPAIR_INEXACT_EDGES,
        degenerateAreaM2: REPAIR_DEGENERATE_AREA_M2,
        smallFaceAreaM2: REPAIR_SMALL_FACE_AREA_M2,
        smallFaceWidthM: REPAIR_SMALL_FACE_WIDTH_M,
        smallFaceMaxRadiusM: REPAIR_SMALL_FACE_MAX_RADIUS_M,
        // Rounds the repair must never consume: both fin-root junctions and the section fillets.
        preserveRadiiM: [
          HEX_ROOT_JUNCTION_RADIUS_M,
          HEX_ROOT_JUNCTION_FORWARD_MIN_RADIUS_M,
          HEX_FILLET_RADIUS_M,
          HEX_FILLET_LE_RADIUS_M,
        ],
        maxVolumeChange: REPAIR_MAX_VOLUME_CHANGE,
        maxSurfaceChange: REPAIR_MAX_SURFACE_CHANGE,
        unit: "m",
      },
      rootJunctionFillet: {
        radius: HEX_ROOT_JUNCTION_RADIUS_M,
        forwardMinRadius: HEX_ROOT_JUNCTION_FORWARD_MIN_RADIUS_M,
        probeRadii: HEX_ROOT_JUNCTION_PROBE_RADII_M,
        minEdges: params.cruciform_count * 2,
        bodyRadius: R,
        seamToleranceM: HEX_ROOT_JUNCTION_SEAM_TOLERANCE_M,
        tolerance: HEX_FILLET_RADIUS_TOLERANCE,
        unit: "m",
        // Each station is rounded on its own. The windows sit on the visible root chord and do not overlap.
        // Only the canted forward station may step down through probeRadii. The aft station stays at radius.
        stations: [
          { name: "front", allowReducedRadius: true, xMin: front.le_at_surface - 0.02, xMax: front.te + 0.02, le: front.le_at_surface, te: front.te },
          { name: "rear", allowReducedRadius: false, xMin: rear.le_at_surface - 0.02, xMax: rear.te + 0.02, le: rear.le_at_surface, te: rear.te },
        ],
      },
    } : {}),
    airfoil: {
      kind: airfoil.kind,
      thickness_percent: params.thickness_percent,
      le_radius_index: params.le_radius_index,
      max_thickness_tenths: params.max_thickness_tenths,
      hex_flat_start: params.hex_flat_start,
      hex_flat_end: params.hex_flat_end,
      thicknessRatio: airfoil.thicknessRatio,
      samplesPerSide: airfoil.kind === "naca" ? AIRFOIL_SAMPLES_PER_SIDE : null,
      edges: airfoil.kind === "hexagon" ? airfoil.loopPoints : airfoil.loop.length,
      ...(airfoil.kind === "hexagon" ? {
        hex_fillet_radius_m: HEX_FILLET_RADIUS_M,
        hex_fillet_le_radius_m: HEX_FILLET_LE_RADIUS_M,
        hex_fillet_tolerance: HEX_FILLET_RADIUS_TOLERANCE,
        hex_root_junction_radius_m: HEX_ROOT_JUNCTION_RADIUS_M,
      } : {}),
    },
    cruciform_count: params.cruciform_count,
    fin_phase_deg: finPhaseDegrees(fin_phase),
    tip_to_tip: D + 2 * Math.max(params.B_W, params.B_F),
    // One side face per profile curve, plus the tip cap. The root cap lies inside the body and is
    // absorbed by Combine.Merge. A hexagonal profile has one face per arc or straight edge.
    expectedFinFaceCount: ((airfoil.kind === "hexagon" ? airfoil.loopPoints : airfoil.loop.length) + 1) * params.cruciform_count * 2,
    model_offset: offset,
    nose_tip_x: offset,
    tip_offset: body.tip.offset,
    nose_junction_x: cylinder ? cylinder.from[0] : params.L_N + offset,
  };
  if (airfoil.kind === "hexagon") {
    // Both hexagon fins stay radial. The forward fin used to be canted by the hexagon bevel angle
    // (6.390 degrees at the defaults) because the root face was assumed to sit tangent to the
    // fuselage, where a ConstantRound on a 0 degree included angle would be refused. Measurement on
    // V241 shows that assumption is false: with the fin radial the forward seam still accepts the
    // full 3 mm ConstantRound, 48 edges and 8 measured blend cylinders, exactly like the aft seam.
    // The cant therefore bought nothing and cost a real defect - a canted forward fin reads as a
    // scissor against the radial aft fin in any axial view - so it is gone.
    const frontFin = result.fins.find((fin) => fin.name === "front");
    frontFin.bodyIntersectionAngleRad = 0;
  }
  for (const fin of result.fins) {
    // The seed fin is a ruled loft, so its centre of mass is the solid centroid. NACA sections
    // are similar; a hexagonal fillet keeps a fixed absolute radius, so its stations are integrated
    // directly. The axial component is what the acceptance gate reads back.
    const solid = finAirfoilSections(fin, airfoil);
    fin.sections = solid.sections;
    fin.centroid = solid.centroid;
    fin.volume = solid.volume;
    // The station that is actually visible - and measurable - on the finished model: where the
    // leading edge meets the body surface. It is what `L_W` / `L_F` are defined to be.
    fin.leAtSurface = fin.le_at_surface;
    fin.leStation = fin.leAtSurface;
    fin.rootChordAtSurface = fin.root_chord_at_surface;
    fin.leFromNoseTip = fin.leAtSurface - result.nose_tip_x;
    fin.leFromNoseJunction = fin.leAtSurface - result.nose_junction_x;
  }
  let expectedFinFaceCount = 0;
  for (const fin of result.fins) {
    const edges = fin.sections[0].segments?.length ?? fin.sections[0].points.length;
    const sameTopology = fin.sections.every((section) => (section.segments?.length ?? section.points.length) === edges);
    if (!sameTopology) throw new Error("根梢剖面曲线数不一致，不能放样。");
    expectedFinFaceCount += (edges + 1) * result.cruciform_count;
  }
  result.airfoil.edges = result.fins[0].sections[0].segments?.length ?? result.fins[0].sections[0].points.length;
  result.expectedFinFaceCount = expectedFinFaceCount;
  result.expectedExtents = expectedExtents(result);
  return result;
}
