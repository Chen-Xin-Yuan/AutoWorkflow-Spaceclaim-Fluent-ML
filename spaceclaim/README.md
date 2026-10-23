# ansys-scdmdoc-mcp

**中文** | [English](README.en.md) | [中英双语速览](README.bilingual.md)

![ANSYS SpaceClaim MCP](docs/assets/circular-pattern-hero.png)

Windows 本地 MCP 服务：检索已安装的 ANSYS SpaceClaim/SCDM 官方 API CHM，返回 API 文档、Python 候选和建模步骤；同时提供参数化导弹几何的参数校验、脚本生成、无界面建模与产物验收。

运行目标为 Windows、Node.js 18+。MCP 使用 stdio，标准输出只承载 JSON-RPC。文档检索和 MCP 启动需要本机官方 CHM；实际建模还需要可运行的 SpaceClaim。独立命令行的仅生成模式可在没有 ANSYS 的机器上使用。

## 修改输入、输出和参数

自有源码在必要的 shebang、模块说明和 import 之后，先列出输入/输出与可调默认参数，再进入处理逻辑。入口的集中修改区为：`server.mjs` 的 `MCP_DEFAULTS`、`scripts/missile.mjs` 的 `DEFAULT_OPTIONS`、`scripts/setup.mjs` 的回环地址/端口/关闭延迟，以及 `scripts/verify-geometry.mjs` 的输出目录默认值、超时和文件名。仅转发或检查入口在文件前部说明实际配置来源；固定验收用例的几何数值与断言不作为生产参数修改。

几何参数仍统一在 `lib/missile-schema.mjs`，输入文件通过 CLI `--preset` 选择，输出通过 `--out`/`--name` 或 MCP `outputDir`/`fileName` 覆盖。优先级保持显式参数 > 预设 > schema 默认值。空输出目录仍交给执行器和 `workspace.json` 在运行时解析；运行编号、实际 run 目录和失败留存路径不能提前写成某次运行的固定绝对路径。安装仍通过 AWP 或受支持的本机配置发现。完整入口与产物说明见 [scripts/README.md](scripts/README.md)。

## 安装与首次配置

在本项目目录执行：

```powershell
npm ci
npm run setup
```

`npm run setup` 在 `127.0.0.1` 的随机端口启动浏览器配置页：

1. AWP 模式列出所有非空的 `AWP_ROOT<number>`，按版本降序供用户选择，并检查对应的官方 CHM。
2. 手动模式接受本机 `SpaceClaim.exe` 路径，查找其同级 `SpaceClaim.Api.V*` 目录中的 API CHM。

AWP 确认失败时，页面显示下面的原文，并在该错误页面禁用 AWP 选项，使用手动输入恢复：

```text
无法根据 AWP 确定环境变量的路径，无法确定 SpaceClaim 的路径
```

配置保存在 `%LOCALAPPDATA%\ansys-scdmdoc-mcp\config.json`。安装路径仅保留在本机配置中，不应写入源码、文档或提交记录。

配置完成后，由支持 MCP stdio 的客户端启动 `node server.mjs`；客户端工作目录设为本项目目录，也可使用 `server.mjs` 的绝对路径。直接运行此命令会等待客户端通过标准输入发送 JSON-RPC。

![MCP setup and search flow](docs/assets/setup-flow.svg)

## MCP 工具

工具注册与输入定义以 [server.mjs](server.mjs) 为准，当前共 7 个工具。

