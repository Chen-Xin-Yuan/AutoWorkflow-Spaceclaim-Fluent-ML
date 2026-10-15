import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { access, cp, mkdir, readdir, readFile, rm, stat, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

const DEFAULT_TIMEOUT_MS = 300000;
const STABLE_POLLS = 4;
/**
 * hh.exe takes the output folder as the first token after -decompile and treats the rest of the
 * raw command line, quotes included, as the CHM path. So the folder must be free of spaces and cmd
 * metacharacters, while the CHM path may contain spaces but must be passed unquoted.
 */
const CMD_SAFE = /^[A-Za-z0-9\\._:\-]+$/;
const CMD_TAIL_UNSAFE = /[&^<>|()%!"\r\n]/;

async function exists(file) {
  try { await access(file); return true; } catch { return false; }
}

async function resolveHhExe() {
  const root = process.env.WINDIR || process.env.SystemRoot;
  if (!root) throw new Error("WINDIR environment variable is not configured.");
  const exe = path.join(root, "hh.exe");
  if (!(await exists(exe))) throw new Error(`Windows HTML Help executable was not found at '${exe}'.`);
  return exe;
}

function runCommand(commandLine, timeoutMs) {
  return new Promise((resolve, reject) => {
    const child = spawn("cmd.exe", ["/c", commandLine], { windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
    let output = "";
    child.stdout.on("data", (chunk) => { output += chunk.toString(); });
    child.stderr.on("data", (chunk) => { output += chunk.toString(); });
    let settled = false;
    const finish = (error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (error) reject(error);
      else resolve(output);
    };
    const timer = setTimeout(() => {
      child.kill();
      finish(new Error(`hh.exe did not finish within ${timeoutMs} ms.`));
    }, timeoutMs);
    child.once("error", (error) => finish(new Error(`could not start cmd.exe: ${error.message}`)));
    child.once("close", (code) => finish(code === 0 ? null : new Error(`hh.exe failed with exit code ${code}: ${output.trim() || commandLine}`)));
  });
}

function runCapture(commandLine) {
  return new Promise((resolve) => {
    const child = spawn("cmd.exe", ["/c", commandLine], { windowsHide: true });
    let out = "";
    child.stdout.on("data", (chunk) => { out += chunk.toString(); });
    child.once("error", () => resolve(""));
    child.once("close", () => resolve(out.trim()));
  });
}

/** 8.3 short form, used only when a path contains spaces or cmd metacharacters. */
async function shortPath(target) {
  const captured = await runCapture(`for %I in ("${target}") do @echo %~sI`);
  const candidate = captured.split(/\r?\n/).pop()?.trim() || "";
  return CMD_SAFE.test(candidate) ? candidate : "";
}

async function spaceFreeBase() {
  const candidates = [os.tmpdir(), process.env.TEMP, process.env.WINDIR ? path.join(process.env.WINDIR, "Temp") : ""];
  for (const candidate of candidates) {
    if (!candidate) continue;
    if (CMD_SAFE.test(candidate)) return candidate;
    const shortened = await shortPath(candidate);
    if (shortened) return shortened;
  }
  return "";
}

async function resolveDecompileTargets(chmPath, outputRoot) {
  let chmArgument = chmPath;
  if (CMD_TAIL_UNSAFE.test(chmPath)) {
    chmArgument = await shortPath(chmPath);
    if (!chmArgument) throw new Error(`The CHM path contains cmd metacharacters that hh.exe cannot accept: '${chmPath}'. Point SCDM_CHM at a copy without them.`);
  }
  if (CMD_SAFE.test(outputRoot)) return { chmArgument, decompileInto: outputRoot, moveTo: null };
  const shortened = await shortPath(outputRoot);
  if (shortened) return { chmArgument, decompileInto: shortened, moveTo: null };
  const base = await spaceFreeBase();
  if (!base) throw new Error("No space-free temporary directory is available for hh.exe; point SCDM_CHM at a path without spaces or special characters.");
  return { chmArgument, decompileInto: path.join(base, `scdm-extract-${Date.now().toString(36)}`), moveTo: outputRoot };
}

/** hh.exe must see the executable unquoted as well; quoting it makes cmd pass the quotes through. */
async function executableArgument(exe) {
  if (CMD_SAFE.test(exe)) return exe;
  const shortened = await shortPath(exe);
  if (shortened) return shortened;
  throw new Error(`Windows HTML Help path contains characters that cannot be passed to hh.exe: '${exe}'`);
}

async function countHtmlFiles(dir) {
  let total = 0;
  const pending = [dir];
  while (pending.length) {
    const current = pending.pop();
    let entries;
    try { entries = await readdir(current, { withFileTypes: true }); } catch { continue; }
    for (const entry of entries) {
      if (entry.isDirectory()) pending.push(path.join(current, entry.name));
      else if (entry.isFile() && /\.html?$/i.test(entry.name)) total += 1;
    }
  }
  return total;
}

async function findByExtension(root, extension) {
  const entries = await readdir(root, { withFileTypes: true }).catch(() => []);
  const match = entries.find((entry) => entry.isFile() && entry.name.toLowerCase().endsWith(extension));
  return match ? path.join(root, match.name) : null;
}

async function waitForExtraction(outputRoot, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  let lastCount = -1;
  let stable = 0;
  while (Date.now() < deadline) {
    const hhc = await findByExtension(outputRoot, ".hhc");
    const hhk = await findByExtension(outputRoot, ".hhk");
    if (hhc && hhk) {
      const count = await countHtmlFiles(path.join(outputRoot, "html"));
      if (count > 0 && count === lastCount) {
        stable += 1;
        if (stable >= STABLE_POLLS) return count;
      } else {
        stable = 0;
        lastCount = count;
      }
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`hh.exe did not produce a .hhc/.hhk pair and extracted HTML within ${timeoutMs} ms.`);
}

function cacheName(chmPath) {
  return createHash("sha1").update(path.resolve(chmPath)).digest("hex").slice(0, 16);
}

export async function ensureExtracted(chmPath, cacheRoot, options = {}) {
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const source = await stat(chmPath);
  const resolvedChm = path.resolve(chmPath);
  const outputRoot = path.join(cacheRoot, `extracted-${cacheName(resolvedChm)}`);
  const metadataPath = path.join(outputRoot, ".scdm-cache.json");
  await mkdir(cacheRoot, { recursive: true });
  let metadata = null;
  try { metadata = JSON.parse(await readFile(metadataPath, "utf8")); } catch {}
  const unchanged = metadata && metadata.sourcePath === resolvedChm && metadata.size === source.size && metadata.mtimeMs === source.mtimeMs;
  const valid = unchanged && await findByExtension(outputRoot, ".hhc") && await findByExtension(outputRoot, ".hhk");
  if (!valid) {
    const exe = await executableArgument(await resolveHhExe());
    const { chmArgument, decompileInto, moveTo } = await resolveDecompileTargets(resolvedChm, outputRoot);
    await mkdir(decompileInto, { recursive: true });
    const started = Date.now();
    await runCommand(`${exe} -decompile ${decompileInto} ${chmArgument}`, timeoutMs);
    if (moveTo) {
      await mkdir(moveTo, { recursive: true });
      await cp(decompileInto, moveTo, { recursive: true });
      await rm(decompileInto, { recursive: true, force: true });
    }
    if (Date.now() - started < 3000 && !(await findByExtension(outputRoot, ".hhc"))) {
      // hh.exe silently produces nothing when it cannot parse the request; do not spin for minutes.
      await new Promise((resolve) => setTimeout(resolve, 5000));
      if (!(await findByExtension(outputRoot, ".hhc"))) {
        throw new Error(`hh.exe exited without extracting anything. The CHM path '${resolvedChm}' or the cache folder '${cacheRoot}' is not usable by hh.exe; check for spaces and special characters.`);
      }
    }
    const pages = await waitForExtraction(outputRoot, timeoutMs);
    await writeFile(metadataPath, JSON.stringify({ sourcePath: resolvedChm, size: source.size, mtimeMs: source.mtimeMs, pages, extractedAt: new Date().toISOString() }, null, 2));
    metadata = JSON.parse(await readFile(metadataPath, "utf8"));
  }
  return {
    root: outputRoot,
    hhc: await findByExtension(outputRoot, ".hhc"),
    hhk: await findByExtension(outputRoot, ".hhk"),
    reused: Boolean(valid),
    metadata,
  };
}
