import test from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

const projectDir = fileURLToPath(new URL("..", import.meta.url));
const hasChm = Boolean(process.env.SCDM_CHM) || Object.keys(process.env).some((name) => /^AWP_ROOT/i.test(name) && process.env[name]);

test("server exits clearly when no CHM is configured", async () => {
  const childEnv = { ...process.env };
  for (const name of Object.keys(childEnv)) {
    if (/^AWP_ROOT/i.test(name) || name === "SCDM_CHM" || name === "SCDM_API_VERSION") delete childEnv[name];
  }
  // Keep the test hermetic: a saved first-run config must not satisfy the lookup either.
  childEnv.LOCALAPPDATA = await mkdtemp(path.join(tmpdir(), "ansys-scdmdoc-no-chm-"));
  const child = spawn("node.exe", ["server.mjs"], { cwd: projectDir, env: childEnv });
  let stderr = "";
  child.stderr.on("data", (chunk) => { stderr += chunk.toString(); });
  const code = await new Promise((resolve) => child.once("close", resolve));
  assert.notEqual(code, 0);
  assert.match(stderr, /SpaceClaim API CHM not found/);
});

test("the model tool reports generated-only instead of runtime-verified", { skip: !hasChm ? "needs an installed CHM" : false }, async () => {
  const outputDir = await mkdtemp(path.join(tmpdir(), "scdm-mcp-generated-"));
  const transport = new StdioClientTransport({ command: "node.exe", args: ["server.mjs"], cwd: projectDir, env: { ...process.env } });
  const client = new Client({ name: "scdm-protocol-test", version: "0.1.0" });
  await client.connect(transport);
  try {
    const built = await client.callTool({ name: "build_missile_model", arguments: { execute: false, outputDir, fileName: "missile.scdoc" } });
    const payload = JSON.parse(built.content[0].text);
    assert.equal(payload.status, "GENERATED");
    assert.equal(payload.ok, true);
    assert.match(payload.evidence, /^generated-only/);
    assert.equal(built.isError, undefined);
    const script = await client.callTool({ name: "generate_model_script", arguments: { outputDir } });
    assert.equal(JSON.parse(script.content[0].text).status, "script-generated");
  } finally {
    await transport.close();
  }
});
