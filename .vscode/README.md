# 工作区编辑器配置

| 下级 | 类型 | 作用 |
|---|---|---|
| `settings.json` | 文件 | 编辑器与 Python 语言服务设置 |
| `README.md` | 文件 | 本文件 |

`settings.json` 当前只包含本机绝对路径。按工作区规则，安装路径不应写入源码与报告；这里保留现状仅作说明，**不要**把其中的绝对路径复制到其他文件，也不要让它替代 `workspace.json` 的路径约定。

`spaceclaim/` 与 `fluent_meshing/` 各自还有自己的 `.vscode/` 配置，与该目录无关。
