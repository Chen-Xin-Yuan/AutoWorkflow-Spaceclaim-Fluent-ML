import { resolveChmPath, defaultCacheRoot } from "../lib/paths.mjs";
import { ensureExtracted } from "../lib/chm.mjs";
import { loadOrBuildIndex, rankSearch } from "../lib/index.mjs";
import { findPythonCommands } from "../lib/python-map.mjs";

// === 输入 / 输出 / 参数来源 ===
// 输入：官方 CHM，由 ../lib/paths.mjs 的 resolveChmPath() 按环境变量/本机 setup 解析。
// 输出：JSON 摘要到 stdout；提取/索引缓存位置由 defaultCacheRoot() 解析，应设于仓库外。
// 本入口没有独立文件参数，也不启动 SpaceClaim；下文 Geometry/圆柱/拉伸参数为固定
// 文档查询示例，不覆盖模型 schema 默认值。
const resolved = await resolveChmPath(process.env);
const extracted = await ensureExtracted(resolved.path, defaultCacheRoot());
const index = await loadOrBuildIndex(extracted);
const geometry = rankSearch(index.records, "Geometry", 5);
const cylinder = findPythonCommands("创建圆柱", index, { parameters: { radius: 10, height: 50 } });
const extrusion = findPythonCommands("拉伸", index, { parameters: { depth: 20 } });
console.log(JSON.stringify({ source: resolved, cacheReused: extracted.reused, indexLoadedFromCache: index.loadedFromCache, pages: index.stats.pages, geometry: geometry.map(({ name, title, path, syntax }) => ({ name, title, path, syntax })), cylinder, extrusion }, null, 2));
