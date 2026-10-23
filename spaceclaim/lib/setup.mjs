import { access, readdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";

// 输入：调用方选择的 SpaceClaim.exe / AWP_ROOT<版本>，以及调用时传入的 env。
// 输出：LOCALAPPDATA > TEMP > 当前目录下的用户配置；安装位置始终动态发现。
// 以下为既有文件/目录名；CHM_NAME 必须与官方安装的文档名称一致。
const CHM_NAME = "API_Combined_Class_Library.chm";
const INSTALL_SUBDIRECTORY = "scdm";
const CONFIG_APPLICATION_DIRECTORY = "ansys-scdmdoc-mcp";
const CONFIG_FILENAME = "config.json";

export function discoverAwpRoots(env = process.env) {
  return Object.entries(env)
    .map(([name, value]) => {
      const match = name.match(/^AWP_ROOT(\d+)$/);
      return match && value ? { name, version: Number(match[1]), apiVersion: `V${match[1]}`, root: path.resolve(value) } : null;
    })
    .filter(Boolean)
    .sort((left, right) => right.version - left.version);
}

export function defaultConfigPath(env = process.env) {
  const base = env.LOCALAPPDATA || env.TEMP || ".";
  return path.join(base, CONFIG_APPLICATION_DIRECTORY, CONFIG_FILENAME);
}

export async function readSetupConfig(env = process.env) {
  try {
    return JSON.parse(await readFile(defaultConfigPath(env), "utf8"));
  } catch {
    return null;
  }
}

export async function saveSetupConfig(config, env = process.env) {
  const file = defaultConfigPath(env);
  const fs = await import("node:fs/promises");
  await fs.mkdir(path.dirname(file), { recursive: true });
  await writeFile(file, `${JSON.stringify({ ...config, savedAt: new Date().toISOString() }, null, 2)}\n`, "utf8");
  return file;
}

export async function chmForSpaceClaimExecutable(executablePath) {
  const executable = path.resolve(executablePath || "");
  await access(executable);
  const scdmRoot = path.dirname(executable);
  const entries = await readdir(scdmRoot, { withFileTypes: true });
  const candidates = entries
    .filter((entry) => entry.isDirectory() && /^SpaceClaim\.Api\.V\d+$/i.test(entry.name))
    .sort((left, right) => Number(right.name.match(/V(\d+)$/i)[1]) - Number(left.name.match(/V(\d+)$/i)[1]))
    .map((entry) => path.join(scdmRoot, entry.name, CHM_NAME));
  for (const candidate of candidates) {
    try {
      await access(candidate);
      return path.resolve(candidate);
    } catch {}
  }
  throw new Error("无法从 SpaceClaim 的应用程序路径找到 API_Combined_Class_Library.chm。");
}

export async function chmForAwpVariable(variable, env = process.env) {
  const item = discoverAwpRoots(env).find((candidate) => candidate.name === variable);
  if (!item) throw new Error("无法根据 AWP 确定环境变量的路径，无法确定 SpaceClaim 的路径");
  const chm = path.join(item.root, INSTALL_SUBDIRECTORY, `SpaceClaim.Api.${item.apiVersion}`, CHM_NAME);
  try {
    await access(chm);
    return path.resolve(chm);
  } catch {
    throw new Error("无法根据 AWP 确定环境变量的路径，无法确定 SpaceClaim 的路径");
  }
}

export async function resolveSetupSelection(selection, env = process.env) {
  if (selection?.mode === "awp_root") {
    const chmPath = await chmForAwpVariable(selection.awpVariable, env);
    return { mode: "awp_root", awpVariable: selection.awpVariable, chmPath };
  }
  if (selection?.mode === "manual") {
    const executablePath = path.resolve(selection.executablePath || "");
    if (!path.basename(executablePath).toLowerCase().endsWith(".exe")) throw new Error("请输入 SpaceClaim 的应用程序路径（SpaceClaim.exe）。");
    const chmPath = await chmForSpaceClaimExecutable(executablePath);
    return { mode: "manual", executablePath, chmPath };
  }
  throw new Error("请选择根据 AWP_ROOT 自动检测，或手动输入 SpaceClaim 的应用程序路径。");
}