| 工具 | 输入与功能 |
| --- | --- |
| `search_api` | `query` 必填；可选 `kind`（`all/class/method/property/event`）与 `limit`（默认 25，最多 100）。检索 CHM 索引，返回相对文档路径等元数据。 |
| `get_doc` | `docPath` 为检索得到的相对路径；`maxChars` 默认 50000，最多 100000。读取页面正文、命名空间、程序集和签名，拒绝越出缓存根目录的路径。 |
| `find_python_command` | `intent` 必填；可选 `parameters`、`limit`（默认 5，最多 20）。匹配内置意图模板并关联 API 文档，返回 `inferred` 候选。 |
| `get_geometry_recipe` | `task` 必填，可选 `parameters`。拉伸任务拆为轮廓和拉伸两步，其余任务返回一步候选。 |
| `validate_missile_params` | 接受模型参数，返回 `ok`、错误、警告、自动修正、生效参数及来源、优化区间；不创建模型。 |
| `generate_model_script` | 模型参数，加可选 `outputDir`、`fileName`。写入 `params.json` 和 `model.py`，返回脚本内容及路径，状态为 `script-generated`。 |
| `build_missile_model` | 模型参数，加可选 `outputDir`、`fileName`、`timeoutMs`、`execute`。默认执行 SpaceClaim 并验收；默认超时 300000 ms，MCP 上限 1800000 ms。`execute:false` 只生成脚本，工具状态为 `GENERATED`。 |

模型参数由 [lib/missile-schema.mjs](lib/missile-schema.mjs) 统一定义。默认模型文件名为 `missile.scdoc`。MCP 的输出选项使用 `outputDir` / `fileName`，CLI 使用 `--out` / `--name`。

可以向 AI 描述“圆周阵列 6 个、覆盖 360°、最后合并”，再检查 `Pattern.CreateCircular`、`CircularPatternData` 和 `Combine.Merge` 对应的文档。内置候选模板覆盖圆周阵列、圆柱、拉伸、矩形、布尔差、球和平面；其中多种模板只是实现提示，需要继续编写脚本。未匹配的意图回退到关键词检索提示。当前没有任意图片自动复现、通用复杂任务规划或专门的共享拓扑生成器。

参数校验和脚本生成本身不需要执行 ANSYS，但 **MCP 服务在注册工具前就会解析并解包 CHM**，因此通过 MCP 使用这些工具仍需满足 CHM 启动条件。

## 参数化建模

`node scripts/missile.mjs` 可以直接使用已有的参数化建模程序生成模型，运行过程中不需要 AI 参与。命令行与 MCP 是两个调用入口，最终共用同一个 `buildMissile()` 函数：

```mermaid
flowchart TD
    A["终端执行<br/>node scripts/missile.mjs"] --> C["命令行入口<br/>scripts/missile.mjs"]
    B["AI应用通过MCP调用<br/>build_missile_model"] --> D["MCP入口<br/>server.mjs"]
    C --> E["共同的建模函数 buildMissile()<br/>lib/spaceclaim-runner.mjs"]
    D --> E
    E --> F["读取参数、计算几何<br/>根据模板生成 params.json 和 model.py"]
    F --> G["SpaceClaim 执行 model.py"]
    G --> H["保存 .scdoc 模型和结果<br/>Node.js执行验收"]
```

命令行入口位于 [scripts/missile.mjs](scripts/missile.mjs)，MCP 工具入口位于 [server.mjs](server.mjs)；两者调用 [lib/spaceclaim-runner.mjs](lib/spaceclaim-runner.mjs) 中的建模流程。[lib/missile-template.mjs](lib/missile-template.mjs) 根据预先编写的模板生成 `model.py`。生成的脚本读取 `params.json`，在 SpaceClaim 内调用建模 API；每次建模不需要让大语言模型重新编写脚本。

AI 可以理解自然语言、选择参数和安排工具调用；普通程序也可以作为 MCP 客户端调用这些工具。现有建模算法只覆盖已实现的参数化几何，新增不同的几何结构需要扩展代码。上图展示实际执行的流程；使用 CLI `--generate-only` 或 MCP `execute:false` 时，流程停在参数文件和脚本生成阶段，不启动 SpaceClaim，也不产生已建好的模型。

所有长度使用米，半径系数无量纲。输入优先级为显式参数 > 预设 JSON > schema 默认值；预设可为参数对象，也可为此前生成的 `params.json`。CLI `--list` 输出全部默认值、几何字段和优化区间。

