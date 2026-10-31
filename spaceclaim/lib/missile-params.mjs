/**
 * Parameter merging and validation for the missile model.
 *
 * Precedence: explicit caller values > preset file > schema default.
 * Every field records where its value came from so any generated model can be audited.
 */

import { ALL_PARAMS, OPTIMISATION_RANGES, schemaEntry } from "./missile-schema.mjs";
import { deriveGeometry, deriveRegions, noseFilletLimits, resolveModelPlacement, solveNoseFillet, solveNoseTipFillet, bodyRadius } from "./missile-geometry.mjs";

// 输入：调用方 explicit / preset / output / outputDir / modelFile；输出：内存中的校验结果和请求对象。
// 本模块不读写文件；参数默认值统一在 missile-schema.mjs 文件前部，优先级为 explicit > preset > default。
// 下列为原有校验常量，所有数值及校验逻辑保持不变。
export const MIN_TIP_CHORD = 1e-3;
const NUMBER_KINDS = new Set(["number", "integer"]);
const ALLOWED_THICKNESS_TENTHS = [2, 3, 4, 5, 6];

/** Same rounding as DATCOM's NACA card: hundredths of a percent, with a tiny bias off the .xx5 edge. */
export function roundThicknessPercent(value) {
  return Math.round((value + 1e-12) * 100) / 100;
}

/**
 * 六边形等厚段起止，由 `flat_percent` 推导。
 *
 * 与 DATCOM 卡片是同一个量：`LFLATU = flat_percent / 100`（等厚段长度）、
 * `LMAXU = (1 - LFLATU) / 2`（前缘斜坡终点 = 等厚段起点），所以
 * `hex_flat_start = LMAXU`、`hex_flat_end = LMAXU + LFLATU = (1 + LFLATU) / 2`。
 * 起止只由这一个百分数决定，不再提供单独开关：否则数据库里的 16 参数就和模型对不上，
 * 同一份参数能画出两种舵面。
 */
export function hexFlatFromPercent(percent) {
  const fraction = Number(percent) / 100;
  return { start: (1 - fraction) / 2, end: (1 + fraction) / 2 };
}

function kindOf(param) {
  if (param.kind) return param.kind;
  return typeof param.default === "number" ? "number" : "string";
}

export function mergeParams(explicit = {}, preset = {}) {
  const known = new Set(ALL_PARAMS.map((param) => param.field));
  const unknown = [...new Set([...Object.keys(explicit), ...Object.keys(preset)])].filter((field) => !known.has(field));
  const values = {};
  const sources = {};
  for (const param of ALL_PARAMS) {
    if (explicit[param.field] !== undefined) {
      values[param.field] = explicit[param.field];
      sources[param.field] = "explicit";
    } else if (preset[param.field] !== undefined) {
      values[param.field] = preset[param.field];
      sources[param.field] = "preset";
    } else {
      values[param.field] = param.default;
      sources[param.field] = "default";
    }
  }
  return { values, sources, unknown };
}

/** Coerce raw values against the schema. Returns errors for anything that cannot be coerced. */
export function coerceParams(values) {
  const coerced = { ...values };
  const errors = [];
  for (const param of ALL_PARAMS) {
    const kind = kindOf(param);
    const raw = coerced[param.field];
    if (raw === undefined || raw === null || raw === "") continue;
    if (kind === "number" || kind === "integer") {
      const value = typeof raw === "number" ? raw : Number(String(raw).trim());
      if (!Number.isFinite(value)) {
        errors.push(`${param.field} 必须是数值，收到 '${raw}'。`);
        continue;
      }
      if (kind === "integer" && !Number.isInteger(value)) {
        errors.push(`${param.field} 必须是整数，收到 '${raw}'。`);
        continue;
      }
      coerced[param.field] = value;
    } else if (kind === "string" || kind === "enum") {
      coerced[param.field] = typeof raw === "string" ? raw : String(raw);
    }
  }
  return { values: coerced, errors };
}

