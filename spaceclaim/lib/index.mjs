import { access, readFile, readdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { extractHtmlPage, parseSitemap, rankSearch } from "./extract.mjs";

// 输入：调用方传入的解包根目录、源 CHM 元数据和可选 sitemapPath。
// 输出：解包根目录中的索引缓存；saveIndex(index, file) 可显式指定文件。
// 以下集中既有文件名；缓存版本与文件名配套，变更时需同时考虑旧缓存兼容。
const DEFAULT_SITEMAP_FILENAME = "API_Combined_Class_Library.hhc";
const INDEX_CACHE_FILENAME = ".scdm-index-v2.json";
const INDEX_CACHE_VERSION = 2;

async function htmlFiles(root, current = root, result = []) {
  for (const entry of await readdir(current, { withFileTypes: true })) {
    const full = path.join(current, entry.name);
    if (entry.isDirectory()) await htmlFiles(root, full, result);
    else if (entry.isFile() && /\.html?$/i.test(entry.name)) result.push(full);
  }
  return result;
}

function pageKind(relativePath) {
  const prefix = path.basename(relativePath).slice(0, 1).toUpperCase();
  return ({ T: "class", M: "method", P: "property", F: "field", E: "event", N: "namespace" })[prefix] || "page";
}

function metadataFromPath(relativePath, sitemapName = "", fallbackVersion = "") {
  const base = path.basename(relativePath, path.extname(relativePath));
  const normalized = base.replace(/^[TMPFEN]_/, "");
  const version = /SpaceClaim_Api_(V\d+)/i.exec(normalized)?.[1] || fallbackVersion;
  const qualifiedName = normalized
    .replace(/_\d+$/, "")
    .replace(/^SpaceClaim_Api_V\d+_/i, version ? `SpaceClaim.Api.${version}.` : "")
    .replaceAll("_", ".");
  return { name: sitemapName || qualifiedName, title: sitemapName || qualifiedName, namespace: version ? `SpaceClaim.Api.${version}` : "", qualifiedName, path: relativePath, kind: pageKind(relativePath), keywords: [qualifiedName] };
}

export function apiVersionFromSourcePath(sourcePath) {
  const match = /SpaceClaim\.Api\.(V\d+)/i.exec(String(sourcePath || ""));
  return match ? match[1] : "";
}

async function readSitemapRecords(extractedRoot, fallbackVersion, sitemapPath = "") {
  const resolvedSitemap = sitemapPath || path.join(extractedRoot, DEFAULT_SITEMAP_FILENAME);
  const sitemap = await readFile(resolvedSitemap, "utf8").catch(() => "");
  const parsed = parseSitemap(sitemap);
  const records = [];
  const seen = new Set();
  for (const item of parsed) {
    const relative = item.path.replaceAll("\\", "/");
    if (!relative.toLowerCase().endsWith(".htm") && !relative.toLowerCase().endsWith(".html")) continue;
    const key = relative.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    records.push(metadataFromPath(relative, item.name, fallbackVersion));
  }
  return records;
}

export async function buildIndex(extractedRoot, options = {}) {
  const fallbackVersion = apiVersionFromSourcePath(options.sourcePath);
  let records = await readSitemapRecords(extractedRoot, fallbackVersion, options.sitemapPath);
  if (!records.length) {
    const files = await htmlFiles(extractedRoot);
    records = files.map((file) => metadataFromPath(path.relative(extractedRoot, file).split(path.sep).join("/"), "", fallbackVersion));
  }
  return { records, stats: { pages: records.length, generatedAt: new Date().toISOString(), source: records.length ? "CHM sitemap metadata" : "extracted HTML filenames", apiVersion: fallbackVersion } };
}

export async function saveIndex(index, file) {
  await writeFile(file, JSON.stringify(index), "utf8");
}

export async function loadOrBuildIndex(extracted, options = {}) {
  const file = path.join(extracted.root, INDEX_CACHE_FILENAME);
  const cacheKey = { sourcePath: extracted.metadata?.sourcePath, size: extracted.metadata?.size, mtimeMs: extracted.metadata?.mtimeMs, version: INDEX_CACHE_VERSION };
  try {
    const saved = JSON.parse(await readFile(file, "utf8"));
    if (JSON.stringify(saved.cacheKey) === JSON.stringify(cacheKey) && Array.isArray(saved.records)) return { ...saved, loadedFromCache: true, indexPath: file };
  } catch {}
  const index = await buildIndex(extracted.root, { sourcePath: extracted.metadata?.sourcePath, sitemapPath: extracted.hhc });
  const saved = { ...index, cacheKey };
  await saveIndex(saved, file);
  return { ...saved, loadedFromCache: false, indexPath: file };
}

export { rankSearch };