```powershell
node scripts/missile.mjs --list
node scripts/missile.mjs --help
node scripts/missile.mjs --preset ..\data_lib\inputs\missile_params_example.json --generate-only
node scripts/missile.mjs --k_nose 0.006 --name missile.scdoc --timeout 300000 --json
```

预设文件示例属于工作区数据目录；使用独立检出时需自行准备 JSON 文件。需要另选输出位置时，使用仓库外目录，例如 `--out ..\data_lib\runs`；程序会在其中新建唯一运行子目录。

| 几何参数 | 含义 | 默认值 |
| --- | --- | --- |
| `L_N` / `L` / `D` | 鼻锥长度 / 总长 / 直径 | 0.49 / 3.69 / 0.18 m |
| `L_W` / `C_RW` / `C_TW` / `B_W` | 前舵可见前缘站位 / 根弦 / 梢弦 / 展长 | 1.72 / 0.23 / 0.06 / 0.23 m |
| `L_F` / `C_RF` / `C_TF` / `B_F` | 后舵可见前缘站位 / 根弦 / 梢弦 / 展长 | 3.2 / 0.38 / 0.19 / 0.22 m |
| `t_fin` | 舵面厚度 | 0.012 m |
| `k_nose` | 锥柱交接倒圆半径 / `L` | 0.005 |
| `k_tip` | 弹尖球面钝化半径 / `L` | 0.002 |

| 约定与区域参数 | 默认值与支持范围 |
| --- | --- |
| `fin_phase` | `+`；也支持 `x`。 |
| `root_overlap` | 0.005 m；必须为正且小于最小展长。 |
| `le_mode` | 当前只实现 `trailing_aligned`；其余声明的模式会被拒绝。 |
| `fillet_location` | `both`；也支持 `cone_cylinder`、`nose_tip`。 |
| `cruciform_count` | 每级 4 片；接受大于等于 2 的偶数。 |
| `scdm_api_version` | 默认空字符串；执行器自动检测目标 API 版本，生成脚本使用检测结果。 |
| `enclosure_shape` | 只支持 `cylinder`。 |
| `enclosure_radius` / `enclosure_front` / `enclosure_back` | 2.7 / 3.69 / 16.45 m。 |
| `boi_radius` / `boi_front` / `boi_back` | 0.45 / 0.5 / 2.5 m。 |
| `model_placement` / `model_offset` | `ensemble` / -4.0 m；定位模式还支持 `missile`、`offset`。 |
| `enclosure_name` / `boi_name` | `Enclosure` / `BOI`；分别为外壳组件名和 BOI 实体名。 |

优化区间仅用于警告，不限制合法几何的输入范围；小于 0.001 m 的非负梢弦会自动修正为 0.001 m。非法尺寸、前后舵重叠、区域不能包住模型或未实现的模式会产生校验错误，不会静默回退。

### 坐标、站位与区域

弹尖朝 -X，弹尾位于弹尖的 +X 方向。`L_W` / `L_F` 从最终弹尖量到弹体表面处的舵面可见前缘；根弦也在弹体表面定义。`root_overlap` 将舵根沿设计边延伸进弹体以便合并，不改变可见前缘和根弦。球面钝化会缩短鼻锥，但保持总长 `L` 和弹尖站位。

外壳固定，导弹和 BOI 一起沿 X 定位。弹尖站位为“定位规则 + `model_offset`”：

| `model_placement` | 定位规则 |
| --- | --- |
| `ensemble` | `(enclosure_back - enclosure_front)/2 - (boi_back - boi_front)/2`，使导弹与 BOI 整体居中。 |
| `missile` | `(enclosure_back - enclosure_front)/2`，使导弹本体居中。 |
| `offset` | `0`，直接使用 `model_offset`。 |

负偏移向弹尖方向移动。按当前默认值计算，弹尖站位为 1.38 m，弹尾为 5.07 m，外壳轴向区间为 `[-3.69, 20.14]` m；这些是解析值，不能替代本次运行的实测验收。