export function validateMissileParams(values) {
  const errors = [];
  const warnings = [];
  const corrections = [];
  const correctionNotes = [];

  const num = (field) => values[field];
  const need = (condition, message) => {
    if (!condition) errors.push(message);
  };

  // Shape of the body.
  need(num("L_N") > 0, "L_N 必须大于 0。");
  need(num("L") > 0, "L 必须大于 0。");
  need(num("D") > 0, "D 必须大于 0。");
  need(num("L_N") < num("L"), `鼻锥长度 L_N (${num("L_N")}) 必须小于弹身总长度 L (${num("L")})。`);
  need(num("D") < num("L"), `机身直径 D (${num("D")}) 必须小于弹身总长度 L (${num("L")})。`);

  // Fins: chord and span signs.
  for (const [root, tip, span] of [["C_RW", "C_TW", "B_W"], ["C_RF", "C_TF", "B_F"]]) {
    need(num(root) > 0, `${root} 必须大于 0。`);
    need(num(span) > 0, `${span} 必须大于 0。`);
    need(num(tip) >= 0, `${tip} 不能为负。`);
  }
  const rawThickness = num("thickness_percent");
  need(Number.isFinite(rawThickness), "thickness_percent 必须是大于 0、小于 40 的有限数。");
  if (Number.isFinite(rawThickness)) {
    // 只有 NACA 卡片把厚度写到 0.01% 的整数格点上；六边形没有这一步，原值就是外形，
    // 所以必须保留数据库里的 6 位小数，否则审核会看到"参数快照 ≠ 库内参数"。
    if (num("fin_section") === "naca") {
      const rounded = roundThicknessPercent(rawThickness);
      if (rounded !== rawThickness) {
        corrections.push({ field: "thickness_percent", from: rawThickness, to: rounded, reason: "翼型厚度按 NACA 写卡规则四舍五入到 0.01。" });
        values.thickness_percent = rounded;
      }
    }
    need(num("thickness_percent") > 0 && num("thickness_percent") < 40, "thickness_percent 必须是大于 0、小于 40 的有限数。");
  }
  need(Number.isInteger(num("le_radius_index")) && num("le_radius_index") >= 0 && num("le_radius_index") <= 9, "le_radius_index 必须是 0 到 9 的整数。");
  need(ALLOWED_THICKNESS_TENTHS.includes(num("max_thickness_tenths")), "max_thickness_tenths 只允许 2、3、4、5、6。");

  // Axial placement measured from the nose tip to the fin root leading edge.
  need(num("L_W") >= num("L_N"), `前舵轴向位置 L_W (${num("L_W")}) 必须不早于锥柱交接处 L_N (${num("L_N")})。`);
  need(num("L_W") + num("C_RW") <= num("L"), "前舵超出弹尾：L_W + C_RW 必须不大于 L。");
  need(num("L_F") >= num("L_N"), `后舵轴向位置 L_F (${num("L_F")}) 必须不早于锥柱交接处 L_N (${num("L_N")})。`);
  need(num("L_F") + num("C_RF") <= num("L"), "后舵超出弹尾：L_F + C_RF 必须不大于 L。");
  need(num("L_W") + num("C_RW") <= num("L_F"), `前后舵重叠：L_W + C_RW (${num("L_W") + num("C_RW")}) 必须不大于 L_F (${num("L_F")})。`);

  // Nose fillet (cone/cylinder junction).
  const location = num("fillet_location");
  const wantsJunction = location === "cone_cylinder" || location === "both";
  const wantsTip = location === "nose_tip" || location === "both";
  const limits = noseFilletLimits({ L: num("L"), L_N: num("L_N"), D: num("D") });
  need(num("k_nose") >= 0, "k_nose 不能为负。");
  need(num("k_tip") >= 0, "k_tip 不能为负。");
  if (wantsJunction && num("k_nose") > 0) {
    const fillet = solveNoseFillet({ L: num("L"), L_N: num("L_N"), D: num("D"), k_nose: num("k_nose") });
    need(fillet.r_f < limits.maxRadius, `倒圆半径 r_f (${fillet.r_f.toFixed(5)} m) 必须小于机身半径 R (${limits.maxRadius} m)，即 k_nose < ${limits.maxK.toFixed(5)}。`);
    need(fillet.cx <= num("L"), "倒圆的柱面切点超出弹尾。");
    need(fillet.t <= limits.maxTangent, "倒圆在锥面上占用的长度超出了锥面本身。");
    if (fillet.r_f > 0.5 * limits.maxRadius) {
      warnings.push(`倒圆半径 r_f (${fillet.r_f.toFixed(5)} m) 已超过机身半径的一半，头部外形被明显改变。`);
    }
    if (fillet.r_f < 0.001) {
      warnings.push(`交接处倒圆半径 r_f (${fillet.r_f.toFixed(5)} m) 小于 1 mm，可能被建模内核并入尖角而不可区分。`);
    }
  }
  if (wantsTip && num("k_tip") > 0) {
    const tip = solveNoseTipFillet({ L: num("L"), L_N: num("L_N"), D: num("D"), k_tip: num("k_tip") });
    need(tip.r_t < tip.maxRadius, `弹尖倒圆半径 r_t (${tip.r_t.toFixed(5)} m) 过大，会吃掉整个锥面；须小于 ${tip.maxRadius.toFixed(5)} m（k_tip < ${tip.maxK.toFixed(5)}）。`);
    if (tip.offset > 0.5 * num("L_N")) {
      warnings.push(`弹尖钝化截掉了 ${(tip.offset * 1000).toFixed(1)} mm，超过鼻锥长度的一半。`);
    }
    if (tip.r_t < 0.0005) {
      warnings.push(`弹尖倒圆半径 r_t (${tip.r_t.toFixed(5)} m) 小于 0.5 mm，可能被建模内核并入尖点而不可区分。`);
    }
  }

  // Convention parameters.
  const finPhase = schemaEntry("fin_phase");
  need(finPhase.implemented.includes(num("fin_phase")), `fin_phase '${num("fin_phase")}' 暂不支持，仅支持 ${finPhase.implemented.join(" / ")}。`);
  const leMode = schemaEntry("le_mode");
  need(leMode.implemented.includes(num("le_mode")), `le_mode '${num("le_mode")}' 暂不支持，仅支持 ${leMode.implemented.join(" / ")}（不会静默回退）。`);
  const finSection = schemaEntry("fin_section");
  need(finSection.implemented.includes(num("fin_section")), `fin_section '${num("fin_section")}' 暂不支持，仅支持 ${finSection.implemented.join(" / ")}。`);
  const noseShape = schemaEntry("nose_shape");
  need(noseShape.implemented.includes(num("nose_shape")), `nose_shape '${num("nose_shape")}' 暂不支持，仅支持 ${noseShape.implemented.join(" / ")}（不会静默回退成本模块支持的圆锥）。`);
  // 等厚段起止由 flat_percent 推导（见 hexFlatFromPercent）。这里只校验源头那一个数，
  // 再顺手确认推导结果落在开区间内，防止以后有人改公式把它悄悄推出边界。
  const flatPercent = num("flat_percent");
  need(Number.isFinite(flatPercent) && flatPercent > 0 && flatPercent < 100, "flat_percent 必须在 (0, 100) 内：0 会让六边形退化成菱形，100 会让它退化成矩形。");
  const flatStart = num("hex_flat_start");
  const flatEnd = num("hex_flat_end");
  need(Number.isFinite(flatStart) && Number.isFinite(flatEnd) && flatStart > 0 && flatEnd < 1 && flatStart < flatEnd, `六边形等厚段推导结果不合法：flat_percent = ${flatPercent} 推出 [${flatStart}, ${flatEnd}]，需要 0 < start < end < 1。`);
  const filletLocation = schemaEntry("fillet_location");
  need(filletLocation.implemented.includes(num("fillet_location")), `fillet_location '${num("fillet_location")}' 暂不支持，仅支持 ${filletLocation.implemented.join(" / ")}（不会静默回退）。`);
  need(num("root_overlap") > 0, "root_overlap 必须大于 0，否则舵根会脱离弹体。");
  need(num("root_overlap") < Math.min(num("B_W"), num("B_F")), `root_overlap (${num("root_overlap")}) 必须小于最小展长 ${Math.min(num("B_W"), num("B_F"))}，否则舵面会整片没入弹体。`);
  need(Number.isInteger(num("cruciform_count")) && num("cruciform_count") >= 2 && num("cruciform_count") % 2 === 0, "cruciform_count 必须是大于等于 2 的偶数。");

  // Simulation regions must actually contain the model and stay nested.
  const tipRadius = (num("D") + 2 * Math.max(num("B_W"), num("B_F"))) / 2;
  const enclosureShape = schemaEntry("enclosure_shape");
  need(enclosureShape.implemented.includes(num("enclosure_shape")), `enclosure_shape '${num("enclosure_shape")}' 暂不支持，仅支持 ${enclosureShape.implemented.join(" / ")}（不会静默回退）。`);
  need(num("enclosure_radius") > tipRadius, `外壳半径 (${num("enclosure_radius")}) 必须大于模型最大半径 ${tipRadius.toFixed(4)} m，否则舵尖会穿出边界。`);
  need(num("enclosure_front") > 0, "外壳前伸长度必须大于 0。");
  need(num("enclosure_back") > 0, "外壳后伸长度必须大于 0。");
  need(num("boi_radius") > tipRadius, `BOI 半径 (${num("boi_radius")}) 必须大于模型最大半径 ${tipRadius.toFixed(4)} m。`);
  need(num("boi_front") >= 0, "BOI 前伸长度不能为负。");
  need(num("boi_back") >= 0, "BOI 后伸长度不能为负。");
  need(num("boi_radius") < num("enclosure_radius"), `BOI 半径 (${num("boi_radius")}) 必须小于外壳半径 (${num("enclosure_radius")})，否则加密区会穿出远场边界。`);
  // The enclosure does not travel with the model, so nesting is checked in absolute terms: whichever
  // station `model_placement` picks, the enclosure's cushions have to stay non-negative around it.
  const placementMode = schemaEntry("model_placement");
  need(placementMode.implemented.includes(num("model_placement")), `model_placement '${num("model_placement")}' 暂不支持，仅支持 ${placementMode.implemented.join(" / ")}（不会静默回退）。`);
  const placement = resolveModelPlacement(values);
  const offset = placement.offset;
  const station = `导弹的轴向站位 (${offset} m，model_placement = ${placement.mode})`;
  need(num("enclosure_back") - offset > 0, `外壳后伸 (${num("enclosure_back")}) 必须大于${station}，否则外壳后端面会跑到弹尾前面。`);
  need(num("enclosure_front") + offset > 0, `外壳前伸 (${num("enclosure_front")}) 加${station} 必须大于 0，否则外壳前端面会跑到弹尖后面。`);
  need(num("boi_front") <= num("enclosure_front") + offset, `BOI 前伸 (${num("boi_front")}) 超出外壳前端面：最多 ${(num("enclosure_front") + offset).toFixed(4)} m（外壳前伸 ${num("enclosure_front")} + 轴向站位 ${offset}）。`);
  need(num("boi_back") <= num("enclosure_back") - offset, `BOI 后伸 (${num("boi_back")}) 超出外壳后端面：最多 ${(num("enclosure_back") - offset).toFixed(4)} m（外壳后伸 ${num("enclosure_back")} − 轴向站位 ${offset}）。`);
  if (num("boi_radius") < tipRadius * 1.2) {
    warnings.push(`BOI 半径 (${num("boi_radius")}) 仅比模型最大半径 ${tipRadius.toFixed(4)} m 大不到 20%，加密区贴得很紧。`);
  }
  if (num("enclosure_radius") < tipRadius * 4) {
    warnings.push(`外壳半径 (${num("enclosure_radius")}) 不到模型最大半径的 4 倍，远场边界可能影响解。`);
  }

  // Corrections: a zero tip chord degenerates the tip edge.
  for (const tip of ["C_TW", "C_TF"]) {
    if (num(tip) >= 0 && num(tip) < MIN_TIP_CHORD) {
      corrections.push({ field: tip, from: num(tip), to: MIN_TIP_CHORD, reason: "梢弦为 0 会产生退化梢边，已抬到下限。" });
      values[tip] = MIN_TIP_CHORD;
      correctionNotes.push(`${tip}: ${MIN_TIP_CHORD} m`);
    }
  }
  if (num("C_TW") > num("C_RW")) warnings.push("前舵梢弦大于根弦（后缘齐平口径下退化为前掠前缘，几何合法）。");
  if (num("C_TF") > num("C_RF")) warnings.push("后舵梢弦大于根弦（后缘齐平口径下退化为前掠前缘，几何合法）。");

  // Optimisation ranges are advisory, never blocking.
  for (const [field, range] of Object.entries(OPTIMISATION_RANGES)) {
    const value = num(field);
    if (value < range.min || value > range.max) {
      warnings.push(`${field} = ${value} 超出优化区间 [${range.min}, ${range.max}]。`);
    }
  }

  return { errors, warnings, corrections, correctionNotes };
}

