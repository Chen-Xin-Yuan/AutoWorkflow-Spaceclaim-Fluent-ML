# 核心模块

本目录没有直接子目录。所有模块使用 Node.js ESM；MCP 入口是仓库根目录的 [`server.mjs`](../server.mjs)，命令入口见 [`scripts/README.md`](../scripts/README.md)。

各模块在导入之后、业务函数之前说明输入、输出和参数来源。需要直接维护的既有文件名、目录名、超时与搜索条数默认值集中在文件前部；由环境、`workspace.json` 或调用方传入的路径继续在调用时解析，保留原来的覆盖优先级。纯函数模块在头部说明不读写文件，并指向已有参数声明，避免重复维护默认值。

| 配置入口 | 输入、输出与修改位置 |
| --- | --- |
| `setup.mjs` / `paths.mjs` | 文件前部集中官方 CHM 文件名、用户配置和缓存目录名；安装根仍通过 AWP 或首次配置发现。用户配置根和缓存根按 `LOCALAPPDATA` → `TEMP` → 当前目录解析。 |
| `chm.mjs` | 调用方提供 CHM 和缓存根；文件前部集中缓存文件名、提取目录前缀、300000 ms 超时、4 次稳定轮询及既有等待间隔，`options.timeoutMs` 可覆盖超时。 |
| `index.mjs` | 调用方提供解包根和可选 sitemap；文件前部集中默认 sitemap 名、索引缓存名与配套格式版本。缓存名与格式版本是兼容性约定，调整时需一并核对。 |
| `workspace-runtime.mjs` | 文件前部集中 `workspace.json` 名、默认数据根、`runs/high/<stage>` 子目录、`request.json` / `run.json` 与运行 ID 的安全字符规则；配置内容覆盖默认根目录，显式 `outputDir` 覆盖默认产物根。运行目录是 **`data_lib/runs/high/<stage>/<run_id>/`**：`runId` 由调用方给出（数据库的三段式 `<geometry_id>__<condition_id>__<时间戳>`），没给时退回 `run-<时间戳>-<随机短串>`。最终绝对路径在调用时生成。导弹建模成功后，`spaceclaim-runner.mjs` 再删掉成功目录里 `params.json`、`model.py`、`result.json`、`diagnostic.txt`、`run.json` 和模型文件以外的文件。 |
| `spaceclaim-runner.mjs` | 文件前部集中模型/请求/脚本/结果/诊断/图片/清单/错误文件名、300000 ms 超时与原有执行默认值；模型名可由 `fileName` 覆盖。输出根按 `options.outputDir` → `SCDM_MISSILE_OUTPUT` → 工作区 `dataRoot` 选择。清单名是与 `workspace-runtime.mjs` 的 `RUN_MANIFEST_FILENAME` 共享的协议名，必须保持一致。 |
| `extract.mjs` / `python-map.mjs` | 不读写文件；文件前部集中原有搜索条数默认值 25 / 5，由函数参数或 `options.limit` 覆盖。候选模板的参数仍在前部 `INTENTS` 中。 |
| `missile-schema.mjs` 及参数/几何/模板模块 | 参数默认值及输出目录/文件名仍以 schema 前部数组为准，按显式值 → 预设 → 默认值合并。翼型截面由几何模块按 `fin_section` 计算：`naca` 用 NACA 三参数，`hexagon` 用厚度和等厚段起止。六边形肩部和后缘半径 `HEX_FILLET_RADIUS_M`（0.001 m）、前缘半径 `HEX_FILLET_LE_RADIUS_M`（0.0018 m）和 ±10% 验收带在 `missile-geometry.mjs` 前部，不是 schema 字段。前舵另按斜边半角 `atan((thickness_percent/100)/(2*hex_flat_start))` 转过，默认 6.390°。每个尖角是一条相切圆弧，模板用与弹身相同的 `SketchArc.CreateSweepArc` 绘制。过短的圆弧按 `sketchScale` 放大后再绕原点缩回，并要求每个剖面草图都新建实体。六边形舵梢部保持设计梯形的尖角，几何模块不生成平面圆弧、模板也不切角，因此梢弦是完整的设计梢弦；前舵和后舵的根部交接各自用模板里的 `ConstantRound`。合并后模板先跑修复：`FixInexactEdges` 清除非精确边（默认模型 28 处全清，面数边数不变），再用 `FixSmallFaces.FixSpecific` 逐个试修舵根小面。选面时**排除所有圆柱面**（`CylinderDiameter` 有效的面一律跳过），因为前舵根部 1.5 mm 圆角由 48 条约 0.094 mm² 的细条组成，按面积删除会把整个圆角抹掉（实测面数 205→197、1.5 mm 圆角面 8→0）；同时限制在 `REPAIR_SMALL_FACE_MAX_RADIUS_M` 以内，避免在舵尖把梢部外推。修复后逐级核对圆角面数（3.0/1.5/1.0 mm 必须不变），保存前再用 `audit_geometry` 对即将写入文件的实体复查非精确边与圆角，因为命名选择和组件清理可能重新引入非精确边。体积与表面积变化超过 `REPAIR_MAX_VOLUME_CHANGE` / `REPAIR_MAX_SURFACE_CHANGE`（各 0.5%）即判失败。修复记录写入 `result.json` 的 `diagnostics.repair` 与 `diagnostics.finalGeometry`。面积单位换算是 `raw / (MM(1.0) * MM_PER_M) ** 2`，不能写成 `to_metres(area) ** 2`，后者会把 1.81e-7 m² 算成 3.28e-14 m² 并凭空造出退化面。后舵为 `HEX_ROOT_JUNCTION_RADIUS_M`（0.003 m）。前舵先试该半径；默认 `hex_flat_end = 0.823` 时接缝直接接受 3 mm，若被拒绝则在 `HEX_ROOT_JUNCTION_FORWARD_MIN_RADIUS_M`（0.0015 m，即 1.5 mm）以上取内核接受的最大半径，并在成功后写入 `params.json`。`hex_flat_end` 不能改回 0.8：实测低于约 0.822 时前舵接缝拒绝 3 mm、圆角被压到 1.5 mm，未倒圆的残余带会在合并后形成 24 个非圆角碎面（0.18–0.98 mm²），这些面无法删除。接缝按“边中点落在弹体柱面上”（`HEX_ROOT_JUNCTION_SEAM_TOLERANCE_M`，0.001 m）选边，不依赖相邻面的质心半径，每个站位整圈一次倒圆、不做逐边降半径；把短边筛掉会在切向接点留下自由端，内核连 0.2 mm 以上都拒绝。两者都在几何模块前部。 |
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
| [`missile-geometry.mjs`](missile-geometry.mjs) | 计算鼻锥与倒圆剖面、梯形舵面平面形状、NACA 或带绝对圆角的六边形横截面、站位、体积/面积/质心、包围盒、BOI 与外壳的预期几何。 |
| [`missile-template.mjs`](missile-template.mjs) | 组装请求 JSON 并渲染目标 API 版本的 SpaceClaim Python；模板读取参数、建模、合并、创建区域和命名选择、保存模型并报告结果。预览导出失败不单独判定几何失败。 |
| [`spaceclaim-runner.mjs`](spaceclaim-runner.mjs) | 解析 SpaceClaim 与 API 版本，生成脚本/参数，可选启动 SpaceClaim，并依据新模型与结构化结果检查几何、区域、选择集及结构树处理。 |
| [`fixture-acceptance.mjs`](fixture-acceptance.mjs) | 解析圆周阵列 fixture 的 `key=value` 结果，检查六个角度、360° 覆盖、单一合并实体及非零面数。 |
| [`workspace-runtime.mjs`](workspace-runtime.mjs) | 相对项目定位上级 `workspace.json`，创建唯一运行目录和清单、计算产物哈希、保留失败产物，并拒绝受保护项目路径。 |

`server.mjs` 启动时先解析并解包官方 CHM，首次需要索引时再加载或构建索引，因此通过 MCP 调用参数校验或仅生成脚本的工具也需要可用 CHM。独立 CLI 的 `--generate-only` 可以在没有 ANSYS 安装时生成请求与脚本；这只产生 `script-generated` / 运行清单 `GENERATED` 状态，不表示模型已生成或几何已验证。

默认运行产物写入工作区配置的 `data_lib/runs/high/spaceclaim/<run_id>/`，失败时尝试移至 `feedback/spaceclaim/<run_id>/`；文件系统或权限错误可能中断移动及清单收尾。调用方可选择仓库外输出位置。用户配置与 CHM 缓存也不应提交到仓库。`official-doc` 只表示官方文档来源，Python 推导候选标为 `inferred`；只有当前运行的新产物通过验收后，才能标为 `runtime-verified`。
