import assert from "node:assert/strict";
import test from "node:test";
import { access, mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { ensureExtracted } from "../lib/chm.mjs";
import { resolveChmPath } from "../lib/paths.mjs";

test("extracts the real SpaceClaim V261 CHM and reuses its cache", async (t) => {
  if (process.env.SCDM_RUN_REAL_CHM_TEST !== "1") {
    t.skip("Set SCDM_RUN_REAL_CHM_TEST=1 to run the opt-in real CHM extraction test");
    return;
  }
  let realChm;
  try { realChm = (await resolveChmPath(process.env)).path; } catch { t.skip("No SpaceClaim CHM is available through the configured ANSYS environment variables"); return; }
  try { await access(realChm); } catch { t.skip("Configured SpaceClaim CHM is not available"); return; }
  const cache = await mkdtemp(path.join(tmpdir(), "scdm-chm-test-"));
  const first = await ensureExtracted(realChm, cache);
  assert.equal(first.reused, false);
  await access(first.hhc);
  await access(first.hhk);
  const second = await ensureExtracted(realChm, cache);
  assert.equal(second.reused, true);
  assert.equal(second.root, first.root);
});
