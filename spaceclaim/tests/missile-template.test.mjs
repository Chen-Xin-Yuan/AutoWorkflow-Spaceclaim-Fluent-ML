import assert from "node:assert/strict";
import test from "node:test";
import { buildModelRequest } from "../lib/missile-params.mjs";
import { buildRequestDocument, renderModelScript } from "../lib/missile-template.mjs";

test("the rendered script carries the target API version", () => {
  const script = renderModelScript("V241");
  assert.match(script, /API Version = V241/);
  assert.equal(script.includes("__API_VERSION__"), false);
  // Every placeholder must be substituted, not just the first one.
  assert.match(script, /from SpaceClaim\.Api\.V241 import WindowExportFormat/);
});

test("a script without a known version still parses", () => {
  const script = renderModelScript("");
  assert.equal(script.includes("__API_VERSION__"), false);
  assert.equal(/from SpaceClaim\.Api\. import/.test(script), false);
  assert.match(script, /WindowExportFormat = None/);
});

test("regions are built into the same document as separate bodies", () => {
  const script = renderModelScript("V241");
  // One document, one save: the model, the enclosure and the BOI live in the same .scdoc.
  assert.equal(script.includes("Document.Create()"), false);
  assert.equal(script.split("DocumentSave.Execute(model_path)").length - 1, 1);
  // The enclosure comes from the Prepare > Enclosure command, with the frame turned onto global X.
  assert.match(script, /result = Enclosure\.Create\(Selection\.Create\(\[source\]\), options\)/);
  assert.match(script, /options\.Frame = Frame\.Create\(Point\.Create\(0, 0, 0\), Direction\.DirX\)/);
  assert.match(script, /options\.EnclosureType = types\[region\["shape"\]\]/);
  assert.match(script, /options\.EnclosureCushion = CylinderEnclosureCushion\(cushion\["axisLow"\], cushion\["axisHigh"\], cushion\["radius"\]\)/);
  // Subtract would hollow the far-field boundary out around the model.
  assert.match(script, /options\.Subtract = False/);
  // The created body's name is read-only, so the component the command creates carries the name.
  assert.match(script, /component\.Name = region\.get\("bodyName", region\["name"\]\)/);
  // The BOI keeps the revolve path.
  assert.match(script, /options\.ExtrudeType = ExtrudeType\.ForceIndependent/);
  assert.match(script, /name_body\(built, region\.get\("bodyName", region\["name"\]\)\)/);
  // Bodies the command parks inside a component are invisible to the part's own body list.
  assert.match(script, /GetRootPart\(\)\.GetAllBodies\(\)/);
  // Body names must stay ASCII: the host cannot read a non-ASCII name back.
  assert.match(script, /name_body\(merged\[0\], "Missile"\)/);
  const nonAscii = [...script].filter((character) => character.charCodeAt(0) > 127);
  const bodyNames = /name_body\([^)]*"([^"]*)"/g;
  for (const match of script.matchAll(bodyNames)) {
    assert.equal([...match[1]].some((character) => character.charCodeAt(0) > 127), false, `body name '${match[1]}' must be ASCII`);
  }
  // The script source stays pure ASCII: a non-ASCII name such as the enclosure's 外壳 arrives
  // through the JSON request, so the host never has to guess the script's charset.
  assert.deepEqual(nonAscii, []);
});

test("the scene snapshots are optional and cannot fail the build", () => {
  const script = renderModelScript("V241");
  assert.match(script, /Window\.ActiveWindow\.Export\(WindowExportFormat\.Png, path\)/);
  assert.match(script, /ActivateNamedView\("Isometric"/);
  // ActivateNamedView returns False when the host does not know the view name, so an explicit zoom
  // is what actually frames the shot: the model alone for the first, the whole scene for the second.
  assert.match(script, /ViewHelper\.ZoomToEntity\(Selection\.Create\(selection\)\)/);
  assert.match(script, /ViewHelper\.ZoomToEntity\(\)/);
  // The export sits inside a try/except that records a diagnostic instead of raising.
  assert.match(script, /DIAGNOSTICS\[key\] = "error: " \+ str\(error\)/);
  // One snapshot of the model alone, one with the simulation regions in place.
  assert.match(script, /snapshot\(image_path, "image", \[merged\[0\]\]\)/);
  assert.match(script, /snapshot\(request\["output"\]\.get\("assemblyImage", ""\), "assemblyImage"\)/);
});

test("the script measures where the fins actually sit", () => {
  const script = renderModelScript("V241");
  // A fin slid along the cylinder leaves extents, areas and volume untouched, so the nose-blunting
  // compensation can only be verified through the fin's own mass centre.
  assert.match(script, /MeasureHelper\.GetCenterOfMass\(Selection\.Create\(body\)\)/);
  assert.match(script, /"centerOfMassM": measure_center_of_mass\(item\)/);
  assert.match(script, /"finPositions": fin_positions,/);
});

test("the script holds no geometry constants of its own", () => {
  const script = renderModelScript("V241");
  for (const literal of ["0.49", "3.69", "0.18", "1.72", "0.23", "0.06", "3.2", "0.38", "0.19", "0.22", "0.012", "0.005", "18.45"]) {
    assert.equal(script.includes(literal), false, `the template must not hard-code ${literal}`);
  }
});

test("the script reads its parameters and reports both success and failure", () => {
  const script = renderModelScript("V241");
  for (const token of ["SCDM_MISSILE_PARAMS", "SCDM_MISSILE_DIAGNOSTIC", "json.load", "DocumentSave.Execute", "status", "traceback.format_exc"]) {
    assert.ok(script.includes(token), `the template must use ${token}`);
  }
  assert.match(script, /SketchArc\.CreateSweepArc/);
  assert.match(script, /ExtrudeType\.ForceIndependent/);
  assert.match(script, /Pattern\.CreateCircular/);
  assert.match(script, /Combine\.Merge/);
});

test("the request document carries values, sources, geometry and outputs", () => {
  const request = buildModelRequest({ explicit: { t_fin: 0.02 } });
  const document = buildRequestDocument(request, { model: "m.scdoc", result: "r.json", diagnostic: "d.txt" });
  assert.equal(document.params.t_fin, 0.02);
  assert.equal(document.sources.t_fin, "explicit");
  assert.equal(document.sources.L, "default");
  assert.equal(document.geometry.body.fillet.enabled, true);
  assert.equal(document.output.model, "m.scdoc");
  assert.equal(document.units, "m");
  assert.equal(document.schemaVersion, 1);
  // The document must be JSON round-trippable because the SCDM script loads it from disk.
  assert.deepEqual(JSON.parse(JSON.stringify(document)), document);
});
