import assert from "node:assert/strict";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import { fileURLToPath } from "node:url";

// === 输入 / 输出 / 参数来源 ===
// 输入：SCDM_CHM / AWP_ROOT<number> / AWP_ROOT；服务再按共享解析器读取 CHM。
// 输出：检查结论到 stdout；子进程 stdout 保留 JSON-RPC。CHM 提取/索引缓存位置
// 来自 ../lib/paths.mjs 的 defaultCacheRoot()，不创建模型或本入口专用结果文件。
// 下文查询、示例参数和断言是固定 smoke 用例；业务默认值应改 server/lib 的配置区。
const hasVersionedAwpRoot = Object.keys(process.env).some((name) => /^AWP_ROOT\d+$/.test(name) && process.env[name]);
if (!process.env.SCDM_CHM && !hasVersionedAwpRoot && !process.env.AWP_ROOT) throw new Error("Set SCDM_CHM, an AWP_ROOT<version>, or AWP_ROOT before running the smoke test.");
const transport = new StdioClientTransport({ command: "node.exe", args: ["server.mjs"], cwd: fileURLToPath(new URL("..", import.meta.url)), env: { ...process.env } });
const client = new Client({ name: "scdm-smoke", version: "0.1.0" });
await client.connect(transport);
const tools = await client.listTools();
assert.deepEqual(tools.tools.map((tool) => tool.name).sort(), ["build_missile_model", "find_python_command", "generate_model_script", "get_doc", "get_geometry_recipe", "search_api", "validate_missile_params"]);
const search = await client.callTool({ name: "search_api", arguments: { query: "Geometry", limit: 3 } });
assert.match(search.content[0].text, /Geometry/);
for (const query of ["BlockBody.Create", "Copy.Execute", "Move.Rotate", "Pattern.CreateCircular", "Combine.Merge"]) {
  const api = await client.callTool({ name: "search_api", arguments: { query, limit: 5 } });
  assert.equal(api.isError, undefined);
  assert.match(api.content[0].text, new RegExp(query.split(".")[0]));
}
const cylinderSearch = await client.callTool({ name: "search_api", arguments: { query: "Geometry.Cylinder.Create", limit: 5 } });
const cylinderPath = JSON.parse(cylinderSearch.content[0].text).results[0]?.path;
assert.ok(cylinderPath, "search_api returned no Cylinder creation page");
const doc = await client.callTool({ name: "get_doc", arguments: { docPath: cylinderPath, maxChars: 20000 } });
assert.equal(doc.isError, undefined, `get_doc failed for ${cylinderPath}`);
assert.match(doc.content[0].text, /Create/);
const refused = await client.callTool({ name: "get_doc", arguments: { docPath: "../API_Combined_Class_Library.hhc" } });
assert.equal(refused.isError, true);
const python = await client.callTool({ name: "find_python_command", arguments: { intent: "创建圆柱", parameters: { radius: 10, height: 50 } } });
assert.match(python.content[0].text, /inferred/);
const recipe = await client.callTool({ name: "get_geometry_recipe", arguments: { task: "矩形拉伸", parameters: { depth: 20 } } });
assert.match(recipe.content[0].text, /steps/);
const validated = await client.callTool({ name: "validate_missile_params", arguments: { k_nose: 0.006, t_fin: 0.015 } });
assert.match(validated.content[0].text, /optimisationPool/);
const rejected = await client.callTool({ name: "validate_missile_params", arguments: { k_nose: 0.05 } });
assert.equal(rejected.isError, undefined);
assert.match(rejected.content[0].text, /必须小于机身半径/);
console.log("SCDM MCP smoke passed: tools, Geometry search, circular-pattern/merge API search, real Cylinder doc, Python candidate, recipe, and missile parameter validation.");
await transport.close();
