# 统一启动、验收、索引与回退入口

脚本从工作区根目录执行，也可以用绝对路径从任意目录执行。对外统一入口是 `run.ps1`。

| 下级 | 类型 | 作用 |
|---|---|---|
| `run.ps1` | 文件 | 唯一入口，模式为 `spaceclaim`、`fluent_meshing`、`verify-spaceclaim`、`verify-meshing`、`spaceclaim-mcp`、`index` |
| `index.mjs` | 文件 | 扫描运行与反馈记录，生成 `data_lib/index.json` |
| `snapshot.ps1` | 文件 | 新建完整快照（含两个仓库的 Git、未跟踪文件、本地配置与依赖），写出文件清单与 SHA-256 并在复制后逐文件复核 |
| `rollback.ps1` | 文件 | 从指定快照恢复旧布局；先验证备份，保留当前的新数据与反馈 |
| `migrate.ps1` | 文件 | 目录布局迁移 |
| `retire-old.ps1` | 文件 | 旧文件下线归档 |
| `find-directory-owner.ps1` | 文件 | 排查占用某个目录的进程 |
| `compare-models.mjs` | 文件 | 模型对比 |
| `README.md` | 文件 | 本文件 |

## 注意

- 除 `spaceclaim-mcp` 与 `index` 模式外，`run.ps1` 结束时会自动调用 `index.mjs` 更新索引，并原样返回被调用命令的退出码。
- `spaceclaim-mcp` 模式下标准输出保留给 JSON-RPC，不打印任何提示，也不接受工作流参数。
- `verify-meshing` 必须显式传入 `-Geometry <新生成的 .scdoc>`，没有默认模型，不会退回历史几何。
- 需要数组开关（例如 `-WorkflowArgs @('-n','8')`）时，改用 `-Command "& '<绝对路径>\scripts\run.ps1' ..."` 调用。
- 本机执行策略禁止运行脚本时，用 `powershell -ExecutionPolicy Bypass -File .\scripts\run.ps1 ...`。
