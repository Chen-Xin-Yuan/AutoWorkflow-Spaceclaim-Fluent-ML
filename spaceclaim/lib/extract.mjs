import path from "node:path";

function decodeHtml(value) {
  return value.replace(/&nbsp;/gi, " ").replace(/&lt;/gi, "<").replace(/&gt;/gi, ">")
    .replace(/&amp;/gi, "&").replace(/&quot;/gi, '"').replace(/&#39;/gi, "'");
}

export function textFromHtml(html) {
  return decodeHtml(html.replace(/<script[\s\S]*?<\/script>/gi, " ").replace(/<style[\s\S]*?<\/style>/gi, " ").replace(/<[^>]+>/g, " ").replace(/\s+/g, " ")).trim();
}

function firstMatch(html, pattern) {
  const match = html.match(pattern);
  return match ? textFromHtml(match[1]) : "";
}

export function extractHtmlPage(html, relativePath) {
  const title = firstMatch(html, /<title[^>]*>([\s\S]*?)<\/title>/i);
  const namespace = firstMatch(html, /<strong>Namespace:<\/strong>\s*(?:<a[^>]*>)?([^<]+)(?:<\/a>)?/i);
  const assembly = firstMatch(html, /<strong>Assembly:<\/strong>\s*([^<]+)/i);
  const syntax = firstMatch(html, /<pre[^>]*>([\s\S]*?)<\/pre>/i);
  const metaKeywords = [...html.matchAll(/<meta[^>]+name=["'](?:System\.Keywords|Microsoft\.Help\.F1)["'][^>]+content=["']([^"']*)["']/gi)].map((m) => decodeHtml(m[1]));
  const headings = [...html.matchAll(/<h[1-4][^>]*>([\s\S]*?)<\/h[1-4]>/gi)].map((m) => textFromHtml(m[1])).filter(Boolean);
  const visible = textFromHtml(html);
  const kind = /\b(Class|Interface|Enumeration|Delegate)\b/i.exec(title)?.[1]?.toLowerCase() || (/\b(Method|Property|Field|Event)\b/i.exec(title)?.[1]?.toLowerCase() || "page");
  return { title, namespace, assembly, syntax, keywords: [...new Set([...metaKeywords, ...headings])], text: visible, path: relativePath, kind };
}

export function parseSitemap(text) {
  const records = [];
  const blockRe = /<OBJECT[^>]*>[\s\S]*?<\/OBJECT>/gi;
  for (const block of text.matchAll(blockRe)) {
    const name = block[0].match(/<param[^>]+name=["']Name["'][^>]+value=["']([^"']*)["']/i)?.[1];
    const local = block[0].match(/<param[^>]+name=["']Local["'][^>]+value=["']([^"']*)["']/i)?.[1];
    if (name && local) records.push({ name: decodeHtml(name), path: local.replaceAll("/", path.sep) });
  }
  return records;
}

export function rankSearch(records, query, limit = 25) {
  const q = query.trim().toLowerCase();
  if (!q) return [];
  const normalizedQuery = q.replace(/[._()#,<>]+/g, " ").replace(/\s+/g, " ").trim();
  return records.map((record) => {
    const haystack = [record.name, record.title, record.namespace, record.path, record.qualifiedName, ...(record.keywords || [])].join(" ").toLowerCase();
    const normalizedHaystack = haystack.replace(/[._()#,<>]+/g, " ").replace(/\s+/g, " ");
    const exact = [record.name, record.title].some((value) => value?.toLowerCase() === q);
    const starts = [record.name, record.title].some((value) => value?.toLowerCase().startsWith(q));
    const normalizedExact = [record.qualifiedName, record.name, record.title].some((value) => value?.toLowerCase().replace(/[._()#,<>]+/g, " ").replace(/\s+/g, " ").trim() === normalizedQuery);
    const score = normalizedExact ? 100 : exact ? 95 : starts ? 70 : (haystack.includes(q) || normalizedHaystack.includes(normalizedQuery)) ? 30 : 0;
    return score ? { ...record, score } : null;
  }).filter(Boolean).sort((a, b) => b.score - a.score || a.path.localeCompare(b.path)).slice(0, limit);
}
