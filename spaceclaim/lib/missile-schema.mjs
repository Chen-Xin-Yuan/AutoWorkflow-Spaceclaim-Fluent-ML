/**
 * Single source of truth for every missile modelling input.
 *
 * Field name, unit, default, optimisation range and description are declared exactly once here.
 * The MCP tool schemas, the command line switches, the params.json template, the validators and
 * the documentation table are all derived from this module. Do not redeclare fields anywhere else.
 *
 * 输入/输出：本模块只返回内存中的参数定义，不读写文件。
 * 可修改参数、单位及默认值已集中在文件前部的四个 *_PARAMS 数组；输出目录/文件名见 OUTPUT_PARAMS。
 * 运行时以调用方显式值 > 预设 > 此处 default 合并，勿另加机器相关绝对路径。
 */

export const MISSILE_SCHEMA_VERSION = 1;

/** Geometric inputs. Every field is caller-overridable; `range` only decides the optimisation pool. */
export const GEOMETRY_PARAMS = [
  { field: "L_N", label: "鼻锥长度", unit: "m", default: 0.49, range: null },
  { field: "L", label: "弹身总长度", unit: "m", default: 3.69, range: null },
  { field: "D", label: "机身最大直径", unit: "m", default: 0.18, range: null },
  { field: "L_W", label: "前十字舵轴向位置（舵面可见前缘到弹尖）", unit: "m", default: 1.72, range: [1.2, 1.8] },
  { field: "C_RW", label: "前舵根弦长", unit: "m", default: 0.23, range: [0.1, 0.4] },
  { field: "C_TW", label: "前舵梢弦长", unit: "m", default: 0.06, range: [0, 0.09] },
  { field: "B_W", label: "前舵展长", unit: "m", default: 0.23, range: [0.1, 0.3] },
  { field: "L_F", label: "后十字舵轴向位置（舵面可见前缘到弹尖）", unit: "m", default: 3.2, range: [3.0, 3.2] },
  { field: "C_RF", label: "后舵根弦长", unit: "m", default: 0.38, range: [0.1, 0.4] },
  { field: "C_TF", label: "后舵梢弦长", unit: "m", default: 0.19, range: [0, 0.25] },
  { field: "B_F", label: "后舵展长", unit: "m", default: 0.22, range: [0.1, 0.3] },
  // 设计厚度 = 4.462（与数据库 geometry 行、DATCOM 的 HEX_THICKNESS_PERCENT 同一个数）。
  // 手工运行不传 --thickness_percent 时用它，派发时用库里那一行的值，两者一致。
  { field: "thickness_percent", label: "舵面最大厚度（弦长的百分数，前后舵共用）", unit: "% chord", default: 4.462, range: null },
  { field: "le_radius_index", label: "前缘半径指数（0 尖前缘，6 标准圆前缘，按 (指数/6)² 缩放）", unit: "", kind: "integer", default: 6, range: null },
  { field: "max_thickness_tenths", label: "最大厚度距前缘的位置（1/10 弦长）", unit: "", kind: "integer", default: 3, range: null },
  { field: "k_nose", label: "鼻锥与柱段交接处倒圆半径系数（r_f = L · k_nose）", unit: "", default: 0.005, range: [0, 0.01] },
  { field: "k_tip", label: "鼻锥尖端倒圆半径系数（r_t = L · k_tip，与锥面相切的球面钝化）", unit: "", default: 0.002, range: [0, 0.01] },
];

/** Conventions that complete the geometry definition. Also caller-overridable. */
export const CONVENTION_PARAMS = [
  { field: "fin_phase", label: "十字舵相位", kind: "enum", default: "+", options: ["+", "x"], implemented: ["+", "x"] },
  { field: "root_overlap", label: "舵根沿展向嵌入弹体的重叠量", unit: "m", kind: "number", default: 0.005, min: 0 },
  { field: "le_mode", label: "梯形舵第四约束", kind: "enum", default: "trailing_aligned", options: ["trailing_aligned", "leading_aligned", "symmetric"], implemented: ["trailing_aligned"] },
  { field: "fin_section", label: "舵面横截面（naca 为对称四位数，hexagon 为等厚段六边形，尖角按固定绝对半径倒圆）", kind: "enum", default: "hexagon", options: ["naca", "hexagon"], implemented: ["naca", "hexagon"] },
  // 数据库里 hex 翼型只有一个几何量：等厚段占弦长的百分数。等厚段在弦上居中，起止由它推导
  // （`hex_flat_start = (1 - p/100) / 2`、`hex_flat_end = (1 + p/100) / 2`，见 missile-params.mjs），
  // 因此不再单独提供起止两个开关：与 DATCOM 的 LFLATU / LMAXU 是同一个量。
  // 默认 64.6 → 等厚段 0.177..0.823。这个值由实测定：前舵根部交接圆角要撑住 3 mm，
  // 平顶终点必须 ≥ 0.822（拐点在 0.8220/0.8221 之间）。60.995（终点 0.805）实测会被压到
  // 1.5 mm 下限，并在根部留下 24 个无法删除的非圆角碎面（feedback/spaceclaim 2026-10-31 那次的
  // run.json：clamped=true、belowFloor=24）。居中推导使起点随之移到 0.177；DATCOM 的六边形
  // 本身就是对称的（LMAXU=(1-LFLATU)/2），两边只能共用这一个值才表示同一个截面。
  { field: "flat_percent", label: "六边形等厚段长度（占当地弦长的百分数，仅 hexagon 使用）", unit: "% chord", kind: "number", default: 64.6, min: 0, max: 100 },
  // 头型：DATCOM 侧有 ogive / cone 两种；本模块目前只建直线圆锥，因此只声明 cone 为已实现，
  // 传 ogive 会按"不会静默回退"的规则直接报错，而不是偷偷建成圆锥。
  { field: "nose_shape", label: "鼻锥头型（cone 为直线圆锥；ogive 暂无实现）", kind: "enum", default: "cone", options: ["cone", "ogive"], implemented: ["cone"] },
  { field: "fillet_location", label: "倒圆位置", kind: "enum", default: "both", options: ["cone_cylinder", "nose_tip", "both"], implemented: ["cone_cylinder", "nose_tip", "both"] },
  { field: "cruciform_count", label: "每级舵面片数", kind: "integer", default: 4, min: 2 },
  { field: "scdm_api_version", label: "目标脚本 API 版本", kind: "string", default: "" },
];

