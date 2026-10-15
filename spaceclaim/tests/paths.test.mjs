import assert from "node:assert/strict";
import test from "node:test";
import { mkdir, mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { inferredChmPath, resolveChmPath } from "../lib/paths.mjs";
import { discoverAwpRoots, resolveSetupSelection } from "../lib/setup.mjs";

test("resolves the SCDM_CHM environment variable first", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "scdm-path-"));
  const explicit = path.join(root, "explicit.chm");
  const envChm = path.join(root, "env.chm");
  await writeFile(explicit, "x");
  await writeFile(envChm, "x");
  const result = await resolveChmPath({ SCDM_CHM: envChm, AWP_ROOT261: path.join(root, "v261") });
  assert.equal(result.source, "SCDM_CHM");
  assert.equal(result.path, path.resolve(envChm));
});

test("derives the CHM path from an explicit API version", () => {
  const root = path.join(tmpdir(), "ansys-root");
  assert.equal(inferredChmPath(root, "V241"), path.join(path.resolve(root), "scdm", "SpaceClaim.Api.V241", "API_Combined_Class_Library.chm"));
  assert.equal(inferredChmPath(root), null);
});

test("discovers the installed API version below the generic AWP_ROOT", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "scdm-awp-scan-"));
  const chm = path.join(root, "scdm", "SpaceClaim.Api.V241", "API_Combined_Class_Library.chm");
  await mkdir(path.dirname(chm), { recursive: true });
  await writeFile(chm, "x");
  const result = await resolveChmPath({ AWP_ROOT: root });
  assert.equal(result.source, "AWP_ROOT");
  assert.equal(result.path, path.resolve(chm));
});

test("uses the highest configured AWP_ROOT version", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "scdm-highest-awp-"));
  const lower = path.join(root, "v241", "scdm", "SpaceClaim.Api.V241", "API_Combined_Class_Library.chm");
  const higher = path.join(root, "v262", "scdm", "SpaceClaim.Api.V262", "API_Combined_Class_Library.chm");
  const fs = await import("node:fs/promises");
  await fs.mkdir(path.dirname(lower), { recursive: true });
  await fs.mkdir(path.dirname(higher), { recursive: true });
  await fs.writeFile(lower, "x");
  await fs.writeFile(higher, "x");
  const result = await resolveChmPath({ AWP_ROOT241: path.join(root, "v241"), AWP_ROOT262: path.join(root, "v262") });
  assert.equal(result.source, "AWP_ROOT262");
  assert.equal(result.path, path.resolve(higher));
});

test("supports the generic AWP_ROOT with an explicit API version", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "scdm-awp-"));
  const chm = path.join(root, "scdm", "SpaceClaim.Api.V261", "API_Combined_Class_Library.chm");
  const fs = await import("node:fs/promises");
  await fs.mkdir(path.dirname(chm), { recursive: true });
  await fs.writeFile(chm, "x");
  const result = await resolveChmPath({ AWP_ROOT: root, SCDM_API_VERSION: "V261" });
  assert.equal(result.source, "AWP_ROOT+SCDM_API_VERSION");
  assert.equal(result.path, path.resolve(chm));
});

test("reports a useful error when no configured file exists", async () => {
  await assert.rejects(() => resolveChmPath({ SCDM_CHM: "Z:\\missing.chm", AWP_ROOT261: "Z:\\missing-root" }), /SpaceClaim API CHM not found/);
});

test("discovers versioned AWP_ROOT variables in descending version order", () => {
  const roots = discoverAwpRoots({ AWP_ROOT241: "C:\\ansys241", AWP_ROOT261: "C:\\ansys261" });
  assert.deepEqual(roots.map((item) => item.name), ["AWP_ROOT261", "AWP_ROOT241"]);
});

test("setup selection validates a chosen AWP_ROOT", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "scdm-setup-awp-"));
  const chm = path.join(root, "scdm", "SpaceClaim.Api.V261", "API_Combined_Class_Library.chm");
  const fs = await import("node:fs/promises");
  await fs.mkdir(path.dirname(chm), { recursive: true });
  await fs.writeFile(chm, "x");
  const result = await resolveSetupSelection({ mode: "awp_root", awpVariable: "AWP_ROOT261" }, { AWP_ROOT261: root });
  assert.equal(result.chmPath, path.resolve(chm));
});

test("setup selection rejects an AWP_ROOT without a SpaceClaim CHM", async () => {
  await assert.rejects(
    () => resolveSetupSelection({ mode: "awp_root", awpVariable: "AWP_ROOT261" }, { AWP_ROOT261: "Z:\\missing-root" }),
    /无法根据 AWP 确定环境变量的路径，无法确定 SpaceClaim 的路径/,
  );
});
