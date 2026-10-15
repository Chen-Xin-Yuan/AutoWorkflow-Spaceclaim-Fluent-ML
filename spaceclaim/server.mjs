#!/usr/bin/env node
import { promises as fs } from "node:fs";
import path from "node:path";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";
import { resolveChmPath, defaultCacheRoot, apiVersionFromChmPath } from "./lib/paths.mjs";
import { ensureExtracted } from "./lib/chm.mjs";
import { loadOrBuildIndex, rankSearch } from "./lib/index.mjs";
import { findPythonCommands, getGeometryRecipe } from "./lib/python-map.mjs";
import { ALL_PARAMS, OUTPUT_FIELDS } from "./lib/missile-schema.mjs";
import { buildModelRequest, optimisationPool } from "./lib/missile-params.mjs";
import { buildMissile } from "./lib/spaceclaim-runner.mjs";
import { readFile } from "node:fs/promises";

const resolved = await resolveChmPath(process.env).catch((error) => { process.stderr.write(`${error.message}\n`); process.exit(1); });
const apiVersion = apiVersionFromChmPath(resolved.path);
const apiNamespace = apiVersion ? `SpaceClaim.Api.${apiVersion}` : "SpaceClaim.Api";
const cache = await ensureExtracted(resolved.path, defaultCacheRoot());
let indexPromise;
async function getIndex() {
  if (!indexPromise) indexPromise = loadOrBuildIndex(cache);
  return indexPromise;
}

function boundedDocPath(docPath) {
  const clean = docPath.replaceAll("\\", "/").split("#")[0];
  const root = path.resolve(cache.root);
  const full = path.resolve(root, clean);
  if (full !== root && !full.startsWith(root + path.sep)) throw new Error("Refused: document path is outside the extracted documentation root.");
  return full;
}

const server = new McpServer({ name: "ansys-scdmdoc", version: "0.1.0" });
server.registerTool("search_api", { title: "Search SpaceClaim API", description: `Search ${apiNamespace} CHM records by type, member, title, keyword, or geometry term.`, inputSchema: { query: z.string().min(1), kind: z.enum(["all", "class", "method", "property", "event"]).optional(), limit: z.number().int().positive().max(100).optional() } }, async ({ query, kind = "all", limit = 25 }) => {
  const index = await getIndex();
  const records = kind === "all" ? index.records : index.records.filter((record) => record.kind === kind);
  const results = rankSearch(records, query, limit);
  return { content: [{ type: "text", text: JSON.stringify({ query, total: results.length, results }, null, 2) }], structuredContent: { query, total: results.length, results } };
});

server.registerTool("get_doc", { title: "Read SpaceClaim API document", description: "Read one relative HTML page returned by search_api.", inputSchema: { docPath: z.string().min(1), maxChars: z.number().int().positive().max(100000).optional() } }, async ({ docPath, maxChars = 50000 }) => {
  try {
    const file = boundedDocPath(docPath);
    const html = await fs.readFile(file, "utf8");
    const { extractHtmlPage } = await import("./lib/extract.mjs");
    const parsed = extractHtmlPage(html, path.relative(cache.root, file).split(path.sep).join("/"));
    const record = (await getIndex()).records.find((item) => path.resolve(cache.root, item.path) === file);
    const text = parsed.text;
    const output = text.length > maxChars ? `${text.slice(0, maxChars)}\n\n...[truncated]` : text;
    return { content: [{ type: "text", text: JSON.stringify({ path: docPath, title: parsed.title || record?.title || "", namespace: parsed.namespace || record?.namespace || "", assembly: parsed.assembly, syntax: parsed.syntax, text: output, truncated: text.length > maxChars }, null, 2) }] };
  } catch (error) {
    return { isError: true, content: [{ type: "text", text: error.code === "ENOENT" ? `Could not read '${docPath}'.` : error.message }] };
  }
});

server.registerTool("find_python_command", { title: "Find SCDM Python command", description: "Find SpaceClaim APIs for a geometry intent and return evidence-labelled Python candidates.", inputSchema: { intent: z.string().min(1), parameters: z.record(z.union([z.string(), z.number(), z.boolean()])).optional(), limit: z.number().int().positive().max(20).optional() } }, async ({ intent, parameters = {}, limit = 5 }) => {
  const result = findPythonCommands(intent, await getIndex(), { parameters, limit });
  return { content: [{ type: "text", text: JSON.stringify(result, null, 2) }], structuredContent: result };
});

server.registerTool("get_geometry_recipe", { title: "Get geometry recipe", description: "Return ordered API-grounded steps and Python candidates for a geometry task.", inputSchema: { task: z.string().min(1), parameters: z.record(z.union([z.string(), z.number(), z.boolean()])).optional() } }, async ({ task, parameters = {} }) => {
  const result = getGeometryRecipe(task, parameters, await getIndex());
  return { content: [{ type: "text", text: JSON.stringify(result, null, 2) }], structuredContent: result };
});