/** Simulation regions: the far-field enclosure boundary and the mesh-refinement BOI, as solids. */
export const REGION_PARAMS = [
  { field: "enclosure_shape", label: "外壳（远场边界）形状", kind: "enum", default: "cylinder", options: ["cylinder"], implemented: ["cylinder"] },
  { field: "enclosure_radius", label: "外壳半径", unit: "m", kind: "number", default: 2.7, min: 0 },
  { field: "enclosure_front", label: "外壳在弹尖之前的前伸长度", unit: "m", kind: "number", default: 3.69, min: 0 },
  { field: "enclosure_back", label: "外壳在弹尾之后的后伸长度", unit: "m", kind: "number", default: 16.45, min: 0 },
  { field: "boi_radius", label: "加密区 BOI 半径", unit: "m", kind: "number", default: 0.45, min: 0 },
  { field: "boi_front", label: "BOI 在弹尖之前的前伸长度", unit: "m", kind: "number", default: 0.5, min: 0 },
  { field: "boi_back", label: "BOI 在弹尾之后的后伸长度", unit: "m", kind: "number", default: 2.5, min: 0 },
  { field: "model_placement", label: "导弹与 BOI 的轴向定位方式", kind: "enum", default: "ensemble", options: ["ensemble", "missile", "offset"], implemented: ["ensemble", "missile", "offset"] },
  { field: "model_offset", label: "在定位规则之上再加的轴向偏移（沿 +X，正值向弹尾、负值向弹尖即靠前）", unit: "m", kind: "number", default: -4.0 },
  { field: "enclosure_name", label: "外壳在结构树里的名字（组件名）", kind: "string", default: "Enclosure" },
  { field: "boi_name", label: "加密区实体的名字", kind: "string", default: "BOI" },
];

/** Run-level outputs. Not part of the model definition, but declared here so the CLI and MCP agree. */
export const OUTPUT_PARAMS = [
  { field: "output_dir", label: "产物目录", kind: "string", default: "" },
  { field: "file_name", label: "模型文件名", kind: "string", default: "missile.scdoc" },
];

export const ALL_PARAMS = [...GEOMETRY_PARAMS, ...CONVENTION_PARAMS, ...REGION_PARAMS, ...OUTPUT_PARAMS];

/** Optimisation pool: derived from the data, not from code. Add a key to take part in optimisation. */
export const OPTIMISATION_RANGES = Object.fromEntries(
  GEOMETRY_PARAMS.filter((param) => param.range).map((param) => [param.field, { min: param.range[0], max: param.range[1] }]),
);

export const GEOMETRY_FIELDS = GEOMETRY_PARAMS.map((param) => param.field);
export const CONVENTION_FIELDS = CONVENTION_PARAMS.map((param) => param.field);
export const REGION_FIELDS = REGION_PARAMS.map((param) => param.field);
export const OUTPUT_FIELDS = OUTPUT_PARAMS.map((param) => param.field);

/**
 * Fields that the generated SCDM script reads from params.json.
 *
 * 派生值不在这个列表里，但同样会写进 params.json：`hex_flat_start` / `hex_flat_end` 由
 * `flat_percent` 在 missile-params.mjs 里推导后注入 `params`，模板照旧读这两个键。
 * 列表只列"可以由调用方给定"的字段，避免把派生值误认成第二个可配入口。
 */
export const MODEL_INPUT_FIELDS = [...GEOMETRY_FIELDS, ...CONVENTION_FIELDS.filter((field) => field !== "scdm_api_version"), ...REGION_FIELDS];

export function defaultParams() {
  return Object.fromEntries(ALL_PARAMS.map((param) => [param.field, param.default]));
}

export function schemaEntry(field) {
  return ALL_PARAMS.find((param) => param.field === field) || null;
}
