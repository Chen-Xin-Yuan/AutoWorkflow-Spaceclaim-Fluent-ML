import assert from "node:assert/strict";
import test from "node:test";
import { findPythonCommands, getGeometryRecipe } from "../lib/python-map.mjs";

const index = { records: [
  { name: "Cylinder", title: "Cylinder Class", path: "html/cylinder.htm", syntax: "public class Cylinder" },
  { name: "Extrude", title: "Extrude Method", path: "html/extrude.htm", syntax: "public static Body Extrude()" },
  { name: "Pattern.CreateCircular", title: "Create Circular Pattern", path: "html/pattern.htm", syntax: "Pattern.CreateCircular(selection, data)" },
  { name: "CircularPatternData", title: "Circular Pattern Data", path: "html/pattern-data.htm", syntax: "CircularPatternData" },
  { name: "Combine.Merge", title: "Merge Bodies", path: "html/combine.htm", syntax: "Combine.Merge(targetSelection)" },
] };

test("returns an evidence-labelled cylinder candidate", () => {
  const result = findPythonCommands("创建圆柱", index, { parameters: { radius: 10, height: 50 } });
  assert.equal(result.sourceType, "inferred");
  assert.match(result.candidates[0].code, /radius=10/);
  assert.equal(result.candidates[0].apiDocs[0].name, "Cylinder");
});

test("builds a multi-step extrusion recipe", () => {
  const result = getGeometryRecipe("矩形拉伸", { depth: 20 }, index);
  assert.equal(result.steps.length, 2);
  assert.match(result.steps[1].result.candidates[0].code, /depth=20/);
});

test("returns an API-grounded inferred candidate without a Python example index", () => {
  const result = findPythonCommands("拉伸", index);
  assert.equal(result.sourceType, "inferred");
  assert.doesNotMatch(result.candidates[0].code, /ExtrudeFaces\.Execute/);
});

test("maps circular repeat to the documented pattern and merge APIs", () => {
  const result = findPythonCommands("圆周阵列", index, { parameters: { count: 6, angle: 360 } });
  const code = result.candidates[0].code;
  assert.match(code, /Pattern\.CreateCircular/);
  assert.match(code, /CircularCount = 6/);
  assert.match(code, /Combine\.Merge/);
  assert.equal(result.candidates[0].apiDocs.length, 3);
});
