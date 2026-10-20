# CI 工作流

本目录没有直接子目录。

[`test.yml`](test.yml) 在 `push` 和 `pull_request` 时触发，使用只读 `contents` 权限，在 `windows-latest` 上分别运行 Node.js `18.x`、`20.x`、`22.x`。每个任务通过 `actions/checkout@v4` 和 `actions/setup-node@v4` 检出代码、配置 npm 缓存，然后执行：

```powershell
npm ci
npm test
```

工作流没有配置 ANSYS 安装、官方 CHM 或 SpaceClaim 许可证，也不运行 `npm run smoke`、`npm run verify:real`、`npm run verify:geometry`。这些本机检查的入口和条件见 [`scripts/README.md`](../../scripts/README.md)；测试中的自动跳过条件见 [`tests/README.md`](../../tests/README.md)。CI 通过不构成几何 `runtime-verified` 证据。
