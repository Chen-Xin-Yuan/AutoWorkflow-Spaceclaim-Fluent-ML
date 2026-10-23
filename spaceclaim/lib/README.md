# 核心模块

本目录没有直接子目录。所有模块使用 Node.js ESM；MCP 入口是仓库根目录的 [`server.mjs`](../server.mjs)，命令入口见 [`scripts/README.md`](../scripts/README.md)。

各模块在导入之后、业务函数之前说明输入、输出和参数来源。需要直接维护的既有文件名、目录名、超时与搜索条数默认值集中在文件前部；由环境、`workspace.json` 或调用方传入的路径继续在调用时解析，保留原来的覆盖优先级。纯函数模块在头部说明不读写文件，并指向已有参数声明，避免重复维护默认值。

| 配置入口 | 输入、输出与修改位置 |
| --- | --- |
| `setup.mjs` / `paths.mjs` | 文件前部集中官方 CHM 文件名、用户配置和缓存目录名；安装根仍通过 AWP 或首次配置发现。用户配置根和缓存根按 `LOCALAPPDATA` → `TEMP` → 当前目录解析。 |
| `chm.mjs` | 调用方提供 CHM 和缓存根；文件前部集中缓存文件名、提取目录前缀、300000 ms 超时、4 次稳定轮询及既有等待间隔，`options.timeoutMs` 可覆盖超时。 |
| `index.mjs` | 调用方提供解包根和可选 sitemap；文件前部集中默认 sitemap 名、索引缓存名与配套格式版本。缓存名与格式版本是兼容性约定，调整时需一并核对。 |
| `workspace-runtime.mjs` | 文件前部集中 `workspace.json` 名、默认数据根、`runs/high` 子目录和 `request.json` / `run.json`；配置内容覆盖默认根目录，显式 `outputDir` 覆盖默认产物根。运行编号与最终绝对路径在调用时生成。 |
| `spaceclaim-runner.mjs` | 文件前部集中模型/请求/脚本/结果/诊断/图片/清单/错误文件名、300000 ms 超时与原有执行默认值；模型名可由 `fileName` 覆盖。输出根按 `options.outputDir` → `SCDM_MISSILE_OUTPUT` → 工作区 `dataRoot` 选择。清单名是与 `workspace-runtime.mjs` 的 `RUN_MANIFEST_FILENAME` 共享的协议名，必须保持一致。 |
| `extract.mjs` / `python-map.mjs` | 不读写文件；文件前部集中原有搜索条数默认值 25 / 5，由函数参数或 `options.limit` 覆盖。候选模板的参数仍在前部 `INTENTS` 中。 |
| `missile-schema.mjs` 及参数/几何/模板模块 | 参数默认值及输出目录/文件名仍以 schema 前部数组为准，按显式值 → 预设 → 默认值合并。生成模板的路径由请求/环境传入，模板内容与数值计算未更改。 |
| `fixture-acceptance.mjs` | 不读写文件；头部说明 fixture 的既有验收约定，不将契约常量重新声明为运行参数。 |

这些整理保留已有默认值、文件格式、调用接口与验收逻辑。可用 `node --check lib/<module>.mjs` 做语法检查，正常代码检查仍按仓库 `AGENTS.md` 执行；语法检查或离线测试不能证明 ANSYS 运行产物已经验收。

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

默认运行产物写入工作区配置的 `data_lib/runs/high/<run-id>/spaceclaim/`，失败时尝试移至 `feedback/spaceclaim/<run-id>/`；文件系统或权限错误可能中断移动及清单收尾。调用方可选择仓库外输出位置。用户配置与 CHM 缓存也不应提交到仓库。`official-doc` 只表示官方文档来源，Python 推导候选标为 `inferred`；只有当前运行的新产物通过验收后，才能标为 `runtime-verified`。
