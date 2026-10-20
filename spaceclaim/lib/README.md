# 核心模块

本目录没有直接子目录。所有模块使用 Node.js ESM；MCP 入口是仓库根目录的 [`server.mjs`](../server.mjs)，命令入口见 [`scripts/README.md`](../scripts/README.md)。

| 模块 | 当前职责 |
| --- | --- |
| [`setup.mjs`](setup.mjs) | 枚举版本化 AWP 环境变量、校验 AWP 或手动选择的 CHM、读写用户级首次配置。 |
| [`paths.mjs`](paths.mjs) | 按 `SCDM_CHM`、有效首次配置、版本化 AWP、通用 AWP 的顺序解析 CHM，并确定用户缓存目录。 |
| [`chm.mjs`](chm.mjs) | 调用 Windows HTML Help 解包本机 CHM，等待提取完成，按源文件元数据复用仓库外缓存。 |
| [`extract.mjs`](extract.mjs) | 从 HTML 提取标题、签名和正文，解析 CHM sitemap，并对 API 名称与关键词评分。 |
| [`index.mjs`](index.mjs) | 优先从 sitemap 元数据建立 API 索引，必要时扫描提取后的 HTML 文件名；缓存索引并校验缓存键。 |
| [`python-map.mjs`](python-map.mjs) | 将圆周阵列、圆柱、拉伸等意图映射为 API 搜索结果及 `inferred` Python 候选；组合几何步骤配方。 |
| [`missile-schema.mjs`](missile-schema.mjs) | 参数字段、单位、默认值、可用约定与优化区间的统一声明，供 MCP、CLI 和请求文件派生使用。 |
| [`missile-params.mjs`](missile-params.mjs) | 按显式参数 → 预设 → 默认值合并，转换类型、检查几何与区域约束，生成错误、警告、修正和模型请求。 |
| [`missile-geometry.mjs`](missile-geometry.mjs) | 计算鼻锥与倒圆剖面、舵面轮廓和站位、体积/面积/质心、包围盒、BOI 与外壳的预期几何。 |
| [`missile-template.mjs`](missile-template.mjs) | 组装请求 JSON 并渲染目标 API 版本的 SpaceClaim Python；模板读取参数、建模、合并、创建区域和命名选择、保存模型并报告结果。预览导出失败不单独判定几何失败。 |
| [`spaceclaim-runner.mjs`](spaceclaim-runner.mjs) | 解析 SpaceClaim 与 API 版本，生成脚本/参数，可选启动 SpaceClaim，并依据新模型与结构化结果检查几何、区域、选择集及结构树处理。 |
| [`fixture-acceptance.mjs`](fixture-acceptance.mjs) | 解析圆周阵列 fixture 的 `key=value` 结果，检查六个角度、360° 覆盖、单一合并实体及非零面数。 |
| [`workspace-runtime.mjs`](workspace-runtime.mjs) | 相对项目定位上级 `workspace.json`，创建唯一运行目录和清单、计算产物哈希、保留失败产物，并拒绝受保护项目路径。 |

`server.mjs` 启动时先解析并解包官方 CHM，首次需要索引时再加载或构建索引，因此通过 MCP 调用参数校验或仅生成脚本的工具也需要可用 CHM。独立 CLI 的 `--generate-only` 可以在没有 ANSYS 安装时生成请求与脚本；这只产生 `script-generated` / 运行清单 `GENERATED` 状态，不表示模型已生成或几何已验证。

默认运行产物写入工作区配置的 `data_lib/runs/<run-id>/spaceclaim/`，失败时尝试移至 `feedback/spaceclaim/<run-id>/`；文件系统或权限错误可能中断移动及清单收尾。调用方可选择仓库外输出位置。用户配置与 CHM 缓存也不应提交到仓库。`official-doc` 只表示官方文档来源，Python 推导候选标为 `inferred`；只有当前运行的新产物通过验收后，才能标为 `runtime-verified`。
