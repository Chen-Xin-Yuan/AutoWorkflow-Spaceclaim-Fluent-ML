# 脚本与本机检查

本目录没有直接子目录。以下命令从仓库根目录运行；需要 Node.js 18+，真实 CHM 与 SpaceClaim 检查面向 Windows。默认运行根目录相对项目和工作区配置解析；显式传入的相对 `--out` 和 `--preset` 路径则相对调用者当前目录解析。

| 文件 / npm 命令 | 当前行为与依赖 |
| --- | --- |
| [`setup.mjs`](setup.mjs) / `npm run setup` | 打开 `127.0.0.1` 临时端口的首次配置页，支持选择非空 `AWP_ROOT<number>` 或手动指定 `SpaceClaim.exe`。验证 CHM 后保存用户级配置；不建模。 |
| [`smoke.mjs`](smoke.mjs) / `npm run smoke` | 启动 stdio MCP，检查七个工具、API 搜索、真实文档读取、越界路径拒绝、Python 候选、几何配方和参数校验；不启动 SpaceClaim。 |
| [`verify-real.mjs`](verify-real.mjs) / `npm run verify:real` | 解析、解包并索引真实 CHM，输出索引信息、Geometry 搜索和圆柱/拉伸候选；不启动 SpaceClaim。 |
| [`verify-geometry.mjs`](verify-geometry.mjs) / `npm run verify:geometry` | 启动本机 SpaceClaim 执行圆周阵列 fixture，检查新 `.scdoc`、新结果文件、结构化几何断言及进程完成情况。 |
| [`spaceclaim-circular-repeat-smoke.py`](spaceclaim-circular-repeat-smoke.py) | 由 `verify:geometry` 加入安装对应的 API 版本头后在 SpaceClaim 内执行；不是普通 CPython 脚本。 |
| [`missile.mjs`](missile.mjs) / `npm run missile -- …` | 合并和校验参数，生成请求与建模脚本，可选执行 SpaceClaim，并核查模型与结果。 |

`smoke` 的入口预检要求 `SCDM_CHM`、非空版本化 `AWP_ROOT<number>` 或通用 `AWP_ROOT` 至少存在一个；仅有 `npm run setup` 保存的配置仍不能通过该预检。`verify:real` 使用统一 CHM 解析器，可以使用有效的首次配置。两者的 CHM 成功、缓存命中和 API 签名只证明文档路径与查询功能，不是几何 `runtime-verified`。

## 文件前部的集中修改区

各入口在必要的模块说明/import 后先说明输入、输出和参数来源，默认值保留原值；路径仍按原有覆盖优先级在运行时解析。

| 入口 | 文件前部的配置或实际来源 |
| --- | --- |
| `missile.mjs` | `DEFAULT_OPTIONS` 集中 `preset`、`outputDir`、`fileName`、`execute`、`timeoutMs` 与显示选项。CLI 覆盖这些默认值；模型字段仍来自 `../lib/missile-schema.mjs`。 |
| `setup.mjs` | `SETUP_HOST`、`SETUP_PORT`、`CLOSE_DELAY_MS` 集中本机页面设置；配置文件位置由共享 `defaultConfigPath()` 依据 `LOCALAPPDATA` / `TEMP` 解析，不复制安装路径。 |
| `verify-geometry.mjs` | `DEFAULT_OUTPUT_DIR`、`DEFAULT_TIMEOUT_MS`、`MAX_TIMEOUT_MS`、`FIXTURE_SOURCE_PARTS` 和 `OUTPUT_FILES` 集中 fixture 输入、超时及运行目录内文件名；`--out`、`--timeout` 仍优先。 |
| `spaceclaim-circular-repeat-smoke.py` | 环境变量名称和诊断缺省文件名在前部；实际模型/结果/诊断路径仍由驱动脚本注入。固定几何验收数值和断言保持原样。 |
| `smoke.mjs` / `verify-real.mjs` | 前部说明 CHM、缓存、标准输出及共享解析器来源；查询样例是固定检查，不另建一套生产模型默认值。 |

