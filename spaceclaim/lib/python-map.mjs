import { rankSearch } from "./extract.mjs";

const INTENTS = [
  { test: /圆周阵列|圆周模式|circular\s*(pattern|array)|polar/i, terms: ["Pattern.CreateCircular", "CircularPatternData", "Combine.Merge"], code: (p) => `# Inferred candidate; verify in SCDM V261
# Create one seed body at 0 degrees, then make a six-instance circular pattern and merge it.
pattern_data = CircularPatternData()
pattern_data.CircularAxis = Selection.Create(Line.Create(Point.Create(MM(0), MM(0), MM(0)), Direction.DirZ))
pattern_data.CircularCount = ${p.count ?? 6}
pattern_data.CircularAngle = DEG(${p.angle ?? 360})
Pattern.CreateCircular(BodySelection.Create(seed_body), pattern_data)
Combine.Merge(Selection.Create(GetRootPart().Bodies))` },
  { test: /圆柱|cylinder/i, terms: ["Cylinder", "CreateCylinder"], code: (p) => `# Inferred candidate; verify in SCDM V261\n# Parameters: radius=${p.radius ?? 10}, height=${p.height ?? 50}\n# Use the documented Cylinder/Geometry API returned below.` },
  { test: /拉伸|extrud/i, terms: ["Extrude", "Profile"], code: (p) => `# Inferred candidate; verify in SCDM V261\n# depth=${p.depth ?? 20}\n# Select a closed profile, then call the documented extrusion operation.` },
  { test: /矩形|rectangle/i, terms: ["Rectangle", "Point"], code: () => `# Inferred candidate; verify in SCDM V261\n# Create two corner points and a closed rectangle profile using documented Point/Curve APIs.` },
  { test: /布尔差|差集|boolean.*subtract|subtract/i, terms: ["Subtract", "Boolean"], code: () => `# Inferred candidate; verify in SCDM V261\n# Pass target and tool bodies to the documented Boolean subtract operation.` },
  { test: /球|sphere/i, terms: ["Sphere"], code: (p) => `# Inferred candidate; verify in SCDM V261\n# radius=${p.radius ?? 10}\n# Use the documented Sphere creation API returned below.` },
  { test: /平面|plane/i, terms: ["Plane"], code: () => `# Inferred candidate; verify in SCDM V261\n# Build a plane from origin and normal with the documented Plane API.` },
];

function selectedIntent(intent) {
  return INTENTS.find((candidate) => candidate.test.test(intent)) || { terms: intent.split(/\s+/).filter((term) => term.length > 1).slice(0, 4), code: () => "# No geometry template matched; use the API search results to compose a verified script." };
}

export function findPythonCommands(intent, index, options = {}) {
  const selected = selectedIntent(intent);
  const limit = options.limit ?? 5;
  const matches = selected.terms.flatMap((term) => rankSearch(index.records, term, limit)).filter((record, position, all) => all.findIndex((other) => other.path === record.path) === position).slice(0, limit);
  return {
    intent,
    sourceType: "inferred",
    confidence: matches.length ? "medium" : "low",
    candidates: [{ code: selected.code(options.parameters || {}), sourceType: "inferred", confidence: matches.length ? "medium" : "low", apiDocs: matches.map((record) => ({ title: record.title, name: record.name, path: record.path, syntax: record.syntax })) }],
    note: "The official V261 CHM proves the API documentation, not Python runtime compatibility. Verify this candidate inside SCDM.",
  };
}

export function getGeometryRecipe(task, parameters = {}, index) {
  const isExtrude = /拉伸|extrud/i.test(task);
  const steps = isExtrude ? [
    { step: 1, action: "建立闭合轮廓", result: findPythonCommands("矩形", index, { parameters }) },
    { step: 2, action: "拉伸轮廓生成实体", result: findPythonCommands("拉伸", index, { parameters }) },
  ] : [{ step: 1, action: task, result: findPythonCommands(task, index, { parameters }) }];
  return { task, parameters, steps, note: "Recipe steps are API-grounded where docs exist; Python code remains inferred and must be verified inside SCDM." };
}
