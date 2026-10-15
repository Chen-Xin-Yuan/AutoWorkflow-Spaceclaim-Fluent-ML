import { resolveChmPath, defaultCacheRoot } from "../lib/paths.mjs";
import { ensureExtracted } from "../lib/chm.mjs";
import { loadOrBuildIndex, rankSearch } from "../lib/index.mjs";
import { findPythonCommands } from "../lib/python-map.mjs";

const resolved = await resolveChmPath(process.env);
const extracted = await ensureExtracted(resolved.path, defaultCacheRoot());
const index = await loadOrBuildIndex(extracted);
const geometry = rankSearch(index.records, "Geometry", 5);
const cylinder = findPythonCommands("创建圆柱", index, { parameters: { radius: 10, height: 50 } });
const extrusion = findPythonCommands("拉伸", index, { parameters: { depth: 20 } });
console.log(JSON.stringify({ source: resolved, cacheReused: extracted.reused, indexLoadedFromCache: index.loadedFromCache, pages: index.stats.pages, geometry: geometry.map(({ name, title, path, syntax }) => ({ name, title, path, syntax })), cylinder, extrusion }, null, 2));