`server.mjs` 的 MCP 入口默认值集中在 `MCP_DEFAULTS`。需要改变输入文件或输出根目录时，优先修改 CLI/MCP 调用；留空输出目录时仍由执行器、环境变量和 `workspace.json` 解析实际运行位置，运行产物应保存在仓库外。唯一 run 目录、反馈路径和安装位置保留动态解析，不写入某次运行的绝对路径。固定协议字段和验收断言不是可随意修改的参数。

## 配置与几何验收

`setup` 不接受命令行选项。配置默认保存至 `%LOCALAPPDATA%\ansys-scdmdoc-mcp\config.json`。AWP 验证失败时页面显示 `无法根据 AWP 确定环境变量的路径，无法确定 SpaceClaim 的路径`，该错误页面只允许手动输入恢复。

```powershell
npm run verify:geometry
npm run verify:geometry -- --timeout 300000
npm run verify:geometry -- --help
```

`verify:geometry` 支持 `--out <directory>` 和 `--timeout <milliseconds>`；超时必须为 1 至 1800000 的整数，默认 300000。`--out` 应选择代码仓库之外的位置。它实际调用 `Pattern.CreateCircular`，从 0° 种子生成六个实体，测量 0/60/120/180/240/300°，再调用 `Combine.Merge` 并检查单一实体及非零面数。它还要求新鲜、非空模型和结果文件，不能仅凭进程退出码通过。

fixture 通过 `SCDM_RUNTIME_MODEL`、`SCDM_RUNTIME_RESULT` 和 `SCDM_RUNTIME_DIAGNOSTIC` 接收输出路径，由驱动脚本设置。不要直接用普通 Python 解释器执行它。

## 参数化建模 CLI

```powershell
npm run missile -- --list
npm run missile -- --help
npm run missile -- --preset params-preset.json --generate-only --json
npm run missile -- --k_nose 0.006 --t_fin 0.015 --timeout 300000 --json
```

| 选项 | 含义 |
| --- | --- |
| `--<field> <value>` | 覆盖 [`missile-schema.mjs`](../lib/missile-schema.mjs) 中声明的模型参数；字段名区分大小写。运行级输出字段使用下面的 `--out` 与 `--name`。 |
| `--preset <file.json>` | 读取参数对象，也接受此前 `params.json` 的 `params` 对象；显式开关优先于预设，预设优先于默认值。 |
| `--out <directory>` | 选择仓库外输出根目录，在其中创建唯一运行子目录。未指定时使用工作区配置的数据目录。 |
| `--name <file.scdoc>` | 模型文件名，默认 `missile.scdoc`；必须为不带目录的文件名。 |
| `--generate-only` | 生成 `params.json` 和 `model.py`，不启动 SpaceClaim；没有 ANSYS 安装也可使用。 |
| `--timeout <milliseconds>` | SpaceClaim 超时，默认 300000。 |
| `--json` | 以 JSON 输出构建结果。 |
| `--list` | 输出全部参数默认值、优化区间和几何字段后退出。 |
| `--help` / `-h` | 输出帮助后退出。 |

几何尺寸以米为单位，参数的完整默认值与允许值由 schema 和 `--list` 给出。目标脚本 API 版本从所选安装解析。CLI 没有批量扫参命令。

默认每次运行写入工作区 `data_lib/runs/high/<run-id>/spaceclaim/`，包括 `request.json` 和 `run.json`；建模路径还生成 `params.json`、`model.py`，执行时产生 `.scdoc`、`result.json` 和诊断，预览图属于可选产物。失败时尝试将运行移至 `feedback/spaceclaim/<run-id>/`，文件系统或权限错误可能中断移动及清单收尾。仅生成脚本的结果为 `script-generated`，清单为 `GENERATED`，不是 `runtime-verified`；模型执行需要新产物通过验收。历史文件不能代替本次验收。