/**
 * Missile tool inputs are derived from lib/missile-schema.mjs so that every declared parameter is
 * accepted here, in the CLI and in params.json without a second definition.
 */
function missileInputShape() {
  const shape = {};
  for (const param of ALL_PARAMS) {
    if (OUTPUT_FIELDS.includes(param.field)) continue;
    const kind = param.kind ?? (typeof param.default === "number" ? "number" : "string");
    shape[param.field] = kind === "integer" ? z.number().int().optional() : kind === "number" ? z.number().optional() : z.string().optional();
  }
  return shape;
}

const MISSILE_INPUT = missileInputShape();

const TOOL_ONLY_KEYS = new Set([...OUTPUT_FIELDS, "outputDir", "fileName", "timeoutMs", "execute"]);

function modelOverrides(args) {
  const overrides = {};
  for (const [key, value] of Object.entries(args)) {
    if (value === undefined || TOOL_ONLY_KEYS.has(key)) continue;
    overrides[key] = value;
  }
  return overrides;
}

server.registerTool("validate_missile_params", { title: "Validate missile parameters", description: "Validate a missile parameter set, returning errors, warnings and automatic corrections without building anything.", inputSchema: MISSILE_INPUT }, async (args) => {
  const request = buildModelRequest({ explicit: modelOverrides(args) });
  const payload = {
    ok: request.ok,
    errors: request.errors,
    warnings: request.warnings,
    corrections: request.corrections,
    effective: { params: request.params, sources: request.sources },
    optimisationPool: optimisationPool(),
    note: "Validation is analytic and needs no ANSYS installation.",
  };
  return { content: [{ type: "text", text: JSON.stringify(payload, null, 2) }], structuredContent: payload };
});

server.registerTool("generate_model_script", { title: "Generate SCDM model script", description: "Write params.json and the SCDM IronPython script for a parameter set without launching SpaceClaim.", inputSchema: { ...MISSILE_INPUT, outputDir: z.string().optional(), fileName: z.string().optional() } }, async (args) => {
  const result = await buildMissile({ explicit: modelOverrides(args), outputDir: args.outputDir ?? "", fileName: args.fileName ?? "missile.scdoc", execute: false });
  if (result.status === "invalid-parameters") {
    const payload = { ok: false, status: result.status, errors: result.errors, warnings: result.warnings, corrections: result.corrections };
    return { content: [{ type: "text", text: JSON.stringify(payload, null, 2) }], structuredContent: payload, isError: true };
  }
  const script = await readFile(result.scriptPath, "utf8");
  const payload = { ok: true, status: result.status, runDir: result.runDir, paramsPath: result.paramsPath, scriptPath: result.scriptPath, modelPath: result.modelPath, executablePath: result.executablePath, executableError: result.executableError, warnings: result.warnings, corrections: result.corrections, script };
  return { content: [{ type: "text", text: JSON.stringify(payload, null, 2) }], structuredContent: payload };
});

server.registerTool("build_missile_model", { title: "Build missile model", description: "Run SpaceClaim headlessly to build the missile model and save a .scdoc, then grade the run against fresh artifacts. With execute:false only params.json and the SCDM script are written and the result is reported as generated-only.", inputSchema: { ...MISSILE_INPUT, outputDir: z.string().optional(), fileName: z.string().optional(), timeoutMs: z.number().int().positive().max(1800000).optional(), execute: z.boolean().optional() } }, async (args) => {
  const execute = args.execute ?? true;
  const result = await buildMissile({ explicit: modelOverrides(args), outputDir: args.outputDir ?? "", fileName: args.fileName ?? "missile.scdoc", timeoutMs: args.timeoutMs ?? 300000, execute });
  const generatedOnly = result.status === "script-generated";
  const payload = {
    ok: result.ok,
    // The run record and the tool report the same wording, so "generated" can
    // never be mistaken for a verified model.
    status: generatedOnly ? "GENERATED" : result.status,
    runDir: result.runDir,
    modelPath: result.modelPath,
    resultPath: result.resultPath,
    acceptance: result.acceptance ?? null,
    errors: result.errors ?? [],
    warnings: result.warnings ?? [],
    corrections: result.corrections ?? [],
    outcome: result.outcome ?? null,
    diagnostic: result.diagnostic ?? "",
    evidence: generatedOnly ? "generated-only: params.json and the SCDM script were written, no model was built and nothing was verified" : result.ok ? "runtime-verified: fresh artifacts checked in the target SpaceClaim installation" : "not verified",
  };
  return { content: [{ type: "text", text: JSON.stringify(payload, null, 2) }], structuredContent: payload, isError: execute && !result.ok ? true : undefined };
});

await server.connect(new StdioServerTransport());
