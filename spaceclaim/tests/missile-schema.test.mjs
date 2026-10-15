import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import {
  ALL_PARAMS,
  CONVENTION_FIELDS,
  CONVENTION_PARAMS,
  GEOMETRY_FIELDS,
  GEOMETRY_PARAMS,
  MODEL_INPUT_FIELDS,
  OPTIMISATION_RANGES,
  OUTPUT_FIELDS,
  defaultParams,
  schemaEntry,
} from "../lib/missile-schema.mjs";

const PLAN_PATH = new URL("../docs/superpowers/plans/2026-09-17-missile-parametric-scdoc.md", import.meta.url);

test("declares every documented geometry parameter with a default", () => {
  assert.equal(GEOMETRY_PARAMS.length, 14);
  assert.deepEqual(GEOMETRY_FIELDS, ["L_N", "L", "D", "L_W", "C_RW", "C_TW", "B_W", "L_F", "C_RF", "C_TF", "B_F", "t_fin", "k_nose", "k_tip"]);
  for (const param of GEOMETRY_PARAMS) {
    assert.equal(typeof param.default, "number", `${param.field} needs a numeric default`);
    assert.ok(param.label, `${param.field} needs a label`);
  }
});

test("defaults reproduce the agreed baseline", () => {
  const values = defaultParams();
  assert.equal(values.L_N, 0.49);
  assert.equal(values.L, 3.69);
  assert.equal(values.D, 0.18);
  assert.equal(values.L_W, 1.72);
  assert.equal(values.C_RW, 0.23);
  assert.equal(values.C_TW, 0.06);
  assert.equal(values.B_W, 0.23);
  assert.equal(values.L_F, 3.2);
  assert.equal(values.C_RF, 0.38);
  assert.equal(values.C_TF, 0.19);
  assert.equal(values.B_F, 0.22);
  assert.equal(values.t_fin, 0.012);
  assert.equal(values.k_nose, 0.005);
  assert.equal(values.k_tip, 0.002);
});

test("t_fin is an overridable input, not a frozen constant", () => {
  const param = schemaEntry("t_fin");
  assert.equal(param.default, 0.012);
  assert.equal(param.range, null, "t_fin stays out of the optimisation pool but must remain settable");
});

test("optimisation pool is derived from data and covers the agreed ranges", () => {
  assert.deepEqual(Object.keys(OPTIMISATION_RANGES).sort(), ["B_F", "B_W", "C_RF", "C_RW", "C_TF", "C_TW", "L_F", "L_W", "k_nose", "k_tip"].sort());
  assert.deepEqual(OPTIMISATION_RANGES.k_nose, { min: 0, max: 0.01 });
  assert.deepEqual(OPTIMISATION_RANGES.k_tip, { min: 0, max: 0.01 });
  assert.deepEqual(OPTIMISATION_RANGES.L_W, { min: 1.2, max: 1.8 });
  assert.deepEqual(OPTIMISATION_RANGES.L_F, { min: 3.0, max: 3.2 });
  for (const field of Object.keys(OPTIMISATION_RANGES)) {
    assert.ok(GEOMETRY_FIELDS.includes(field), `${field} must be a geometry field`);
  }
});

test("convention parameters expose their implemented value set", () => {
  assert.deepEqual(CONVENTION_FIELDS, ["fin_phase", "root_overlap", "le_mode", "fillet_location", "cruciform_count", "scdm_api_version"]);
  assert.deepEqual(schemaEntry("fin_phase").options, ["+", "x"]);
  assert.deepEqual(schemaEntry("le_mode").implemented, ["trailing_aligned"]);
  assert.deepEqual(schemaEntry("fillet_location").options, ["cone_cylinder", "nose_tip", "both"]);
  assert.deepEqual(schemaEntry("fillet_location").implemented, ["cone_cylinder", "nose_tip", "both"]);
  assert.equal(schemaEntry("fillet_location").default, "both");
  assert.deepEqual(OUTPUT_FIELDS, ["output_dir", "file_name"]);
});

test("the model script input list excludes run-level fields", () => {
  for (const field of ["output_dir", "file_name", "scdm_api_version"]) {
    assert.ok(!MODEL_INPUT_FIELDS.includes(field), `${field} is not a geometry input`);
  }
  for (const field of GEOMETRY_FIELDS) {
    assert.ok(MODEL_INPUT_FIELDS.includes(field), `${field} must reach the script`);
  }
});

test("no declared field is missing from the plan document", async () => {
  const plan = await readFile(PLAN_PATH, "utf8");
  const documented = new Set([...plan.matchAll(/`([A-Za-z_][A-Za-z0-9_]*)`/g)].map((match) => match[1]));
  for (const param of ALL_PARAMS) {
    assert.ok(documented.has(param.field), `${param.field} is declared in the schema but absent from the plan document`);
  }
  for (const param of CONVENTION_PARAMS) {
    assert.ok(param.kind, `${param.field} needs an explicit kind so coercion stays predictable`);
  }
});
