import path from "node:path";
import { access, readdir } from "node:fs/promises";
import { chmForSpaceClaimExecutable, chmForAwpVariable, readSetupConfig } from "./setup.mjs";

// 输入：调用方 env；CHM 优先级为 SCDM_CHM > 有效 setup 配置 > AWP_ROOT<版本> > AWP_ROOT。
// 输出：只返回解析后的 CHM/缓存路径，不写文件；根目录仍在调用时从 env 解析。
// 通用 AWP_ROOT 可由 SCDM_API_VERSION 指定 API；缓存根按 LOCALAPPDATA > TEMP > 当前目录。
const CHM_FILENAME = "API_Combined_Class_Library.chm";
const INSTALL_SUBDIRECTORY = "scdm";
const CACHE_APPLICATION_DIRECTORY = "ansys-scdmdoc-mcp";
const CACHE_DIRECTORY_NAME = "cache";

export function inferredChmPath(awpRoot, apiVersion) {
  if (!awpRoot || !apiVersion) return null;
  return path.join(path.resolve(awpRoot), INSTALL_SUBDIRECTORY, `SpaceClaim.Api.${apiVersion}`, CHM_FILENAME);
}

export function apiVersionFromChmPath(chmPath) {
  const match = /SpaceClaim\.Api\.(V\d+)/i.exec(String(chmPath || ""));
  return match ? match[1] : "";
}

async function installedApiVersions(awpRoot) {
  try {
    const entries = await readdir(path.join(path.resolve(awpRoot), INSTALL_SUBDIRECTORY), { withFileTypes: true });
    return entries
      .filter((entry) => entry.isDirectory())
      .map((entry) => /^SpaceClaim\.Api\.(V\d+)$/i.exec(entry.name)?.[1])
      .filter(Boolean)
      .sort((left, right) => Number(right.slice(1)) - Number(left.slice(1)));
  } catch {
    return [];
  }
}

async function genericAwpCandidates(env) {
  const root = env.AWP_ROOT;
  if (!root) return [];
  const explicit = env.SCDM_API_VERSION ? [env.SCDM_API_VERSION.replace(/^v/i, "V")] : [];
  const versions = [...new Set([...explicit, ...(await installedApiVersions(root))])];
  return versions
    .map((apiVersion) => [inferredChmPath(root, apiVersion), env.SCDM_API_VERSION ? "AWP_ROOT+SCDM_API_VERSION" : "AWP_ROOT"])
    .filter(([value]) => Boolean(value));
}

function versionedAwpxCandidates(env) {
  return Object.entries(env)
    .map(([name, value]) => {
      const match = name.match(/^AWP_ROOT(\d+)$/);
      return match && value ? { name, version: Number(match[1]), apiVersion: `V${match[1]}`, root: value } : null;
    })
    .filter(Boolean)
    .sort((left, right) => right.version - left.version)
    .map(({ name, apiVersion, root }) => [inferredChmPath(root, apiVersion), name]);
}

export async function resolveChmPath(env = process.env) {
  const candidates = [
    [env.SCDM_CHM, "SCDM_CHM"],
    ...(await configuredCandidates(env)),
    ...versionedAwpxCandidates(env),
    ...(await genericAwpCandidates(env)),
  ].filter(([value]) => Boolean(value)).map(([value, source]) => ({ path: path.resolve(value), source }));
  for (const candidate of candidates) {
    try {
      await access(candidate.path);
      return candidate;
    } catch {}
  }
  const attempted = candidates.map((candidate) => `${candidate.source}: ${candidate.path}`).join("; ");
  throw new Error(`SpaceClaim API CHM not found. Checked ${attempted || "no configured path"}.`);
}

async function configuredCandidates(env) {
  const config = await readSetupConfig(env);
  if (!config) return [];
  try {
    const chm = config.mode === "manual" ? await chmForSpaceClaimExecutable(config.executablePath) : await chmForAwpVariable(config.awpVariable, env);
    return [[chm, `setup:${config.mode}`]];
  } catch {
    return [];
  }
}

export function defaultCacheRoot(env = process.env) {
  const base = env.LOCALAPPDATA || env.TEMP || ".";
  return path.join(base, CACHE_APPLICATION_DIRECTORY, CACHE_DIRECTORY_NAME);
}
