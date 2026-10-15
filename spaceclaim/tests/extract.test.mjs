import assert from "node:assert/strict";
import test from "node:test";
import path from "node:path";
import { extractHtmlPage, parseSitemap, rankSearch } from "../lib/extract.mjs";

test("extracts SpaceClaim metadata and syntax from HTML", () => {
  const page = extractHtmlPage('<title>Geometry.CreateCylinder Method</title><strong>Namespace:</strong><a>SpaceClaim.Api.V261</a><strong>Assembly:</strong> SpaceClaim.Api.V261 (in dll)<pre>public static Body CreateCylinder()</pre>', "html/method.htm");
  assert.equal(page.namespace, "SpaceClaim.Api.V261");
  assert.match(page.syntax, /CreateCylinder/);
  assert.equal(page.path, "html/method.htm");
});

test("parses CHM sitemap records", () => {
  const records = parseSitemap('<OBJECT type="text/sitemap"><param name="Name" value="Geometry Class"><param name="Local" value="html/T_Geometry.htm"></OBJECT>');
  assert.deepEqual(records, [{ name: "Geometry Class", path: path.join("html", "T_Geometry.htm") }]);
});

test("ranks exact and substring API matches", () => {
  const results = rankSearch([{ name: "CreateCylinder", title: "Geometry.CreateCylinder Method", path: "html/a.htm", qualifiedName: "SpaceClaim.Api.V261.Geometry.CreateCylinder", keywords: [] }, { name: "Cylinder", title: "Cylinder Class", path: "html/b.htm", keywords: [] }], "CreateCylinder", 5);
  assert.equal(results[0].name, "CreateCylinder");
});
