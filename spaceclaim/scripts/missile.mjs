#!/usr/bin/env node
/**
 * Command line entry point for the parametric missile model.
 *
 * Every switch is derived from lib/missile-schema.mjs, so adding a parameter to the schema adds it
 * here, to the MCP tools and to params.json at the same time.
 */

import path from "node:path";
import { ALL_PARAMS, GEOMETRY_PARAMS, OPTIMISATION_RANGES, OUTPUT_FIELDS, defaultParams } from "../lib/missile-schema.mjs";
import { buildMissile } from "../lib/spaceclaim-runner.mjs";
import { readFile } from "node:fs/promises";

// === 输入 / 输出 / 可调默认参数（集中修改区） ===
// 输入：--preset JSON（相对路径基于调用者当前目录）；显式 CLI 参数覆盖预设。
// 输出：--out/--name 覆盖下列默认值；空 outputDir 保留 SCDM_MISSILE_OUTPUT /
// workspace.json 的默认目录解析，在运行时创建唯一 run 目录；失败保留到 feedback。
// 成功执行后的目录留 params.json、model.py、模型文件和 run.json/result.json/diagnostic.txt。
// 验收结果打印在控制台。失败时运行目录移到 feedback。仅生成脚本时仍写运行清单。
// 几何参数默认值及允许范围只在 ../lib/missile-schema.mjs 定义，--list 可查看。
const DEFAULT_OPTIONS = {
  preset: "",
  outputDir: "",
  runId: "",
  fileName: "missile.scdoc",
  execute: true,
  timeoutMs: 300000,
  json: false,
  list: false,
};

const MODEL_FIELDS = ALL_PARAMS.filter((param) => !OUTPUT_FIELDS.includes(param.field)).map((param) => param.field);

function usage() {
  const rows = ALL_PARAMS.map((param) => {
    const range = OPTIMISATION_RANGES[param.field];
    const suffix = range ? `  (优化区间 ${range.min} – ${range.max})` : "";
    return `  --${param.field.padEnd(18)} ${String(param.default).padEnd(10)} ${param.label}${suffix}`;
  });
  return [
    "用法: node scripts/missile.mjs [参数开关] [选项]",
    "",
    "参数开关（全部可省略，省略时取默认值）:",
    ...rows,
    "",
    "选项:",
    "  --preset <file.json>   预设参数文件，优先级低于命令行开关",
    "  --out <dir>            产物根目录；最终目录是 <dir>/<run-id>（默认 data_lib/runs/high/spaceclaim/<run-id>）",
    "  --run-id <id>          运行 ID，一般由数据库给出，形如 <geometry_id>__<condition_id>__<时间戳>",
    "  --name <file.scdoc>    模型文件名",
    "  --generate-only        只生成 params.json 与 model.py，不启动 SpaceClaim",
    `  --timeout <ms>         SpaceClaim 执行超时（默认 ${DEFAULT_OPTIONS.timeoutMs}）`,
    "  --json                 以 JSON 输出结果",
    "  --list                 列出参数与默认值后退出",
    "",
    "示例:",
    "  node scripts/missile.mjs --k_nose 0.006 --out D:\\models",
    "  node scripts/missile.mjs --preset my.json --generate-only",
  ].join("\n");
}

function parseArgs(argv) {
  const explicit = {};
  const options = { ...DEFAULT_OPTIONS };
  for (let index = 0; index < argv.length; index += 1) {
    const token = argv[index];
    if (!token.startsWith("--")) throw new Error(`无法识别的参数 '${token}'。`);
    const name = token.slice(2);
    if (name === "list") { options.list = true; continue; }
    if (name === "json") { options.json = true; continue; }
    if (name === "generate-only") { options.execute = false; continue; }
    const value = argv[index + 1];
    if (value === undefined || value.startsWith("--")) throw new Error(`开关 --${name} 缺少取值。`);
    index += 1;
    if (name === "preset") { options.preset = value; continue; }
    if (name === "out") { options.outputDir = value; continue; }
    if (name === "run-id") { options.runId = value; continue; }
    if (name === "name") { options.fileName = value; continue; }
    if (name === "timeout") { options.timeoutMs = Number(value); continue; }
    if (!MODEL_FIELDS.includes(name)) throw new Error(`未知参数 '--${name}'。字段必须在 lib/missile-schema.mjs 中声明。`);
    explicit[name] = value;
  }
  return { explicit, options };
}

async function loadPreset(file) {
  if (!file) return {};
  const parsed = JSON.parse(await readFile(path.resolve(file), "utf8"));
  // Accept either a bare parameter object or a previously generated params.json.
  return parsed.params && typeof parsed.params === "object" ? parsed.params : parsed;
}

async function main() {
  const { explicit, options } = parseArgs(process.argv.slice(2));
  if (options.list) {
    console.log(JSON.stringify({ defaults: defaultParams(), optimisationPool: OPTIMISATION_RANGES, geometryFields: GEOMETRY_PARAMS.map((param) => param.field) }, null, 2));
    return 0;
  }

  const preset = await loadPreset(options.preset);
  const result = await buildMissile({
    explicit,
    preset,
    outputDir: options.outputDir,
    runId: options.runId,
    fileName: options.fileName,
    execute: options.execute,
    timeoutMs: options.timeoutMs,
  });

  if (options.json) {
    console.log(JSON.stringify(result, null, 2));
    return result.ok ? 0 : 1;
  }

  for (const warning of result.warnings ?? []) console.log(`警告: ${warning}`);
  for (const correction of result.corrections ?? []) console.log(`修正: ${correction.field} ${correction.from} → ${correction.to}（${correction.reason}）`);
  if (result.status === "invalid-parameters") {
    console.error(`参数校验失败，诊断已保存到 ${result.runDir}：`);
    for (const error of result.errors) console.error(`  - ${error}`);
    return 2;
  }
  if (result.status === "script-generated") {
    console.log(`已生成（未执行）：${result.runDir}`);
    console.log(`  params.json: ${result.paramsPath}`);
    console.log(`  model.py   : ${result.scriptPath}`);
    if (result.executableError) console.log(`  提示: ${result.executableError}`);
    return 0;
  }
  for (const check of result.acceptance.checks) console.log(`${check.ok ? "通过" : "失败"}  ${check.name}  :: ${check.detail}`);
  console.log(`\n模型: ${result.modelPath}`);
  console.log(`参数: ${result.paramsPath}`);
  console.log(`脚本: ${result.scriptPath}`);
  if (!result.ok && result.diagnostic) console.log(`诊断: ${result.diagnostic.split(/\r?\n/).slice(0, 4).join(" / ")}`);
  if (!result.ok) {
    console.error(`\n验收未通过（status=${result.status}），产物保留在 ${result.runDir}`);
    return 1;
  }
  console.log(`\n验收通过：${result.acceptance.checks.length} 项检查全部满足，产物在 ${result.runDir}`);
  return 0;
}

try {
  process.exitCode = await main();
} catch (error) {
  if (process.argv.includes("--help") || process.argv.includes("-h")) {
    console.log(usage());
    process.exitCode = 0;
  } else {
    console.error(`错误: ${error.message}`);
    console.error("");
    console.error(usage());
    process.exitCode = 2;
  }
}