生成目标为同一个 `.scdoc` 中的三个实体：合并后的 `Missile`、外壳和 BOI。外壳调用官方 `Enclosure.Create`，沿 X 轴设置圆柱、`Subtract=False`、`CreateShareTopology=False`，通过包围盒和缓冲量换算目标尺寸；组件名使用 `enclosure_name`。BOI 为独立旋转实体。外壳保留完整实体，不自动扣除导弹或建立共享拓扑；这里生成几何和区域，不执行网格划分或 CFD 求解。

### 命名选择与结构清理

| 命名选择 | 几何内容 |
| --- | --- |
| `inlet` / `wall` / `outlet` | 外壳低 X 端盖 / 圆柱侧面 / 高 X 端盖。 |
| `missile_nose_tip` | 存在球面钝化时的弹尖面；尖头时不创建该组。 |
| `missile_nose` | 鼻锥锥面。 |
| `missile_body` | 柱面、锥柱交接圆角及弹底。 |
| `missile_fin` | 前后全部舵面。 |

默认创建 7 组；数量和面数随实际几何变化。脚本核对导弹分组的面数及面积总和，检查外壳端盖与侧面面积。阵列合并后只删除确认不含实体的残留组件；保留模型、BOI、外壳实体及其组件，其余枚举到的结构对象调用物理抑制。抑制验收检查调用结果与保留集合，当前实现不回读抑制状态。

## 安装发现、缓存与运行记录

CHM 按以下顺序查找，候选不存在时继续尝试：

1. `SCDM_CHM`；
2. 首次配置保存的 AWP 选择或手动可执行文件路径；
3. 所有非空 `AWP_ROOT<number>`，按版本降序；
4. 通用 `AWP_ROOT`：优先尝试 `SCDM_API_VERSION`，再扫描该安装中的 API 版本目录。

SpaceClaim 可执行文件单独按 `SCDM_EXECUTABLE`、手动配置、降序 AWP 变量、通用 `AWP_ROOT` 查找。API 版本优先取可执行文件的安装目录版本，其次取解析到的 CHM 版本，最后扫描同级 API 目录；当前运行器不会用模型参数 `scdm_api_version` 强制覆盖检测结果。

CHM 使用 Windows `hh.exe` 解包，并按源文件路径、大小和修改时间缓存提取结果及索引。默认缓存为 `%LOCALAPPDATA%\ansys-scdmdoc-mcp\cache`（无 `LOCALAPPDATA` 时依次回退到 `TEMP` 和当前目录，应配置到仓库外）。不读取本地 Python 知识库，也不随仓库分发 ANSYS 文档。

建模与圆周验收根据项目父目录的 `workspace.json` 解析工作区数据路径；缺失该配置时使用同级默认 `data_lib` / `feedback`。默认运行位置是 `data_lib/runs/<runId>/spaceclaim`；失败时尝试将产物移至 `feedback/spaceclaim/<runId>`，文件系统或权限错误可能中断移动及清单收尾。`--out`、MCP `outputDir` 或 `SCDM_MISSILE_OUTPUT` 可覆盖建模输出根目录，应始终指定仓库外位置。

每次运行写入 `request.json` 和 `run.json`，清单记录状态、验证结果、产物大小及 SHA-256。参数有效后写入 `params.json`、ASCII 源码 `model.py`；实际执行还会尝试生成模型、`result.json`、诊断文本和预览图。预览是否生成不作为几何验收依据。

仅生成时 `run.json` 为 `GENERATED` 且 `verification.runtimeVerified=false`，不产生已经建好的模型。成功建模的运行清单为 `PASSED`，MCP 建模返回 `PASS`；失败且完成收尾后清单为 `FAILED`。旧记录和历史方案仅作证据快照。

## 验证与证据边界

- `official-doc`：官方 CHM 中的 API 文档事实。
- `inferred`：由 C#/.NET 签名、内置模板和建模意图推导的 Python 候选。
- `runtime-verified`：本次在目标 SpaceClaim 执行后，新生成产物通过验收。