/**
 * Full request for one model build: merged values, validation report and the derived geometry
 * that the SCDM script consumes. `ok` is false when the geometry must not be built.
 */
export function buildModelRequest({ explicit = {}, preset = {}, output = {}, outputDir = "", modelFile = "" } = {}) {
  const merged = mergeParams(explicit, preset);
  const errors = merged.unknown.map((field) => `未知参数 '${field}'，字段必须在 lib/missile-schema.mjs 中声明。`);
  const coerced = coerceParams(merged.values);
  errors.push(...coerced.errors);
  // 六边形等厚段：唯一来源是 flat_percent，起止在这里推导后注入，几何层与模板照旧读
  // hex_flat_start / hex_flat_end。它们不再是 schema 字段，所以既不能从命令行传入，
  // 也不会在 params.json 里出现"另一份可改的起止值"。
  const flat = hexFlatFromPercent(coerced.values.flat_percent);
  coerced.values.hex_flat_start = flat.start;
  coerced.values.hex_flat_end = flat.end;
  merged.sources.hex_flat_start = "derived:flat_percent";
  merged.sources.hex_flat_end = "derived:flat_percent";
  const report = validateMissileParams(coerced.values);
  errors.push(...report.errors);
  const ok = errors.length === 0;
  // `model_placement` is resolved from the schema-level rule into a plain axial station inside the
  // geometry layer; this is the same call, reported so the artifacts state where the model went.
  const placement = resolveModelPlacement(coerced.values);
  // 几何层在极薄/极短的截面上会抛错（六边形圆角是绝对半径，厚度不够时相切圆弧不存在）。
  // 把这种"参数合法但建不出来"的情况收成一条 errors 记录，保持 `ok === false` 的契约，
  // 而不是让调用方收到一个未捕获异常。
  let geometry = null;
  let regions = null;
  if (ok) {
    try {
      geometry = deriveGeometry(coerced.values);
      regions = deriveRegions(coerced.values, geometry);
    } catch (error) {
      errors.push(error.message || String(error));
      geometry = null;
      regions = null;
    }
  }
  // The Enclosure command takes a cushion, not a radius: a radius that does not clear the model's
  // bounding-box corner would come out as a negative cushion and grow the wrong way.
  if (regions && !(regions.enclosure.cushion.radius > 0)) {
    errors.push(`外壳半径 (${coerced.values.enclosure_radius}) 必须大于模型包围盒的角点半径 ${regions.enclosure.envelopeRadius.toFixed(4)} m。`);
  }
  // Fin stations are absolute from the nose tip; a shoulder that blunting pulled forward must not
  // overtake them.
  const earlyFins = geometry ? geometry.fins.filter((fin) => fin.leStation < geometry.nose_junction_x) : [];
  if (earlyFins.length) {
    errors.push(`舵面站位 (${earlyFins.map((fin) => fin.leStation.toFixed(5)).join(", ")}) 落在鼻锥上（锥柱交接处在 x = ${geometry.nose_junction_x.toFixed(5)} m）。`);
  }
  const valid = ok && errors.length === 0;
  return {
    ok: valid,
    schemaVersion: 1,
    params: coerced.values,
    // Where `model_placement` actually put the model: the mode's own centring station, the nudge the
    // caller typed, and the sum the geometry was built at.
    placement: { mode: placement.mode, rule: placement.rule, offset: placement.offset, requested: placement.requested },
    sources: merged.sources,
    errors: [...new Set(errors)],
    warnings: report.warnings,
    corrections: report.corrections,
    geometry: valid ? geometry : null,
    regions: valid ? regions : null,
    output: { dir: outputDir, model: modelFile, ...output },
  };
}

export function optimisationPool() {
  return Object.keys(OPTIMISATION_RANGES);
}

export { bodyRadius };
