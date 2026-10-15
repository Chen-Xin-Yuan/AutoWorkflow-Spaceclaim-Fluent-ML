#!/usr/bin/env node
import http from "node:http";
import { execFile } from "node:child_process";
import { discoverAwpRoots, resolveSetupSelection, saveSetupConfig } from "../lib/setup.mjs";

const awpRoots = discoverAwpRoots(process.env);
const port = 0;

function page(message = "", forceManual = false) {
  const options = awpRoots.map((item) => `<option value="${item.name}">${item.name} → ${item.root} (V${item.version})</option>`).join("");
  return `<!doctype html><meta charset="utf-8"><title>ANSYS SCDM MCP 首次配置</title>
<style>body{font:16px system-ui;max-width:760px;margin:40px auto;padding:0 20px}fieldset{margin:18px 0;padding:18px;border:1px solid #bbb;border-radius:8px}label{display:block;margin:10px 0}input,select,button{font:inherit;padding:8px;width:100%;box-sizing:border-box}button{margin-top:12px;cursor:pointer}.error{color:#b00020;background:#fff0f0;padding:12px;border-radius:6px}.ok{color:#075e2b;background:#eefbf2;padding:12px;border-radius:6px}.disabled{opacity:.5}</style>
<h1>ANSYS SCDM MCP 首次配置</h1><p>请选择 SpaceClaim API 文档的来源。</p>
${message ? `<div class="error">${message}</div>` : ""}
<fieldset class="${forceManual ? "disabled" : ""}"><legend>根据 AWP_ROOT 自动检测</legend>
<form method="post" action="/configure"><input type="hidden" name="mode" value="awp_root"><label>检测到的 AWP_ROOT 环境变量<select name="awpVariable" ${forceManual || !awpRoots.length ? "disabled" : "required"}>${options || "<option>未检测到 AWP_ROOT<number></option>"}</select></label><button ${forceManual || !awpRoots.length ? "disabled" : ""}>确认并检测 SpaceClaim 路径</button></form></fieldset>
<fieldset><legend>手动输入 SpaceClaim 应用程序路径</legend><form method="post" action="/configure"><input type="hidden" name="mode" value="manual"><label>SpaceClaim.exe 路径<input name="executablePath" placeholder="例如：...\\scdm\\SpaceClaim.exe" required></label><button>确认并保存</button></form></fieldset>
${forceManual ? "<p>自动检测失败，当前只能手动输入 SpaceClaim 的应用程序路径。</p>" : ""}`;
}

const server = http.createServer(async (request, response) => {
  if (request.method === "GET") {
    response.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    response.end(page());
    return;
  }
  if (request.method !== "POST" || request.url !== "/configure") {
    response.writeHead(404); response.end("Not found"); return;
  }
  let body = "";
  for await (const chunk of request) body += chunk;
  const form = Object.fromEntries(new URLSearchParams(body));
  try {
    const config = await resolveSetupSelection(form, process.env);
    const configPath = await saveSetupConfig(config, process.env);
    response.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    response.end(page(`已找到并保存 API 文档路径：<code>${config.chmPath}</code><br>配置文件：<code>${configPath}</code>`, false).replace("<div class=\"error\">", "<div class=\"ok\">").replace("<h1>", "<h1>配置完成："));
    setTimeout(() => server.close(() => process.exit(0)), 300);
  } catch (error) {
    response.writeHead(400, { "content-type": "text/html; charset=utf-8" });
    response.end(page(error.message, form.mode === "awp_root"));
  }
});

server.listen(port, "127.0.0.1", () => {
  const address = server.address();
  const url = `http://127.0.0.1:${address.port}/`;
  console.error(`Opening setup page: ${url}`);
  execFile("rundll32.exe", ["url.dll,FileProtocolHandler", url], (error) => {
    if (error) console.error(`Open this URL in a browser: ${url}`);
  });
});