建模验收检查新鲜非空模型和结果、实体数量、尺寸、旋转体体积及面面积、轴向质心、舵面站位、外壳与 BOI 的尺寸/体积/定位、命名选择覆盖、组件清理与物理抑制调用结果。全部检查通过并且进程正常结束，才报告运行验证成功；检查项数量随几何条件变化。

普通开发检查：

```powershell
npm ci
npm test
npm run smoke
```

`npm test` 主要检查解析、路径、模板、参数和验收逻辑；存在 CHM/AWP 环境变量时，协议测试还会连接真实 CHM 并验证仅生成模式。完整 CHM 解包与缓存测试需显式设置 `SCDM_RUN_REAL_CHM_TEST=1` 后运行 `npm test`。它们不执行真实几何建模。

本机文档与协议检查需要已安装的官方 CHM：

```powershell
npm run smoke
npm run verify:real
```

`smoke` 检查 7 个工具、检索、页面读取、路径拒绝、候选与参数校验；其启动预检查要求 `SCDM_CHM`、版本化 AWP 或通用 `AWP_ROOT`，仅有已保存的 setup 配置还不够。`verify:real` 检查 CHM 解包、索引和查询，可使用 setup 配置。两者都不证明几何执行成功。

需要真实几何验证时，手动运行：

```powershell
npm run verify:geometry
npm run verify:geometry -- --timeout 300000
```

该命令启动目标 SpaceClaim，运行 [圆周阵列夹具](scripts/spaceclaim-circular-repeat-smoke.py)：

```text
0° 基体 → Pattern.CreateCircular(CircularPatternData, 6 个, 360°)
→ 检查 0/60/120/180/240/300° → Combine.Merge → 1 个合并实体
```

结果保存在新运行目录的 `result.txt`，结构化键值必须包含 `status=PASS`、阵列体数、角度、步长、覆盖度、合并体数和非零面数；同时检查新模型 `circular.scdoc` 和新结果的时间、大小及进程状态。成功查询、缓存存在、旧模型或退出码 0 都不能单独证明几何正确。本 README 不声明此次文档修改已完成真实 ANSYS 验证。

## 目录与维护

| 直接子目录 | 用途 |
| --- | --- |
| [.github/](.github/README.md) | GitHub Actions 配置；`workflows/` 运行 Windows 单元测试。 |
| `.vscode/` | 本机编辑器配置；当前本机设置不纳入版本控制，不保存可分发的安装路径。 |
| [docs/](docs/README.md) | 说明素材 `assets/` 和历史设计 `superpowers/`。 |
| [lib/](lib/README.md) | CHM、索引、参数、几何、脚本模板、执行器和运行记录模块。 |
| `node_modules/` | npm 生成的第三方依赖；不纳入版本控制，不手工编辑其文件或 README。 |
| [scripts/](scripts/README.md) | 首次配置、CLI、文档检查和真实几何验收入口。 |
| [tests/](tests/README.md) | Node.js 测试；真实 CHM 测试的启用条件见上文。 |

入口为 `server.mjs`，命令与依赖版本见 `package.json` / `package-lock.json`。工作区总览见 [../README.md](../README.md)；历史建模设计见 [计划目录](docs/superpowers/plans/README.md)。

[AGENTS.md](AGENTS.md) 要求每次修改代码都在同一变更中同步更新相关 README；共同内容须保持中、英、双语一致，目录变动须同步更新父目录说明。依赖自身的 README 不属于本项目维护范围。

## 发布边界

仓库只发布实现、测试和说明；不要提交 CHM、解包的 HTML/HHC/HHK、缓存、运行日志、模型、本机配置、凭据或安装路径。推送、发布或修改外部 GitHub 状态须获得当轮明确授权。

代码采用 [MIT License](LICENSE)。ANSYS、SpaceClaim、SCDM 及相关名称属于各自权利人，本项目不代表 ANSYS 官方。
